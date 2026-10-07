# Moyai Devin

Moyai Devin is a self-hosted AI coding agent for background engineering work. Ask it to fix a bug or build a feature from your browser or Slack. The agent works in an isolated cloud workspace, where it can edit code, run tests, and open a pull request for your review. Connect GitHub, Linear, Slack, or Notion to give it access to your code and team context. You can check its progress and send corrections while it works, or resume the task with your saved conversation and files.

<!-- Add the recorded local-demo GIF here once uploaded to the repository.
Suggested path: docs/assets/getting-started.gif
Caption: Local demo: submit a task, watch its activity, and view the saved result.
Agent responses in this preview are simulated. -->

## Getting started

Try the local demo without API keys or cloud accounts. You'll need **Git**, **Python 3.12+**, and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**.

1. **Clone the repository.**

   ```sh
   git clone https://github.com/BerriAI/moyai-devin.git
   cd moyai-devin
   ```

2. **Install dependencies.**

   ```sh
   cp .env.example .env
   uv sync --frozen
   ```

3. **Start Moyai.**

   ```sh
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --workers 1
   ```

4. **Send your first task.** Open [localhost:8787](http://127.0.0.1:8787), type a message, and click **Start session**. Open **Activity** to follow the demo, then send a follow-up in the same chat.

To run real tasks, **[set up cloud execution](docs/deployment.md#enable-cloud-runs)** with Modal and a model gateway, then **[connect your apps](docs/integrations.md)**. In the local demo, you receive simulated responses without model calls or repository changes.

BerriAI teammates can [open the hosted workspace](https://moyai-devin-litellm.onrender.com) and sign in with an `@berri.ai` Google account.

## More information

- [All documentation](docs/README.md)
- [Using Moyai](docs/usage.md) · [Slack](docs/slack.md) · [Skills](docs/skills.md) · [Automations](docs/automations.md)
- [Deployment](docs/deployment.md) · [App connections](docs/integrations.md) · [Security and limitations](docs/security-and-scope.md)
- [Architecture](docs/architecture.md) · [Testing](docs/verification.md) · [Costs](docs/costs.md) · [Tracing](docs/observability.md)
