# Readable session names

The sidebar shows a task name, visible status, last activity time, and real
repository context. Titles update through existing polling without replacing
chat drafts. Search matches both the displayed name and original request.
Explicit agent labels take precedence over generated names; child agents keep
their hierarchy. The original prompt and transcript are never rewritten.

## Background title agent

`app/session_titles.py` uses the OpenAI Agents SDK with an explicit
`OpenAIChatCompletionsModel`, pointed to `LITELLM_API_BASE` and
`LITELLM_API_KEY`. Its default model is `openai/gpt-4.1-nano`, independent
of the main chat model. Administrators can change the exact gateway model ID
under **Settings → Session title model**. The saved workspace-wide value
survives restarts and overrides `SESSION_TITLE_MODEL`. Changes apply to new
title attempts without restarting; existing titles and attempts are preserved.
The setting never changes gateway credentials or the main conversation model.

The agent has no tools or handoffs. SDK tracing is disabled, requests do not
stream, and both SDK and HTTP retries are disabled. The first 4,000 characters
of the first saved user message are sent to the configured gateway. No tool
outputs or other transcript messages are sent. Treat that message as untrusted
source material. Validated output is stored in `runs.display_title`; the
browser escapes it as text. Existing labels win races with generation.

Configuration defaults:

| Variable | Default |
| --- | --- |
| `SESSION_TITLES_ENABLED` | `true` |
| `SESSION_TITLE_MODEL` | `openai/gpt-4.1-nano` |
| `SESSION_TITLE_TIMEOUT_SECONDS` | `8` |
| `SESSION_TITLE_CONCURRENCY` | `2` |
| `SESSION_TITLE_BACKFILL_LIMIT` | `50` |

There is one bounded startup backfill, at most 128 queued IDs, a 96-token
output limit, and an 80-character title limit. Web and Slack admissions enqueue
work without waiting for inference; GET endpoints never trigger it. A durable
attempt marker prevents repeated charges, even after failures or restart.
Queue overflow leaves a session eligible for a later startup or admission.
Failures retain the cleaned prompt fallback. Shutdown cancels work and closes
the client. Missing gateway configuration makes the service a no-op.

OpenAI Agents SDK 0.17.3 and OpenAI 2.26.0 are pinned together: tests exercise
the real SDK through a mocked HTTP transport to detect usage-schema changes.

## Verification

- Python tests cover actual SDK request construction, persistence, unchanged
  history, gateway errors/timeouts, migration, backfill, shutdown, web and Slack.
- CJS tests cover safe titles, original-prompt search, hierarchy and live refresh.
- The browser demo saved `openai/gpt-4.1-nano` in Settings and created a local
  demo chat. A foreground process ran the actual title service against the
  authorized LiteLLM gateway, using that saved model and the same database.
  Gateway credentials were injected only into that process, not the browser
  server. The main chat reply was simulated; the title inference was real.
- Input: “Can you help us fix the sidebar so that chat sessions have short
  readable task names?” Live output: “Improve sidebar task name display”.
  Browser polling displayed that title in the sidebar and header without
  changing the original request.
- Live testing exposed an HTTP 400 when tool-choice options were sent without
  tools. Those options are now omitted; the agent still has no tools/handoffs.
- The original `fireworks_ai/deepseek-v4-pro` remains selectable by exact ID
  on gateways that serve it. No fallback model is silently substituted.

For live verification with a gateway enabling the requested model, supply
`LITELLM_API_BASE` and `LITELLM_API_KEY` securely and run
`uv run python -m scripts.session_title_smoke`. This uses synthetic input and a
separate temporary database. It exits nonzero if no valid title is returned.

Deployment is separate from PR publication. Confirm the configured gateway
serves the title model before enabling generation in production.
