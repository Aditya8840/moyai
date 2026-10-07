# Install Moyai and run your first real task

[Documentation](README.md) · [Project overview](../README.md)

This walkthrough is for a **new installation**. It deploys the browser app on Modal and uses Modal sandboxes for real agent work. The local UI demo is not part of this setup.

## What runs where

```text
Your browser → Moyai web app on Modal → isolated Modal agent sandbox
                        ↑                         |
                        └── model/tool requests ──┘
                        |
                        ├── your model API / LiteLLM gateway
                        └── GitHub and other apps you connect later
```

Your computer runs the installation commands; it does not need to stay on after deployment. The web app holds the encrypted connections and brokers access for agents. Modal provides compute, not model credits. You pay separately for Modal and model usage. This route needs no domain, reverse proxy, Render account, Docker, or tunnel.

**This is a shared, trusted-team workspace.** Do not expose it without authentication or invite untrusted users. Connected apps retain their authorizing identity's permissions; see [security boundaries](security-and-scope.md).

## 1. Prepare your computer

Install Git, Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/). These commands use a macOS/Linux shell; on Windows, use WSL.

```sh
git --version
uv --version
uv python install 3.12
git clone https://github.com/BerriAI/moyai-devin.git
cd moyai-devin
uv sync --frozen --python 3.12
cp .env.example .env
chmod 600 .env
```

Run subsequent commands from this repository directory. `uv sync` installs the Modal CLI and other project dependencies in `.venv`; there is no separate global Modal installation step. If you already have a `.env`, edit it rather than copying over it.

## 2. Get Modal credentials

