# Runbook

## One-time setup (week 0)

1. **Repos.** Create the public repo (MIT) and a **private** repo named `volforecast-data`.
2. **Deploy keys on `volforecast-data`.**
   - One with write access; store its private half as the `DATA_REPO_DEPLOY_KEY` secret.
   - One read-only; store it as `DATA_REPO_READ_KEY`.
3. **The `forecasts` branch.** Create it with an empty `data/` folder (for example a `.gitkeep`). Protect it with "block force pushes" and "restrict deletions". Don't require pull requests, so the bot can push.
4. **Protect `main`.** Require a pull request with passing `ci`.
5. **Secrets.** `ALPHAVANTAGE_API_KEY`, `FRED_API_KEY`, `DATA_REPO_DEPLOY_KEY`, `DATA_REPO_READ_KEY`, `HEARTBEAT_URL`.
6. **Pages.** Settings → Pages → Source: GitHub Actions.
7. **Notifications.** Turn on email notifications for failed workflow runs and force one failure to test them.
8. **Heartbeat.** Create a heartbeat monitor that alerts if no ping arrives within 36 hours on a trading day.
9. **Smoke test.** Run `uv run volforecast smoke` locally with the keys in `.env`.
10. **Snapshot.** Run `uv run volforecast snapshot` once, with `VOLFORECAST_PRIVATE_DIR` pointing at a checkout of `volforecast-data`, then commit and push it. It is never refreshed.

## Symptoms and actions

| Symptom | Likely cause | Action |
|---|---|---|
| `data_not_ready` on both runs | Provider's end-of-day bar delayed or missing | Run `daily` manually later. If the session was an unscheduled closure, add its date to `data/no_session.csv` (column `date`) on the `forecasts` branch. |
| `rate_limited` or `premium_endpoint` | Daily cap reached, or the endpoint moved to premium | Check `runs.csv` call counts and the provider docs; switch provider via config if needed. |
| `auth_error` | API key revoked or expired | Create a new key and update the secret (see key rotation). |
| Validation failure in the run notes | Bad bar from the provider, or a split/dividend not yet recorded | Inspect `revisions.csv` in the private repo; rerun after the provider corrects it. |
| Repeated `fallback` fits (`health` flags 2+ in a row) | Model instability, e.g. an extreme market day | Check `params/` in the private repo. If it persists, release a new model version. |
| Workflow shows "disabled" | 60-day inactivity rule | Re-enable it in the Actions tab and find out why the job stopped. |
| Private-repo push fails | Deploy key removed or repo renamed | Re-add the deploy key. The job refuses to publish forecasts until this works. |
| A holiday is missing from the calendar | Calendar package not yet updated | Add the date to `data/no_session.csv`; upgrade `exchange_calendars`. |
| Forecast committed after the open | Run delayed | Nothing to fix: it is marked `on_time = false` and excluded. |
| `budget_exhausted` | Retries or manual runs used up the day's calls | Wait for tomorrow; check `runs.csv` and `budget.json` for what used the calls. |
| Proof stamping failed | OpenTimestamps calendar servers unreachable | Nothing urgent: the next run stamps the missed file, marked late. |
| Proof still pending after 7 days | `proofs.yml` failing or not running | Run `proofs.yml` manually and check its log. |
| Privacy check stopped a publish | A code change added a raw column to public output | Fix the code. Never bypass the check. |
| Key needs replacing | Key leaked, revoked or expired | Follow the key rotation steps below. |

## Key rotation

1. Create the new key at the provider.
2. Update `.env` locally and the GitHub secret.
3. Run `uv run volforecast smoke`.
4. Revoke the old key.

## Forced-failure drill (definition of done)

1. Temporarily set `ALPHAVANTAGE_API_KEY` to an invalid value.
2. Run `daily` manually and confirm the failure notification arrives.
3. Follow the `auth_error` row above, then restore the key.
