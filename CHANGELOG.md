# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.2] - 2026-11-22

### Changed
- Metadata-only patch release to rerun the production release and deployment pipeline.

## [0.1.1] - 2026-11-22

### Changed
- Allow renewing credentials in place for invalid Kaggle accounts during login.
- Make PyPI publishing retry-safe so an existing distribution does not fail the release workflow.

## [0.1.0] - 2026-11-21

First public release.

### Added
- Multiple Kaggle accounts on one machine, each in an isolated credential sandbox (per-account `KAGGLE_CONFIG_DIR` and `HOME`).
- Account sign-in through the browser (sandboxed `kaggle auth login`), sign-in with a link and code, API keys, and import from `~/.kaggle`.
- `kfleet account test` checks the identity Kaggle reports, not just that a login works.
- Notebook discovery, staging, and `kernel-metadata.json` generation, with dataset, competition, kernel and model sources.
- Push, status, logs and output download for Kaggle kernels, plus a `--watch` mode.
- Local web panel (`kfleet serve`) with accounts, estimated weekly GPU hours per account, launches, jobs and logs.
- `--json` output on every command, plus `kfleet doctor`, for scripts and AI agents. See `AGENTS.md`.
- Cross-process file locking for the state files, so the CLI and the web panel can run at the same time.
- Web panel protection against cross-site requests and DNS rebinding.
- Published on PyPI as `kaggle-fleet` (`pipx install kaggle-fleet`), with a tag-driven release workflow using PyPI Trusted Publishing.
