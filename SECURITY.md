# Security policy

KaggleFleet stores Kaggle credentials for several people on one machine, so security reports are
taken seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's
[private vulnerability reporting](https://github.com/towhaEL/KaggleFleet/security/advisories/new)
and include steps to reproduce. We aim to respond within a week.

## Threat model

**In scope:**

- Credentials of one account being used for, or leaking into, another account's commands.
- Other websites open in your browser driving the local web panel (CSRF, DNS rebinding).
- Secrets reaching the browser, logs, `--json` output, or error messages.
- Files written with permissions that are too broad.

**Out of scope:**

- An attacker who already runs code as your OS user. Credentials are stored unencrypted in
  `~/.kaggle_fleet` (mode `0700` / `0600`), the same way the Kaggle CLI stores its own.
- Exposing `kfleet serve` on a network address. The panel has no authentication, which is documented.
  Use an SSH tunnel instead.

## Hardening tips

- Keep `kfleet serve` on `127.0.0.1` (the default).
- Run KaggleFleet on a machine whose users you trust.
- Remove accounts you no longer use with `kfleet account remove`, and revoke their API tokens on kaggle.com.
