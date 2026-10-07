# Moyai Devin

Use Moyai Devin to delegate coding tasks from your browser or Slack. The agent edits code and runs tests in an isolated cloud workspace, and can open a pull request for your review. You can send corrections during a task or resume it with saved files and conversation history. Connect GitHub for repository access; add Linear, Slack, or Notion for team context.

## Choose your model and harness

Bring your own model through a compatible API or LiteLLM gateway, and choose the agent harness for each new session: **Hermes, Claude Code, Codex, OpenCode, Deep Agents, or Tool Loop**. You can configure models beyond the built-in picker defaults with `AGENT_MODEL`.

Choose **Harness** beside **Model** before starting a session. Hermes is the default. Claude Code requires an enabled `anthropic/claude-*` model and a Messages endpoint; Codex requires an enabled `openai/*` model and a Responses endpoint. The other harnesses use Chat Completions with tool calling. Keep the same harness for a session, or start a new session to change it. See [harness compatibility and custom adapters](docs/harnesses.md).

## Getting started

You need a **Modal account** and a **model API key or gateway key** to run coding tasks. Follow these steps to host the web app and agent machines on Modal. Modal supplies the HTTPS address, so you can skip domain and proxy setup. The repository has no local agent runner; its local demo produces simulated responses.

### 1. Install and clone

You'll need **Git**, **Python 3.12+**, and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**. Commands below use a macOS/Linux shell (Windows users can use WSL).

```sh
git clone https://github.com/BerriAI/moyai-devin.git
cd moyai-devin
uv sync --frozen
cp .env.example .env
chmod 600 .env
```

Do not overwrite an existing `.env` when upgrading.

### 2. Connect Modal and a model

1. Create a [Modal account](https://modal.com/docs/guide) and select the workspace for this installation. Run `uv run modal token new` and complete the browser login. Open `~/.modal.toml` in a private editor window. Copy the selected workspace's `token_id` and `token_secret` into `.env` as `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`. Moyai requires these fields even after you log in through the CLI.
2. Get an API key from your model provider, or a dedicated key from your team's LiteLLM gateway. Edit these existing entries in `.env` (replace the example values):

   ```dotenv
   MODAL_TOKEN_ID=<token_id from your Modal profile>
   MODAL_TOKEN_SECRET=<token_secret from the same profile>
   LITELLM_API_BASE=https://your-gateway.example.com/v1
   LITELLM_API_KEY=<your dedicated gateway key>
   AGENT_MODEL=<exact model alias enabled for that key>
   ```

   For a first run with Hermes and OpenAI, [create an API key and enable API billing](https://platform.openai.com/docs/quickstart). Set `LITELLM_API_BASE=https://api.openai.com/v1`, put the key in `LITELLM_API_KEY`, and set `AGENT_MODEL=gpt-4.1` if your project has access. Keep the `LITELLM_*` variable names for this configuration. Your endpoint must support Chat Completions and tool calling. A ChatGPT subscription does not include API access. For other harnesses, use the [model and protocol requirements](docs/getting-started.md#choose-a-harness).
3. For code work, change `GITHUB_REPOSITORY` from the example's BerriAI repository to **your organization's** `owner/repository`. Leave optional Slack, Google SSO, tracing, and Temporal settings off for the first run. Set `SESSION_TITLES_ENABLED=false` to avoid requiring the separate default title model.

Keep `.env` and your Modal profile out of Git and chat. See the [setup walkthrough](docs/getting-started.md) for credential details and GitHub connection steps.

### 3. Deploy and sign in

```sh
uv run python deploy_modal.py
```

**Deployment starts a billed, always-on service.** Use a fresh Modal workspace for a new installation. The script uses fixed resource names; running it in an existing installation replaces the web app and interrupts active tasks.

Open the address printed as `Workspace URL: https://…`. Sign in with `WORKSPACE_PASSWORD` from your local `.env`; the script generates it if empty. It generates missing session/encryption secrets and sets the public HTTPS URL too. Keep `.env` for future deployments. Leave `PUBLIC_URL` at its example value for this deployment path.

### 4. Run a task and connect your repository

Open **Settings → Runtime** and check for **Cloud ready**. The badge confirms that you supplied the required settings; you still need to test the credentials. Start a new session with **Context & tools → Execution → Cloud session**, choose **Hermes** in the harness picker for this first check, select your configured model, leave the repository empty, and send:

> Use the terminal to create `/workspace/setup-check.txt` containing `moyai setup works`. Read it back with a tool and report the contents. Do not connect apps or publish anything.

Allow several minutes for the first agent image build. Check the tool calls in **Activity** and open `setup-check.txt` in **Files** to confirm its contents.

Open **Connections → GitHub → Connect**, register or connect an organization-owned GitHub App, and restrict its installation to your configured repository. Follow the [GitHub setup and first repository task](docs/getting-started.md#6-connect-your-github-repository). Add Slack, Linear, or Notion as needed.

For errors, see [setup troubleshooting](docs/getting-started.md#troubleshooting). To stop compute charges, stop the `moyai-devin` web app and any remaining agent sandboxes in the Modal dashboard. Closing your browser leaves them running.

## Local UI preview (not a working agent)

For UI development only, use a **separate checkout** with an unmodified `.env.example` copied to `.env`, then run:

```sh
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --workers 1
```

Open [localhost:8787](http://127.0.0.1:8787) to inspect the UI with simulated responses. The preview does not call a model, start an agent machine, or change a repository. Skip it unless you need to work on the UI.

## More information

- [Full installation walkthrough](docs/getting-started.md) · [All documentation](docs/README.md)
- [Using Moyai](docs/usage.md) · [Slack](docs/slack.md) · [Skills](docs/skills.md) · [Automations](docs/automations.md)
- [Deployment alternatives and operations](docs/deployment.md) · [App connections](docs/integrations.md) · [Security and limitations](docs/security-and-scope.md)
- [Architecture](docs/architecture.md) · [Testing](docs/verification.md) · [Costs](docs/costs.md) · [Tracing](docs/observability.md)

BerriAI teammates using the existing installation can [open the hosted workspace](https://moyai-devin-litellm.onrender.com) and sign in with an `@berri.ai` Google account instead of deploying another copy.
