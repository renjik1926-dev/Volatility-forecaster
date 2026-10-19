# Security

## Reporting

Please report security issues privately through GitHub's "Report a vulnerability" (Security advisories) on this repository rather than in a public issue.

## How secrets are handled

- API keys live only in GitHub Secrets (and a git-ignored `.env` locally). They are never printed; error messages from the providers are rewritten so they never contain the request URL or the key.
- Each workflow gets only the secrets it needs (see `BUILD_PLAN.md` §18). The write deploy key can write only to the private data repo; the backtest uses a read-only key.
- The bot's `GITHUB_TOKEN` can push only to the `forecasts` branch; `main` requires a reviewed pull request.
- No `pull_request_target` workflows. Third-party actions are pinned to commit SHAs. `pip-audit` runs in CI; Dependabot watches Python packages and actions.
- The site is static: no forms, cookies or tracking.
