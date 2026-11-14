# GitHub Actions workflows

Ready-to-use workflows for this repo:

- `ci.yml` — server tests (SQLite + Postgres), Python SDK tests (3.9 / 3.12), JS SDK tests, dashboard type-check/build, Docker build.
- `pages.yml` — builds the static, read-only demo dashboard from `demo/snapshot` and publishes it to GitHub Pages.

To enable them, copy them into `.github/workflows/`:

```bash
mkdir -p .github/workflows && cp deploy/github-actions/*.yml .github/workflows/
```

(They live here because they were pushed with a token that lacked the `workflow` scope.
The live demo is currently published from the `gh-pages` branch via `scripts/publish_pages.sh`.)
