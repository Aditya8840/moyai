# Local setup

[Documentation](README.md) · [Project overview](../README.md)

> This guide retains the detailed reference material from the original README.
> Dated acceptance reports describe past checks, not a current deployment or test result.

For the shortest install-and-preview path, follow the [getting-started steps](../README.md#getting-started). Local demo responses are simulated; real agent execution requires [cloud setup](deployment.md#enable-cloud-runs).

## Start locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
cp .env.example .env
uv sync --frozen
uv run uvicorn app.main:app --host 127.0.0.1 --port 8787 --workers 1
```

Open **http://127.0.0.1:8787**. Start a demo task, inspect its activity, or stop it. History survives server restarts in `.data/workspace.db`.

Values explicitly present in this project's `.env` take precedence over shell environment variables, including an empty value. This prevents accidentally using an unrelated globally configured gateway key. Container deployments do not include the `.env` file and use their configured environment/secret store.

Use exactly **one server process**. The MVP owns its job queue and runner in that process. Do not use multiple Uvicorn workers, multiple containers sharing the database, or development auto-reload while real runs are active.
