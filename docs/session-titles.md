# Readable session names

The sidebar shows a task name, visible status, last activity time, and real
repository context. Titles update through existing polling without replacing
chat drafts. Search matches both the displayed name and original request.
Explicit agent labels take precedence over generated names; child agents keep
their hierarchy. The original prompt and transcript are never rewritten.

## Background title agent

`app/session_titles.py` uses the OpenAI Agents SDK with an explicit
`OpenAIChatCompletionsModel`, pointed to `LITELLM_API_BASE` and
`LITELLM_API_KEY`. Its default model is exactly
`fireworks_ai/deepseek-v4-pro`, independent of the main chat model.

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
| `SESSION_TITLE_MODEL` | `fireworks_ai/deepseek-v4-pro` |
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
- The local browser demo uses the real app in explicitly labeled demo mode;
  it verifies cleaned fallback names and searching the original request, not
  live generated title quality.
- Live verification against the authorized providers gateway could not obtain
  a title. Its authenticated model catalog did not list the requested model.
  The exact requested model is preserved; no substitute is silently selected.

For live verification with a gateway enabling the requested model, supply
`LITELLM_API_BASE` and `LITELLM_API_KEY` securely and run
`uv run python -m scripts.session_title_smoke`. This uses synthetic input and a
separate temporary database. It exits nonzero if no valid title is returned.

Deployment is separate from PR publication. Confirm the configured gateway
serves the title model before enabling generation in production.
