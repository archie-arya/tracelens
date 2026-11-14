# Deploying a shared instance (optional)

TraceLens is local-first: `./scripts/run_local.sh` or `docker compose up` from the repo root is all one developer needs.
For one server a whole team sends traces to, use the same Docker image anywhere you can run a container. The files here are starting points for two managed hosts.

| File | Host | Use |
|---|---|---|
| `fly.toml` | Fly.io | `fly launch --config deploy/fly.toml --copy-config --no-deploy`, `fly volumes create tracelens_data --size 1`, `fly secrets set GEMINI_API_KEY=... TRACELENS_INIT_API_KEY=...`, `fly deploy --config deploy/fly.toml` (run from the repo root) |
| `render.yaml` | Render | New → Blueprint, select this repo, set the blueprint path to `deploy/render.yaml`. It creates the web service plus a free Postgres database |

Before sending production data, always set `TRACELENS_INIT_API_KEY` (or create projects with the admin token), use Postgres for anything long-lived, and put the server behind HTTPS.
Set secrets with the host's secret store. Never put them in these files.
