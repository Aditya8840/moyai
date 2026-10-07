# Testing and historical verification

[Documentation](README.md) · [Project overview](../README.md)

> This guide retains the detailed reference material from the original README.
> Dated acceptance reports describe past checks, not a current deployment or test result.

## Verification

```sh
uv run pytest -q
node --check app/static/app.js
uv run python -m compileall -q app sandbox
```

The automated suite uses isolated temporary databases and mocked external services. It tests demo completion and SSE replay, cancellation, CSRF/host/session boundaries, repository URL validation, encrypted credentials, per-run tool scope, direct tool execution without approval, uncertain writes, OAuth state binding/replay rejection, restart recovery, model-proxy restrictions, sandbox cleanup during provisioning and shutdown, admin/member restrictions, connection-policy revocation, Slack signature freshness and event deduplication, and bot/user token rotation.

Manual browser QA covers creating a task, streaming and saved activity, stopping a task, connection dialogs, runtime readiness, and responsive layout. The optional browser WebMCP tools expose listing tasks and starting explicit demos; they do not enable unattended cloud runs.

Historical cloud checks are retained under [Deployment and acceptance history](#deployment-and-acceptance-history). For future releases, use this acceptance checklist with configured accounts:

1. Submit a small task against a public test repository in Modal mode; confirm the image builds, Hermes invokes a terminal tool, and results stream back.
2. Confirm the model gateway records the configured model/key and budget.
3. Connect each provider and run one search/read. Test live writes only with an explicitly authorized disposable destination; direct execution, policy denial and ambiguous-write handling are covered by the automated suite, but real provider writes remain unverified.
4. Open a public page with the agent browser and download its screenshot.
5. Stop a real task during provisioning and while running; verify Modal shows no remaining sandbox after cleanup/timeout.

## Deployment and acceptance history

**Goal mode:** start a cloud chat request with `/goal <objective>` to keep working across normal response boundaries until evidence-backed completion. Use `/goal status`, `/goal pause`, `/goal resume`, or `/goal clear`; Stop, blockers, and safety limits still apply. See [goal mode and implementation research](goals.md).

**Cloud workspace:** [Open Moyai Devin](https://moyai-devin-litellm.onrender.com), hosted in **Organization for Litellm → Litellm** on Render; Hermes sandboxes and filesystem snapshots run under **hermes-workspace** in the **litellm** Modal workspace. Sign in with your **@berri.ai Google Workspace account**. Shared-password login is disabled. Secrets remain private in Render and ignored local environment files. Historical links below use the old origin; their session IDs are preserved on the new origin.

**Initial Slack verification:** a real @Moyai Devin mention in [#bot-spam](https://berriaillm.slack.com/archives/C0B302ZJU05/p1790720764864289) created exactly one cloud run and returned a protected link on the original Modal deployment. The agent answered `Ready`, used no connected-app tools, and its sandbox was terminated. The chat continuation path is described in [Using Moyai](usage.md#chat-interface).

**Chat verification:** real session [`e391ff74341d464a9674810584eae976`](https://moyai-devin.onrender.com/#run=e391ff74341d464a9674810584eae976) completed three replies. A follow-up queued during the first response remembered a phrase and changed the same file from 7 to 12. A fresh deployment preserved the four-message transcript, snapshot, and latest file archive. A third message sent from the reopened browser chat recalled the phrase and read the file as 12. All six chat messages are saved, no connected-app tools or approvals were used, and no sandbox was left running.

**Current verification:** the hosted cloud path works with `openai/gpt-6-astra` through `https://gateway.litellm-sandbox.ai/v1`. Live acceptance run `15c6ceef68724dc6897dce5639ef9350` created Python files, ran four unit tests successfully, opened and read a page through the real Chromium MCP tool, returned a downloadable archive and screenshot, streamed 20 activity events without decoding errors, and confirmed sandbox termination. The configured key's model catalog also includes `anthropic/claude-opus-5-5`; Astra is the current default.

Local/cloud sign-in, protected APIs, demo tasks, Docker startup/restart, and cloud history restoration after redeployment passed. The 142 automated tests cover approvals, OAuth state, model restrictions, answer preservation, checkpoint recovery, activity framing, cancellation, recovery archives, and Slack conversation/reaction routing using simulated providers. A real provisioning-cancellation check confirmed task cancellation, token revocation, and sandbox exit. Demo events are explicitly labeled and never execute agent code.

**Live app setup:** all three dedicated integrations are connected: Linear with Read, Create comments, and Create issues for LiteLLM-prod only (Linear’s Create issues scope also allows issue updates); Slack OAuth for BerriAI with search, channel/DM history, and posting scopes, with token rotation enabled; and Notion OAuth for LiteLLM with Read and Insert content, without editing existing content or user-profile access. At the user's request, Notion was granted every available page under Teamspaces, Shared, and Private, including their children. Notion only offers pages where the authorizing user has Full Access; this does not grant access to other Notion workspaces or guarantee access to future top-level pages.

Live integration acceptance run `16f17267a9f749cb8063a5aa5d38d76c` used real Hermes with Astra in a Modal sandbox. All six native calls succeeded: search and read for each of Linear, Slack, and Notion. The run created a downloadable report, requested no writes or approvals, and finished with zero active sandboxes. A final redeployment preserved all three connections, both completed acceptance runs, and their downloadable artifacts; unauthenticated APIs still returned 401. See integration verification (`../hermes-integration-verification.md`; historical artifact not included in this repository) and result archive (`../hermes-integration-test.zip`; historical artifact not included in this repository). Search results are paginated and large tool outputs can be truncated; this verifies connectivity and selected targets, not exhaustive search coverage or complete Notion content retrieval. External writes have not been exercised against real accounts.

A separate deterministic runtime check also passed: the real Hermes conversation loop consumed streamed tool calls, invoked Chromium through the actual workspace MCP bridge, returned a result, and cleaned up its sandbox. That check used a test model fixture, not an AI provider.

## Recovery from code-content connection failures

A production failure on LIT-6275 exposed an edge-firewall false positive: the first inference and Linear read succeeded, but the saved conversation containing the issue’s code/reproduction examples received Cloudflare HTML `403 Blocked` before reaching the Render app. Every follow-up restored that same context and failed again. An isolated copy of the actual snapshot reproduced this: plain JSON returned the expected capability `401`, while the saved conversation returned `403`.

The sandbox now runs an authenticated loopback adapter for Hermes and MCP. Requests cross the public edge as Fernet envelopes bound to a fresh run capability and exact route, with a five-minute validity window. Render authenticates the active run **before** decrypting and keeps its model allowlist, sender accounting, input validation, size limits and tool policies. No gateway credentials enter the sandbox, no extra gateway key is created, and no firewall setting is changed. Restored sessions receive the updated adapter files, preserving user files and history. The exact previously blocked conversation passed the edge after this transport change.

Slack context is included once instead of being appended again on every follow-up. Failed assistant turns retain their failure status and appear as failures in Slack and the web UI. Connection failures give an actionable explanation instead of guessing about the user’s model API key. For issue follow-ups, the agent checks for an existing fix PR and uses the shared GitHub App to create a normal PR directly when enabled.

**Verification:** 124 automated tests passed, including encrypted model/tool requests, unchanged gateway costs and identity attribution, tampered/expired/wrong-route rejection, revoked capability rejection, and continued admin approval requirements for writes. Commit `046c317` was deployed to Render. The actual failed conversation resumed from its saved snapshot, re-read the Linear issue, and identified merged PR [#38416](https://github.com/BerriAI/litellm/pull/38416), shipped in v1.100.0. Its answer arrived in the original Slack thread. A subsequent Slack reply without an issue identifier received the correct contextual answer in the same web/Slack session. The spend dashboard attributed the repaired calls to the sender’s linked Google identity. Both turns saved their snapshots and stopped their sandboxes; a Modal SDK check found zero active sandboxes under `hermes-workspace` after verification.
