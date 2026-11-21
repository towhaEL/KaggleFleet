# Contributing to KaggleFleet

Thanks for helping! Bug reports, docs fixes and pull requests are all welcome.

## Development setup

```bash
git clone https://github.com/towhaEL/KaggleFleet.git
cd KaggleFleet
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
pytest                   # the whole suite runs offline in a few seconds
ruff check src tests
```

- **Keep the runtime standard-library only.** The only runtime dependency is the `kaggle` CLI, which
  KaggleFleet drives as a subprocess.
- **Tests must never contact Kaggle.** Mock `subprocess.run`, or use the fake `kaggle` script pattern
  from `tests/test_link_login.py`. Use the notebooks in `tests/fixtures/notebooks/`.
- **New CLI commands need `--json` output.** Go through `emit()` / `fail()` in `cli.py` and add a test in
  `tests/test_cli_json.py`.
- **Take extra care with security-sensitive code:** credential isolation (`account_manager.py`), the HTTP
  request checks (`server/http_server.py`), and anything that returns account data to the browser.
  Changes there need tests.
- Add a line under `## [Unreleased]` in `CHANGELOG.md`.

## Testing against real Kaggle

Some behavior, such as Kaggle CLI output formats and OAuth, can only be checked against a real account.
If you do this, use your own account and a throwaway private kernel, and paste the relevant
(redacted) CLI output into the pull request.

## Reporting bugs

Please include `kfleet doctor --json` output, your OS, and the exact command you ran. **Remove any
usernames or tokens you don't want to make public.** For security problems, see [SECURITY.md](SECURITY.md)
instead of opening a public issue.
