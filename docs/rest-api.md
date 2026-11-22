# REST API

`kfleet serve` exposes the JSON API that the web panel uses. It is meant for the panel and for local
scripts. For automation, the CLI with `--json` is usually simpler (see [`AGENTS.md`](../AGENTS.md)).

**Access rules.** Requests must arrive with a `Host` header naming the server (`127.0.0.1`, `localhost`,
`::1`, the bind address, or a name added with `--allow-host`). Requests that carry an `Origin` header must be
same-origin. `POST` bodies must be `Content-Type: application/json`. There is no CORS support and no
authentication, so keep the server on loopback.

Responses are JSON. Errors look like `{"error": "..."}` with a 4xx or 5xx status.
Account objects never include credentials: `credentials` is always `{}`.

## Status

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | Version, Kaggle CLI info, active account, counts, notebook/output/staging dirs |

## Accounts

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/accounts` | | All accounts with `usage` (`gpu_hours`, `tpu_hours`, `active_jobs`) for the current quota week, plus `quota_window_start`, `quota_reset_at` and `quota_resets_in_seconds` |
| POST | `/api/accounts` | `{username, api_key, label?, make_active?, notes?}` | Add an API-key account |
| POST | `/api/accounts/import` | | Import `~/.kaggle/kaggle.json` / `credentials.json` |
| POST | `/api/accounts/login` | `{label?, make_active?}` | Browser sign-in on the server machine (blocks until done) |
| POST | `/api/accounts/login/start` | `{label?, make_active?}` | Start a link sign-in. Returns `{session, url}`. |
| POST | `/api/accounts/login/finish` | `{session, code}` | Finish it with the code Kaggle shows |
| POST | `/api/accounts/login/cancel` | `{session}` | Abandon a link sign-in |
| GET | `/api/accounts/:id` | | One account (`:id` may be the username) |
| DELETE | `/api/accounts/:id` | | Remove the account's local sandbox |
| POST | `/api/accounts/:id/switch` | | Make it the active account |
| POST | `/api/accounts/:id/test` | | Check that the credentials authenticate as this account |

## Notebooks

| Method | Path | Description |
|---|---|---|
| GET | `/api/notebooks` | Discovered notebooks (`?path=DIR` to scan another directory) |
| GET | `/api/notebooks/:name` | Metadata plus a preview of the first 5 cells |

## Runs

| Method | Path | Body / query | Description |
|---|---|---|---|
| GET | `/api/runs` | `?limit=50&account=<id>` | Jobs, newest first |
| POST | `/api/runs` | see below | Stage and push a notebook, then watch it in the background |
| GET | `/api/runs/:id` | `?refresh=1` | One job. Queued or running jobs are refreshed from Kaggle automatically. |
| GET | `/api/runs/:id/logs` | | Kernel logs |
| POST | `/api/runs/:id/download` | `{output_path?}` | Download outputs (defaults to `<outputs dir>/<slug>`) |

`POST /api/runs` body:

```json
{
  "notebook": "02_train.ipynb",
  "account_id": "bob",
  "accelerator": "NvidiaTeslaT4",
  "enable_gpu": true,
  "enable_internet": true,
  "auto_download": true,
  "max_runtime_hours": 4,
  "dataset_sources": ["bob/preprocessed-data"],
  "title": "optional",
  "slug": "optional"
}
```

It returns `201 {"success": true, "job": {...}}`. If Kaggle rejects the push, the job comes back with
`"status": "ERROR"` and an `error_message`.