1. [Create a Modal account](https://modal.com/docs/guide), choose the workspace that should own Moyai, and make sure its usage/billing policy permits the deployment. Prefer a fresh workspace: the deploy script uses fixed resource names and will update an existing Moyai installation in the same environment.
2. Authenticate from the repository:

   ```sh
   uv run modal token new
   ```

3. Complete the browser flow for the intended workspace. The CLI verifies the new credentials by default and saves them in `~/.modal.toml` (unless you have explicitly overridden Modal's configuration location).
4. Open that file **privately in a text editor**, not by printing it into a shared terminal or chat. Find the profile for that workspace. Copy `token_id` into the existing `MODAL_TOKEN_ID=` entry in `.env`, and `token_secret` into `MODAL_TOKEN_SECRET=`. Use both values from the same profile.

Moyai's deploy script requires these two `.env` fields even if the Modal CLI is already logged in. Tokens expire according to your Modal workspace's policy; plan rotation rather than copying an expiry date from someone else's deployment. For a team service, use your organization's managed service identity policy.

## 3. Configure a model endpoint

Choose **one** option. An API credential belongs in `.env` or the host's secret store, never in a task prompt.

### Option A: your existing LiteLLM gateway

Ask your gateway administrator for:

- Its OpenAI-compatible API base URL, usually `https://your-gateway.example.com/v1`.
- A dedicated API key with a budget and permission to use your chosen model.
- The **exact model alias** exposed to that key. Do not guess the alias from the provider's marketing name.

Edit these entries in `.env`:

```dotenv
LITELLM_API_BASE=https://your-gateway.example.com/v1
LITELLM_API_KEY=<dedicated gateway key>
AGENT_MODEL=<exact gateway model alias>
```

The app appends `/chat/completions` to the base URL. Do not put `/chat/completions` in the base itself. The gateway must be reachable **from Modal**, not just your laptop; `localhost:4000` refers to the cloud container after deployment. To operate your own gateway, use the [LiteLLM gateway setup guide](https://docs.litellm.ai/docs/proxy/quick_start) and host it at a protected reachable endpoint.

### Option B: OpenAI directly, without operating a gateway

Follow the [OpenAI API quickstart](https://platform.openai.com/docs/quickstart) to create a project API key and configure API billing. A ChatGPT subscription does not supply API access. Then set:

```dotenv
LITELLM_API_BASE=https://api.openai.com/v1
LITELLM_API_KEY=<your OpenAI project API key>
AGENT_MODEL=gpt-4.1
```

Use `gpt-4.1` only if enabled for your project, or another accessible Chat Completions model supporting tool calling and the agent's request parameters. The `LITELLM_*` names are still used for this direct endpoint. Use the provider's exact model ID, not a LiteLLM provider prefix. An Anthropic API key cannot be substituted into this example; route non-OpenAI APIs through a compatible gateway.

### Finish the first-run configuration

Set or add:

```dotenv
# Replace this with a repository belonging to YOUR GitHub organization.
GITHUB_REPOSITORY=your-org/your-repo
# Avoid requiring the separate default title model during initial setup.
SESSION_TITLES_ENABLED=false
```

Replace `your-org/your-repo` before connecting GitHub. GitHub is optional for the initial file-writing smoke test. For several repositories, use `GITHUB_REPOSITORIES=your-org/repo-a,your-org/repo-b`; this overrides `GITHUB_REPOSITORY` and all repositories must belong to one organization. Personal-account GitHub App installations are not supported by the current shared organization flow.

Leave Google OAuth, Slack, tracing, and Temporal disabled for now. You do not need their credentials to run an agent. The copied Google domain/admin examples are BerriAI-specific; replace them before enabling Google SSO for your own team.

For this **Modal-hosted web app** path, leave `PUBLIC_URL` at its example value and leave `WORKSPACE_PASSWORD`, `SESSION_SECRET`, and `ENCRYPTION_KEY` empty on the first deployment. The deploy script generates missing secrets and determines the actual public URL. If you set a workspace password yourself, use a unique value of at least 16 characters. Keep `PASSWORD_LOGIN_ENABLED=true` until you have verified another administrator sign-in method.

### Check configuration before deployment

This checks for missing values without printing secrets or making a model call:

```sh
uv run python -c 'from app.config import Settings; s=Settings(); missing=s.missing_cloud(); print("Missing: " + ", ".join(missing) if missing else "Required cloud fields are present (credentials not tested)."); raise SystemExit(bool(missing))'
```

Do not proceed if it lists missing fields. Presence alone does not establish authentication, model access, or network reachability.

## 4. Deploy the web app

**This command changes your Modal account and starts billed compute.** It updates the `moyai-devin` web app, `hermes-workspace-config` secret, and `hermes-workspace-state` volume. Do not run it against an existing shared installation without coordinating downtime.

```sh
uv run python deploy_modal.py
```

The script:

1. Reads `.env` and checks for Modal credentials.
2. Generates missing workspace password, session secret, and encryption key, saving them privately back to `.env`.
3. Uploads configuration into a Modal Secret, builds the web app image, and deploys one always-on web container.
4. Prints `Workspace URL: https://…` using Modal's actual assigned URL.

Open **the URL printed by your command**. The app sets its own `PUBLIC_URL` to that origin at startup; you do not need to guess a hostname or deploy twice. Retrieve `WORKSPACE_PASSWORD` from `.env` in your private editor and use it to sign in.

Keep this `.env` for redeployment, with a secure backup. Regenerating `ENCRYPTION_KEY` can make existing saved connections unreadable. `.env` is gitignored and is not bundled into the image. The web service uses one writer with persistent snapshots; see [deployment and backups](deployment.md#deploy-the-control-plane) before changing that topology.

**Local configuration rule:** explicitly present `.env` values override shell variables, even when empty. Exporting a key in your shell will not replace a blank entry in `.env`. After changing `.env` for a Modal-hosted installation, rerun the deploy command when sessions are idle; editing the local file alone does not update the running service.

## 5. Prove the agent actually runs

1. Open **Settings → Runtime**. Each required value should be configured and the badge should read **Cloud ready**. This is only a configuration check, not a successful model or Modal call.
2. Start a new session. In **Context & tools**, select **Execution → Cloud session** and leave **GitHub repository** empty. Do not choose **Demo · simulated**.
3. Select the exact model configured as `AGENT_MODEL`. The picker also contains built-in model names; their presence does **not** mean your endpoint/key supports them.
4. Send:

   > Use the terminal to create `/workspace/setup-check.txt` containing `moyai setup works`. Read it back with a tool and report the contents. Do not connect apps or publish anything.

5. Allow several minutes for the first Hermes sandbox image build. Inspect **Activity** for actual tool execution and **Files** for `setup-check.txt`. Wait for the turn to finish, not just for text to appear.
6. Send a follow-up in the same chat:

   > Read `/workspace/setup-check.txt` again using a tool. What does it contain?

You have verified real execution when tool output and the saved file agree and the follow-up can read it. A normal-looking response in Demo mode proves none of this. If the task fails, fix that layer before adding integrations.

## 6. Connect your GitHub repository

You need an organization owner or someone allowed to register/install the organization's GitHub App.

1. Confirm `.env` names **your** repository, and deploy again if you changed it after step 4.
2. Sign in as an administrator and open **Connections → GitHub → Connect**. Check that the displayed repository list is correct before continuing.
3. Click **Register a new GitHub App**, then **Continue to GitHub**. Complete the organization-owned App creation and install it on **only the configured repositories**. If your team already has a suitable App, enter its **App ID** and upload its **PEM private key** using **Verify app and continue** instead. Never paste the PEM into chat.
4. Review permissions: Contents and Pull requests **read/write**, Metadata **read**. The new-App manifest also requests Administration **write** for ruleset reviewer edits. That permission is not required for ordinary checkout/PR work with an existing suitable App. See [GitHub permissions](integrations.md#shared-organization-github) before granting it; do not add the App as a ruleset bypass actor.
5. Return to Moyai and confirm the connection is healthy. Registering an App without completing its installation is not enough.
6. Start a **new Cloud session**, set **GitHub repository** to `https://github.com/your-org/your-repo`, and ensure GitHub is checked under **Organization connections**. Existing sessions keep their original connection selection.
7. First ask for read-only work:

   > Check out this repository, identify the command for running its tests, and run the smallest relevant test suite. Report the command and actual result. Do not edit files or open a PR.

After that succeeds, request a small change and explicitly ask for a pull request when you want one. The integration can publish PRs; it cannot approve or merge them. [Slack](slack.md), [Linear, and Notion](integrations.md) can be connected later and are not prerequisites for coding.

## Troubleshooting

| Symptom | What to check / do |
| --- | --- |
| `uv` is not found or Python is too old | Install uv, reopen your shell, run `uv python install 3.12`, then `uv sync --frozen --python 3.12`. |
| `Configure Modal credentials in .env first` | CLI login is separate. Put both values from the same Modal profile into `.env`. Blank `.env` values override exported shell variables. |
| Modal rejects authentication or permissions | Check that the token belongs to the intended workspace, has not expired, and can deploy/use sandboxes. Replace the credentials privately and redeploy. |
| Modal image/deployment build fails | Read the failed build step in the deploy output or Modal dashboard. Check workspace billing/quota and network access to package/Git dependencies. A running UI does not prove the separate agent image built. |
| Localhost works but **Cloud session** is disabled | Loopback `PUBLIC_URL` deliberately disables real execution. Use the Modal URL printed by deployment, not `localhost:8787`. For a separately hosted web app, configure its real reachable HTTPS origin. |
| **Cloud ready**, then model request fails | The badge checks presence only. A gateway 401/403 usually means key/access problems; 404 can mean the wrong base URL or model alias; 429 can mean budget/rate limits. Inspect the provider/gateway error and your model access. Do not select a built-in model unless your endpoint serves that exact ID. |
| Web app cannot reach gateway | A local-only gateway is not reachable from Modal. Use a reachable protected endpoint; the app appends `/chat/completions` to `LITELLM_API_BASE`. |
| Agent fails to call back to a separately hosted app | Use HTTPS and the exact configured origin; proxies must preserve Host and allow `/broker/` requests authenticated by run tokens, without an extra browser-only login. The Modal-hosted path configures its origin for you. |
| Responses say demo/simulated | Start a new session with **Cloud session** selected. Existing demo sessions do not become real agents when configuration changes. |
| GitHub shows BerriAI's repository / wrong repositories | Change the allowlist in `.env`, redeploy, then reconnect/install the App for exactly those repositories. |
| GitHub connected but missing from an old session | Start a new session and explicitly check GitHub in **Context & tools**. Check that the connection is enabled and healthy. |
| Changed `.env`, but nothing changed in the app | Redeploy for Modal hosting; restart for a locally hosted control plane. Preserve existing secrets and wait for active turns to settle first. |

Do not include `.env`, model keys, Modal tokens, or PEM files in bug reports. Include the failed step and redacted error instead.

## Stop and maintain the installation

- Closing the browser does not stop the service. Stop/cancel active sessions, then stop the `moyai-devin` web app in the Modal dashboard if no longer needed. Check remaining sandboxes separately and review storage retention/charges; stopping the web app does not delete persisted data.
- Keep one web container. Do not enable rolling deployments, multiple Uvicorn workers, or multiple writers on the same database/volume. Redeployments interrupt active tasks.
- Back up your stable session/encryption secrets along with state. Rotate expired credentials privately and redeploy when idle.
- Use [deployment alternatives](deployment.md) if you need Render or an existing VM. Those paths still require Modal for the agent sandbox; Docker alone is not a local agent runner.

## Optional: local UI development only

In a separate checkout, follow the [local UI preview](../README.md#local-ui-preview-not-a-working-agent). It uses simulated responses and persists preview history in `.data/workspace.db`. Do not use it as proof that cloud execution, model credentials, or repository access work.
