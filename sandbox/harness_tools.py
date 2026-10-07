"""Tool bindings for the in-process harnesses, still inside the Modal sandbox."""
import asyncio
import json
from pathlib import Path


def tools_for(cwd, config):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def workspace_tools() -> str:
        """List currently authorized workspace tool names, descriptions and input schemas."""
        return await invoke('', {})

    async def workspace_call(name: str, arguments_json: str) -> str:
        """Call one authorized workspace tool. Pass arguments as a JSON object string; discover its schema with workspace_tools first."""
        return await invoke(name, json.loads(arguments_json))

    async def invoke(name, arguments):
        server = config['mcp_servers']['workspace']
        params = StdioServerParameters(command=server['command'], args=server.get('args', []), env=server.get('env'))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                result = await client.call_tool(name, arguments) if name else await client.list_tools()
                if getattr(result, 'isError', False):
                    raise RuntimeError('Workspace tool failed; the action was not confirmed. Do not retry writes automatically.')
                return result.model_dump_json()

    def resolve(path):
        root = Path(cwd).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Path must stay inside the workspace')
        return target

    def read_file(path: str) -> str:
        """Read a UTF-8 file within the workspace, up to 100KB."""
        with resolve(path).open() as stream:
            return stream.read(100000)

    def write_file(path: str, content: str) -> str:
        """Write a UTF-8 file within the workspace."""
        target = resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return 'File written: ' + str(target)

    async def terminal(command: str) -> str:
        """Run a shell command in the isolated workspace, with a 120 second limit."""
        from litellm.harness.sandbox.local import filtered_environ
        proc = await asyncio.create_subprocess_exec('/bin/bash', '-lc', command, cwd=cwd, env=filtered_environ(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), 120)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 'Command timed out; not replayed.'
        return json.dumps({'exit_code': proc.returncode, 'output': output.decode(errors='replace')[:100000]})

    return [workspace_tools, workspace_call, read_file, write_file, terminal]
