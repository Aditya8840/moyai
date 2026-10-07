# Moyai Devin

Moyai Devin is a self-hosted AI coding agent for background engineering work. Ask it to fix a bug or build a feature from your browser or Slack. The agent works in an isolated cloud workspace, where it can edit code, run tests, and open a pull request for your review. Connect GitHub, Linear, Slack, or Notion to give it access to your code and team context. You can check its progress and send corrections while it works, or resume the task with your saved conversation and files.

## Getting started

**Start with a real agent, not the local demo.** Real execution requires a Modal account for the agent's machine and a paid model API or gateway. There is no local-only agent runner in this repository. The recommended setup below hosts both the web app and agent machines on Modal; you do not need Render, Docker, a domain, or an HTTPS tunnel.

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

1. Create a [Modal account](https://modal.com/docs/guide) and select the workspace that should own this installation. Run `uv run modal token new` and complete its browser authentication. Open the generated `~/.modal.toml` **privately in your editor**, and copy that workspace's `token_id` and `token_secret` into `.env` as `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`. CLI authentication alone is not enough: Moyai reads these fields from `.env`.
2. Get an API key from your model provider, or a dedicated key from your team's LiteLLM gateway. Edit these existing entries in `.env` (replace the example values):

   ```dotenv
   MODAL_TOKEN_ID=<token_id from your Modal profile>
   MODAL_TOKEN_SECRET=<token_secret from the same profile>
   LITELLM_API_BASE=https://your-gateway.example.com/v1
   LITELLM_API_KEY=<your dedicated gateway key>
   AGENT_MODEL=<exact model alias enabled for that key>
   ```

   **No gateway?** You can configure OpenAI directly: [create an API key and enable API billing](https://platform.openai.com/docs/quickstart), use `LITELLM_API_BASE=https://api.openai.com/v1`, put that key in `LITELLM_API_KEY`, and use `AGENT_MODEL=gpt-4.1` if your project has access to it. These environment variable names are retained even without LiteLLM. The endpoint must support Chat Completions and tool calling; a ChatGPT subscription is not an API credential.
3. For code work, change `GITHUB_REPOSITORY` from the example's BerriAI repository to **your organization's** `owner/repository`. Leave optional Slack, Google SSO, tracing, and Temporal settings off for the first run. Set `SESSION_TITLES_ENABLED=false` to avoid requiring the separate default title model.

Never commit `.env`, paste keys into chat, or share your Modal profile. **[The full setup walkthrough](docs/getting-started.md)** explains every required value, model selection, and how to connect GitHub.

### 3. Deploy and sign in

```sh
uv run python deploy_modal.py
```

**This creates a billed, always-on cloud service.** Use a fresh Modal workspace for a new installation: the script updates fixed app, secret, and volume names, so rerunning it in an existing installation redeploys that installation and interrupts active tasks.

The command prints `Workspace URL: https://…`. Open that actual URL and sign in using `WORKSPACE_PASSWORD`, which the script generates in your local `.env` if empty. It also generates the session/encryption secrets and configures the public HTTPS URL automatically. Keep `.env` safe for future deployments; do not invent a `PUBLIC_URL` or start localhost for this path.

### 4. Verify real execution, then connect your repository

Open **Settings → Runtime** and check for **Cloud ready**. This checks configuration only, not whether credentials work. Start a new session with **Context & tools → Execution → Cloud session**, select the exact model you configured, leave the repository empty, and send:

> Use the terminal to create `/workspace/setup-check.txt` containing `moyai setup works`. Read it back with a tool and report the contents. Do not connect apps or publish anything.

The first run builds the agent image and can take several minutes. Confirm actual tool calls in **Activity** and the file in **Files**. A simulated reply or a green Runtime badge is not a successful installation.

Next, open **Connections → GitHub → Connect**, register or connect an organization-owned GitHub App, and install it on only your configured repository. Follow the [GitHub setup and first repository task](docs/getting-started.md#6-connect-your-github-repository). Slack, Linear, and Notion are optional and can be added later.

**Stuck?** See [setup troubleshooting](docs/getting-started.md#troubleshooting). Stop the `moyai-devin` web app in the Modal dashboard when you no longer need it, and check for remaining agent sandboxes; closing the browser does not stop billing.

## Local UI preview (not a working agent)

For UI development only, use a **separate checkout** with an unmodified `.env.example` copied to `.env`, then run:

```sh
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --workers 1
```

Open [localhost:8787](http://127.0.0.1:8787). Responses are simulated: no model is called, no agent machine is started, and no repository changes are made. This is not a required setup step.

## More information

- [Full installation walkthrough](docs/getting-started.md) · [All documentation](docs/README.md)
- [Using Moyai](docs/usage.md) · [Slack](docs/slack.md) · [Skills](docs/skills.md) · [Automations](docs/automations.md)
- [Deployment alternatives and operations](docs/deployment.md) · [App connections](docs/integrations.md) · [Security and limitations](docs/security-and-scope.md)
- [Architecture](docs/architecture.md) · [Testing](docs/verification.md) · [Costs](docs/costs.md) · [Tracing](docs/observability.md)

BerriAI teammates using the existing installation can [open the hosted workspace](https://moyai-devin-litellm.onrender.com) and sign in with an `@berri.ai` Google account instead of deploying another copy.
