# Swappable agent harnesses

Moyai uses [LiteLLM's harness API](https://docs.litellm.ai/docs/harness) for
all five supported runtimes, with a separate native Hermes adapter. LiteLLM does not currently
include Hermes in its `Harness` enum.

## Architecture

- `sandbox/harness_registry.py` is the single catalog used by the sandbox,
  API validation, UI choices/model filtering and Slack selection.
- `sandbox/agent.py` owns shared workspace preparation, prompts, activity,
  steering, waits, checkpointing and final delivery. It calls a registry factory,
  not a Hermes/Claude conditional.
- `sandbox/harness_agent.py` defines the **HarnessAgent** abstract base class and
  typed **HarnessContext**. Both HermesAgent and LiteLLMAgent implement it.
  Each adapter implements `validate()`, `run_conversation(...)`, `interrupt()`
  and `close()`. Results carry completion/failure/interruption flags and saved
  transcript messages. Live-steering adapters additionally expose the Hermes
  redirect/steer contract; boundary-only adapters declare that capability false.
- `sandbox/hermes_harness.py` encapsulates Hermes imports, discovery and callbacks.
- `sandbox/litellm_harness.py` calls **`litellm.aagent_session()`**, selects the
  registered **`Harness` enum**, and consumes typed
  `Text`, `ToolCall`, `ToolResult`, `Approval` and final result events. Private
  `Reasoning` events never enter public activity.
- `sandbox/harness_bindings.py` contains named, typed runtime bindings rather than
  anonymous factory tuples and dynamic `__import__` lambdas.
  Runtime launch/MCP configuration is separate from the shared LiteLLM
  event loop. The Claude binding uses a `LocalSandbox` subclass inside the
  existing isolated Modal sandbox. LiteLLM owns CLI execution and event parsing.

To add another runtime, register its `HarnessDefinition` and verified runtime
binding (binary, options, workspace tools and model protocol). No edits to
`agent.py`, API schema or model picker are needed. The beta LiteLLM API does not
provide a common MCP configuration argument, so each CLI's MCP setup still needs
a small binding. All five current enum members now have bindings and live
verification. A coverage test detects additions to the pinned upstream enum.

## UI and Slack

New sessions have an **Agent harness** picker beside the model picker:

- **Hermes** (default): existing runtime and all configured model choices.
- **Claude Code**: `Harness.CLAUDE_CODE`, Claude's native file/shell tools
  and Moyai MCP tools; requires an allowed `anthropic/claude-*` model.
- **Codex**: `Harness.CODEX`, requires an allowed `openai/*` model.
  This is the Codex runtime, not the separate OpenAI Agents SDK.
- **OpenCode**: `Harness.OPENCODE`, native tools and configured Moyai MCP server.
- **Deep Agents**: `Harness.DEEPAGENTS`, LangChain runtime with LiteLLM's sandbox
  backend and `workspace_tools`/`workspace_call` access to the authorized MCP catalog.
- **Tool Loop**: `Harness.TOOL_LOOP`, explicit workspace file/shell tools
  plus the same authorized MCP catalog wrappers.

The persisted ID `claude-agent-sdk` remains stable for compatibility. New Slack
threads can begin with:

```text
@Moyai Devin harness claude-agent-sdk
Your task here
```

Other IDs are `codex`, `opencode`, `deepagents`, `tool-loop`, and `hermes`.

The task line is optional; a harness-only command starts no compute. Follow-ups,
mirrored Slack conversations, delegated workers and UI side chats retain the
selection. Existing sessions migrate to Hermes. Harnesses are replaceable in
code and selectable for new sessions, **not hot-switched inside existing native
histories**. Start a new session to choose another runtime. Automations remain
Hermes-only in this v0.

## Dependencies and gateway boundary

The tested PyPI wheel `litellm==1.104.0` does not expose the documented beta API.
The runtime therefore uses the complete upstream Python package at immutable
revision `2cee61626d9581bc22bbdeefb1924f854f50d427`, with the wheel's dependencies.
This avoids compiling the upstream Rust extension solely to use the Python
harness API. `harness_dependencies.py` prepares it at image build time and for
older prepared snapshots. No upstream source is vendored into Moyai. The pinned
`claude-agent-sdk==0.2.163` supplies the Claude binary; Moyai no longer directly
uses `ClaudeSDKClient`. MCP stays on 1.x for Hermes compatibility.
Additional pins: `@openai/codex@0.160.1`, `opencode-ai@1.18.35`,
`deepagents==0.7.22`, `langchain-litellm==0.11.0`. The image installs all runtimes;
older snapshots install missing dependencies before starting the selected harness.

LiteLLM receives `model='litellm_proxy/' + selected_model`, the loopback broker
URL and only the short-lived run capability. Its local endpoint forwards to
Moyai's authenticated relay. The relay seals and forwards each native route:
`/v1/messages` stays Messages, `/v1/responses` stays Responses. **LiteLLM AI Gateway
owns protocol unification**. No local Messages/Responses conversion or synthetic
SSE generation remains. OpenCode and the in-process harnesses use Chat Completions.
`app/harness_gateway.py` handles only run authorization, model pinning, context,
attachments, limits and accounting; native response bytes stream unchanged.
Native model trace spans record usage/status only, avoiding reasoning or tool
payload capture; public tool traces remain separate. Provider keys stay server-side.
There is no silent fallback to Hermes.

Only the explicitly authorized MCP configuration is loaded. The server is named
`moyai` because Claude reserves `workspace`. The Claude launch binding applies a
finite native-tool allowlist and noninteractive `dontAsk` permissions; connected
app authorization remains enforced by the broker. Private memory and credential
tool payloads remain scrubbed from public activity and traces.

## Continuation and limitations

Each app turn creates a fresh LiteLLM session using saved transcript/tool receipts.
**TurnJournal** records typed runtime tool events and final text; it no longer
extracts history from inference requests. This keeps lifecycle code independent
of Messages, Responses and Chat Completions schemas. It does not reuse
another runtime's native resume ID. Steering, credential/delegation waits
and machine renewal are handled at complete tool-round boundaries. Claude does
not redirect an in-flight inference like Hermes. Stop still revokes the capability
and terminates the sandbox. Native content blocks pass through to the gateway;
provider feature support belongs to the gateway/runtime, not a Moyai translator.
Codex nested OS sandboxing is disabled only because it runs within Moyai's
isolated Modal machine, not on the web host. Python harness tools execute in that
same isolated machine. Tool Loop file tools reject paths outside the workspace;
shell subprocesses filter provider secret environment variables. Provider-native
server-side `previous_response_id` is rejected; saved full input is used instead.

## Verification

`python -m pytest tests/test_harnesses.py` covers selection, registry extension,
model restrictions, idempotency, Slack routing, entrypoint dispatch, checkpoint
receipts and privacy. `node --test tests/test_harness_picker.cjs` covers the picker.
`tests/test_harness_gateway.py` asserts native request schemas and byte-identical
stream/nonstream responses, model pinning, revocation, and native usage accounting.

Opt-in real inference: make the pinned source importable (for example through
`PYTHONPATH` pointing to a checkout at the exact revision), install the pinned
runtime dependencies and run `python -m scripts.harness_smoke` with
`GATEWAY_BASE_URL` and securely injected `GATEWAY_API_KEY`. `SMOKE_HARNESS` selects
the runtime and `SMOKE_MODEL` selects a compatible model. This makes billed calls
against an isolated local database: create Python code, read and execute it,
call a read-only workspace MCP tool, and complete a follow-up using saved context.
Never put keys in checked-in files.

Verified locally with real gateway inference:

| Harness | Model | Code execution | Workspace tool | Follow-up |
| --- | --- | --- | --- | --- |
| Claude Code | anthropic/claude-sonnet-4-5 | PASS | PASS | PASS |
| Codex | openai/gpt-5.4 | PASS | PASS | PASS |
| OpenCode | anthropic/claude-sonnet-4-5 | PASS | PASS | PASS |
| Deep Agents | anthropic/claude-sonnet-4-5 | PASS | PASS | PASS |
| Tool Loop | anthropic/claude-sonnet-4-5 | PASS | PASS | PASS |

Each created program printed `harness-live-ok`. These smoke tests do not prove
every provider/model combination or all long-running recovery scenarios.

Production Modal image builds and hosted Slack delivery require a subsequent
deployment. This change neither deploys nor alters the active agent.
