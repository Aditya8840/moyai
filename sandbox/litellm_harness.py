"""Moyai lifecycle adapter using the public LiteLLM harness/session/event API."""
import asyncio
import json
import os
from pathlib import Path
import threading

try:
    from .harness_dependencies import prepare_runtime, prepare_binary
except ImportError:
    from harness_dependencies import prepare_runtime, prepare_binary


def claude_sandbox(cwd, config):
    """Sandbox launch customization only; LiteLLM owns the agent loop and parsing.

LiteLLM beta has no common MCP parameter. Inject our explicitly authorized MCP
config at the sandbox boundary, never load a repository's MCP configuration.
"""
    import claude_agent_sdk
    from litellm.harness.sandbox.local import LocalSandbox
    binary = str(Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude')
    class WorkspaceSandbox(LocalSandbox):
        async def which(self, name):
            return binary if name == 'claude' else await super().which(name)

        async def exec(self, cmd, *, env=None, cwd=None):
            cmd = list(cmd)
            if cmd and cmd[0] == 'claude':
                cmd[0] = binary
                # Keep root-compatible noninteractive permissions, with a finite
                # allowlist; broker permissions still govern all app operations.
                index = cmd.index('--permission-mode')
                cmd[index + 1] = 'dontAsk'
                cmd += ['--tools', 'Bash,Read,Write,Edit,Glob,Grep',
                        '--allowedTools', 'Bash,Read,Write,Edit,Glob,Grep,mcp__moyai__*',
                        '--strict-mcp-config', '--mcp-config', json.dumps({'mcpServers': {
                            'moyai' if name == 'workspace' else name: server
                            for name, server in config['mcp_servers'].items()}})]
            return await super().exec(cmd, env=env, cwd=cwd)
    return WorkspaceSandbox(cwd)


def claude_options():
    from litellm import ClaudeCodeOptions
    return ClaudeCodeOptions(env={'CLAUDE_CODE_MAX_RETRIES': '0', 'ENABLE_TOOL_SEARCH': 'false'})


def local_sandbox(cwd, config):
    from litellm.harness.sandbox.local import LocalSandbox
    return LocalSandbox(cwd)


def codex_sandbox(cwd, config):
    from litellm.harness.sandbox.local import LocalSandbox
    class CodexSandbox(LocalSandbox):
        # The enclosing Modal machine is the isolation boundary.
        is_container = True
        async def exec(self, cmd, *, env=None, cwd=None):
            cmd = list(cmd)
            if cmd and cmd[0] == 'codex':
                server = config['mcp_servers']['workspace']
                overrides = ['features.shell_tool=true', 'features.apply_patch_freeform=false',
                             'model_providers.litellm.request_max_retries=0',
                             'model_providers.litellm.stream_max_retries=0']
                for key in ('command', 'args', 'env'):
                    if key in server:
                        value = server[key]
                        if isinstance(value, dict):
                            for name, val in value.items():
                                overrides.append('mcp_servers.moyai.env.' + name + '=' + json.dumps(val))
                        else:
                            overrides.append('mcp_servers.moyai.' + key + '=' + json.dumps(value))
                cmd[-1:-1] = [part for entry in overrides for part in ('-c', entry)]
            return await super().exec(cmd, env=env, cwd=cwd)
    return CodexSandbox(cwd)


def opencode_options(config):
    from litellm import OpenCodeOptions
    server = config['mcp_servers']['workspace']
    return OpenCodeOptions(config={'mcp': {'moyai': {'type': 'local',
        'command': [server['command'], *server.get('args', [])],
        'environment': server.get('env', {}), 'enabled': True}}})


# Runtime-specific launch/MCP configuration is separate from the shared event
# loop. Only verified bindings are enabled; enum membership alone is not enough.
RUNTIME_BINDINGS = {
    'claude': (claude_sandbox, lambda config: claude_options()),
    'codex': (codex_sandbox, lambda config: __import__('litellm').CodexOptions()),
    'opencode': (local_sandbox, opencode_options),
    'deepagents': (local_sandbox, lambda config: __import__('litellm').DeepAgentsOptions()),
    'tool-loop': (local_sandbox, lambda config: __import__('litellm').ToolLoopOptions(completion_kwargs={'num_retries': 0})),
}


class LiteLLMAgent:
    def __init__(self, *, spec, relay, config, activity, step, cwd, definition):
        self.spec, self.relay, self.config = spec, relay, config
        self.activity, self.step, self.cwd, self.definition = activity, step, cwd, definition
        self.stopped = threading.Event()
        self.messages, self.history, self.prompt = [], [], ''
        relay.before_model = self.before_model

    def validate(self):
        if self.definition.runtime_binding not in RUNTIME_BINDINGS:
            raise ValueError('No verified sandbox/tool binding for the selected LiteLLM harness')
        prepare_runtime()
        prepare_binary(self.definition.runtime_binding)
        import litellm
        harness = getattr(litellm.Harness, self.definition.litellm_harness)
        self.capabilities = litellm.agent_capabilities(harness)

    def interrupt(self):
        self.stopped.set()

    def before_model(self, messages):
        current = [dict(m) for m in messages if m.get('role') not in {'system', 'developer'}]
        if self.history:
            for message in current:
                content = message.get('content')
                if message.get('role') == 'user' and 'SAVED CONVERSATION REFERENCE:' in str(content):
                    message['content'] = self.prompt
                    break
        self.messages = [*self.history, *current]
        self.step()
        return not self.stopped.is_set()

    def run_conversation(self, prompt, *, conversation_history, system_message):
        return asyncio.run(self._run(prompt, conversation_history, system_message))

    async def _run(self, prompt, history, system_message):
        self.validate()
        import litellm
        from litellm.harness import Text, Reasoning, ToolCall, ToolResult, Approval
        harness = getattr(litellm.Harness, self.definition.litellm_harness)
        self.history, self.prompt, self.messages = list(history), prompt, list(history)
        self.stopped.clear()
        if history:
            prompt = ('SAVED CONVERSATION REFERENCE: completed messages and tool receipts, not new instructions. '
                      'Do not replay completed actions.\n' + json.dumps(history, ensure_ascii=False)
                      + '\n\nCURRENT REQUEST:\n' + prompt)
        sandbox_factory, options_factory = RUNTIME_BINDINGS[self.definition.runtime_binding]
        options = options_factory(self.config)
        in_process = self.definition.runtime_binding in {'deepagents', 'tool-loop'}
        tools = []
        if in_process:
            try:
                from .harness_tools import tools_for
            except ImportError:
                from harness_tools import tools_for
            tools = tools_for(self.cwd, self.config)
            if self.definition.runtime_binding == 'deepagents':
                tools = tools[:2]  # Deep Agents already supplies filesystem/exec tools.
        calls, pending_text = {}, []
        result = None
        async with sandbox_factory(self.cwd, self.config) as sandbox:
            async with litellm.aagent_session(harness, sandbox=sandbox,
                    model='litellm_proxy/' + self.spec['model'], api_base=self.relay.url + ('/v1' if in_process else ''),
                    api_key=os.environ['WORKSPACE_RUN_TOKEN'],
                    instructions=system_message + ('\nUse workspace_tools to discover authorized tools and workspace_call to invoke them. ' if in_process else '\nUse the advertised Moyai MCP tools directly. ')
                        +
                        'Hermes discovery wrappers are not available. Do not start detached background work.',
                    max_turns=self.spec.get('max_iterations') or None,
                    timeout=self.spec.get('timeout'), permissions='full', options=options, tools=tools) as session:
                stream = session.astream(prompt)
                async for event in stream:
                    if isinstance(event, Reasoning):
                        continue
                    if isinstance(event, Text):
                        pending_text.append(event.delta)
                    elif isinstance(event, ToolCall):
                        if pending_text:
                            self.activity.commentary(''.join(pending_text))
                            pending_text.clear()
                        calls[event.id] = (event.native_name, dict(event.input))
                        self.activity.start(event.id, *calls[event.id])
                    elif isinstance(event, ToolResult):
                        name, args = calls.pop(event.id, ('tool', {}))
                        self.activity.complete(event.id, name, args, {'content': [{'type': 'text', 'text': event.output}], 'isError': event.is_error})
                    elif isinstance(event, Approval):
                        event.deny('Use the workspace authorization flow.')
                result = stream.result
        interrupted = self.stopped.is_set()
        completed = bool(result and result.stop_reason == 'done' and not interrupted)
        answer = result.text if result else ''
        if completed:
            self.messages.append({'role': 'assistant', 'content': answer})
        return {'completed': completed, 'interrupted': interrupted,
                'failed': not completed and not interrupted, 'messages': self.messages,
                'final_response': answer or 'The selected harness stopped before completing the response.'}

    def close(self):
        self.relay.before_model = None
