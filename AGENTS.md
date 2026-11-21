# AGENTS.md: using KaggleFleet from an AI agent

This file is written for coding agents (Claude Code, Codex, Cursor, and similar) that run notebooks
on Kaggle GPUs for a human research team. It has two parts:
[**Using the tool**](#part-1-using-kfleet) and [**Working on this repository**](#part-2-working-on-this-repository).

To give an agent in *your* research repo this guidance, add a line like this to that repo's
`AGENTS.md` or `CLAUDE.md`:

> Notebooks run on Kaggle through `kfleet`. Follow https://github.com/towhaEL/KaggleFleet/blob/main/AGENTS.md (Part 1).

---

## Part 1: using `kfleet`

### Rules

1. **Always pass `--json`** and parse stdout. A non-zero exit code means failure, and stdout then holds `{"error": "..."}`.
2. **Every `run push` starts a new run on Kaggle and uses real GPU quota** that belongs to a human
   co-author. Push only when the user asked for a run. Never push again just to "retry" a status check,
   and never push in a loop.
3. **Never add, remove or switch accounts unless the user asks.** Account sign-in needs a human in a browser.
4. **Never print, log or commit credentials.** `kfleet` itself never prints secrets. Do not read
   `~/.kaggle_fleet/accounts.json` or anything under `~/.kaggle_fleet/envs/`.
5. Don't start `kfleet serve` unless asked, because it is a long-running process meant for humans.
6. Each account belongs to a real person. Do not suggest creating extra Kaggle accounts to get more quota.

### Workflow

```bash
# 0. Check the setup once per session. Exit code 0 means ready.
kfleet doctor --json

# 1. What can be run, and on which account?
kfleet notebook list --json
kfleet account list --json          # includes usage.gpu_hours for the last 7 days

# 2. Push. Returns as soon as Kaggle accepts the kernel.
kfleet run push <notebook.ipynb> --account <username> --json [--accelerator NvidiaTeslaT4]

# 3. Poll status. Wait at least 60 s between polls; runs take minutes to hours.
kfleet run status <job_id> --json

# 4. When status is COMPLETE, fetch the outputs.
kfleet run download <job_id> --json   # returns output_dir and the list of files

# On ERROR, read the logs to diagnose the problem before touching the notebook.
kfleet run logs <job_id> --json
```

`<job_id>` can also be the kernel slug (for example `nb-02-train`). If you leave it out, the most
recent job is used.

### Choosing an account

When the user doesn't name an account, pick one from `kfleet account list --json` with
`status == "valid"`, the lowest `usage.gpu_hours`, and `usage.active_jobs < 2`. Kaggle allows only a
couple of concurrent GPU sessions per account. Tell the user which account you chose.
`usage` is an **estimate** from jobs launched through KaggleFleet. Kaggle's quota page is authoritative.

### Job status values

| Status | Meaning | Next step |
|---|---|---|
| `QUEUED` / `RUNNING` | On Kaggle | Keep polling `run status` |
| `COMPLETE` | Finished successfully | `run download` |
| `DOWNLOADED` | Outputs are on disk (`job.output_dir`) | Done |
| `ERROR` | The push was rejected or the kernel failed. See `job.error_message`. | `run logs`, then fix and ask before pushing again |
| `CANCELLED` | Stopped on kaggle.com | Ask the user |

A push can fail even when Kaggle accepted the request, for example with
`Maximum batch GPU session count of 2 reached`. That is reported as `"success": false` with the reason.
Use a different account or wait. Don't loop.

### JSON shapes (abridged)

```jsonc
// kfleet run push|status <...> --json
{ "success": true,
  "job": { "id": "job_20261121_101500_nb-02-train_3f2a", "kernel_id": "bob/nb-02-train",
           "kernel_slug": "nb-02-train", "account_id": "acc_bob_1a2b3c", "username": "bob",
           "status": "RUNNING", "accelerator": "NvidiaTeslaT4", "created_at": "...",
           "started_at": "...", "completed_at": null, "output_dir": null,
           "error_message": null, "last_log_snippet": "..." } }

// kfleet account list --json
{ "active_account_id": "acc_alice_9f8e7d", "usage_window_days": 7,
  "accounts": [ { "id": "acc_alice_9f8e7d", "username": "alice", "label": "Alice",
                  "status": "valid", "active": true, "auth_type": "oauth_token",
                  "usage": { "gpu_hours": 3.2, "active_jobs": 1 }, "credentials": {} } ] }

// any failure (exit code 1)
{ "error": "Job 'nope' not found in local history." }
```

### Notebooks on Kaggle

- Inputs are mounted read-only under `/kaggle/input/<dataset-slug>/`. Attach them with `--dataset owner/slug`,
  `--competition slug`, `--kernel-source owner/kernel-slug` or `--model ...`.
- Anything written to `/kaggle/working/` becomes the kernel's output, which is what `run download` fetches.
- A run is limited to Kaggle's session limit (currently 12 h). `--max-runtime SECONDS` sets a lower cap.
- To chain stages, attach the earlier kernel's output with `--kernel-source <user>/<slug>`.

---

## Part 2: working on this repository

- Layout: `src/kaggle_fleet/` (package), `tests/` (unittest, runs under pytest), `docs/`.
- The runtime is **standard library only**. Do not add runtime dependencies besides `kaggle`.
- Tests must never reach the network or a real Kaggle account. Mock `subprocess.run` or use the fake
  `kaggle` script pattern in `tests/test_link_login.py`. Use `tests/fixtures/notebooks/` for notebooks.
- Before you finish: `pytest` and `ruff check src tests` must pass.
- Security-sensitive areas need extra care and tests:
  - `account_manager.get_env_for_account` / `_ensure_private_home`: per-account isolation
  - `server/http_server.py` `_api_request_allowed`: cross-site and DNS-rebinding protection
  - `server/api_handlers.py` `_public`: secrets must never reach the browser
- Every new CLI command needs a `--json` output path that goes through `emit()` / `fail()` in `cli.py`.
