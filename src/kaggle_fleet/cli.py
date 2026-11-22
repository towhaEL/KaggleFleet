"""
Command line interface for KaggleFleet (`kfleet`).

Every command accepts the global `--json` flag, which prints a single JSON
document on stdout instead of tables -- intended for scripts and AI agents.
Errors are then printed as {"error": "..."} with a non-zero exit code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__, config
from .account_manager import AccountManager
from .models import JobStatus, RunOptions
from .notebook_manager import NotebookManager
from .kaggle_runner import KaggleRunner, empty_usage, quota_info
from .storage import StorageEngine


# ANSI Color formatting
class Style:
    USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    RESET = "\033[0m" if USE_COLOR else ""
    BOLD = "\033[1m" if USE_COLOR else ""
    DIM = "\033[2m" if USE_COLOR else ""
    RED = "\033[31m" if USE_COLOR else ""
    GREEN = "\033[32m" if USE_COLOR else ""
    YELLOW = "\033[33m" if USE_COLOR else ""
    BLUE = "\033[34m" if USE_COLOR else ""
    MAGENTA = "\033[35m" if USE_COLOR else ""
    CYAN = "\033[36m" if USE_COLOR else ""
    WHITE = "\033[37m" if USE_COLOR else ""


def emit(data: Any) -> int:
    """Print a JSON document for --json mode."""
    print(json.dumps(data, indent=2, default=str))
    return 0


def fail(args: argparse.Namespace, prefix: str, err: Any) -> int:
    """Report an error in the active output mode and return exit code 1."""
    if getattr(args, "json", False):
        print(json.dumps({"error": f"{prefix}: {err}" if prefix else str(err)}))
    else:
        print(f"{Style.RED}[!] {prefix}:{Style.RESET} {err}" if prefix else
              f"{Style.RED}[!] {err}{Style.RESET}", file=sys.stderr)
    return 1


def public_account(acc: Any, active_id: str | None = None) -> dict[str, Any]:
    """Account dict without secrets."""
    d = acc.to_dict()
    d["credentials"] = {}
    d["active"] = acc.id == active_id
    return d


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Renders a clean formatted ASCII/Unicode terminal table."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            clean_cell = cell.replace(Style.BOLD, "").replace(Style.GREEN, "").replace(
                Style.RED, "").replace(Style.YELLOW, "").replace(Style.CYAN, "").replace(
                Style.DIM, "").replace(Style.RESET, "").replace(Style.MAGENTA, "")
            if idx < len(col_widths):
                col_widths[idx] = max(col_widths[idx], len(clean_cell))
            else:
                col_widths.append(len(clean_cell))

    # Header
    header_str = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_str = "-+-".join("-" * col_widths[i] for i in range(len(headers)))

    lines = [
        f"{Style.BOLD}{header_str}{Style.RESET}",
        f"{Style.DIM}{sep_str}{Style.RESET}",
    ]

    for row in rows:
        formatted_cells = []
        for i, cell in enumerate(row):
            clean_len = len(
                cell.replace(Style.BOLD, "").replace(Style.GREEN, "").replace(
                    Style.RED, "").replace(Style.YELLOW, "").replace(Style.CYAN, "").replace(
                    Style.DIM, "").replace(Style.RESET, "").replace(Style.MAGENTA, "")
            )
            padding = " " * (col_widths[i] - clean_len)
            formatted_cells.append(cell + padding)
        lines.append(" | ".join(formatted_cells))

    return "\n".join(lines)


# -------------------------------------------------------------------------
# Account Subcommands
# -------------------------------------------------------------------------

def cmd_account_list(args: argparse.Namespace, mgr: AccountManager) -> int:
    accounts = mgr.list_accounts()
    active_id = mgr.get_active_account_id()
    runner = KaggleRunner(account_manager=mgr, storage=mgr.storage)
    if getattr(args, "refresh_usage", False):
        runner.refresh_runtimes()
    usage = runner.gpu_usage_by_account()
    quota = quota_info()

    if args.json:
        out = []
        for a in accounts:
            d = public_account(a, active_id)
            d["usage"] = usage.get(a.id, empty_usage())
            out.append(d)
        return emit({"active_account_id": active_id, **quota, "accounts": out})

    if not accounts:
        print(f"{Style.YELLOW}No Kaggle accounts configured yet.{Style.RESET}")
        print("Run `kfleet account login`, `kfleet account import` or `kfleet account add` to get started.")
        return 0

    headers = ["Act", "Account ID", "Label", "Username", "Auth", "Status", "GPU h (week)", "Running", "Last Used"]
    rows = []
    for acc in accounts:
        is_active = (acc.id == active_id)
        act_mark = f"{Style.GREEN}* {Style.RESET}" if is_active else "  "

        status_colored = acc.status
        if acc.status == "valid":
            status_colored = f"{Style.GREEN}valid{Style.RESET}"
        elif acc.status == "invalid":
            status_colored = f"{Style.RED}invalid{Style.RESET}"
        else:
            status_colored = f"{Style.YELLOW}{acc.status}{Style.RESET}"

        last_used = acc.last_used_at[:16].replace("T", " ") if acc.last_used_at else "Never"

        rows.append([
            act_mark,
            acc.id,
            acc.label,
            acc.username,
            acc.auth_type,
            status_colored,
            f"{usage.get(acc.id, empty_usage())['gpu_hours']:.1f}",
            str(usage.get(acc.id, empty_usage())["active_jobs"]),
            last_used,
        ])

    print(f"\n{Style.BOLD}Configured Kaggle Accounts ({len(accounts)}):{Style.RESET}\n")
    print(format_table(headers, rows))
    left = quota["quota_resets_in_seconds"]
    reset = quota["quota_reset_at"][:16].replace("T", " ")
    print(f"\nQuota resets {reset} UTC (in {left // 86400}d {left % 86400 // 3600}h). "
          "GPU hours are an estimate; Kaggle's quota page is authoritative.")
    print()
    return 0


def cmd_account_add(args: argparse.Namespace, mgr: AccountManager) -> int:
    try:
        acc = mgr.add_account(
            label=args.label or args.username,
            username=args.username,
            auth_type="api_key",
            credentials={"key": args.key},
            make_active=args.active,
            quota_notes=args.quota_notes or "30h weekly GPU",
            notes=args.notes or "",
        )
        if args.json:
            return emit({"success": True, "account": public_account(acc, mgr.get_active_account_id())})
        print(f"{Style.GREEN}[+] Added API Key account:{Style.RESET} {acc.username} (ID: {acc.id})")
        if args.active:
            print(f"{Style.CYAN}[*] Switched active account to: {acc.username}{Style.RESET}")
        return 0
    except Exception as e:
        return fail(args, "Failed to add account", e)


def cmd_account_add_oauth(args: argparse.Namespace, mgr: AccountManager) -> int:
    cred_path = Path(args.credentials_file).resolve()
    if not cred_path.is_file():
        return fail(args, "Credentials file not found", cred_path)

    try:
        with open(cred_path, encoding="utf-8") as f:
            cred_data = json.load(f)

        username = cred_data.get("username")
        if not username:
            return fail(args, "", "No 'username' field found in credentials JSON.")

        acc = mgr.add_account(
            label=args.label or f"{username} (OAuth)",
            username=username,
            auth_type="oauth_token",
            credentials=cred_data,
            make_active=args.active,
            quota_notes=args.quota_notes or "30h weekly GPU",
            notes=args.notes or f"Imported from {cred_path.name}",
        )
        if args.json:
            return emit({"success": True, "account": public_account(acc, mgr.get_active_account_id())})
        print(f"{Style.GREEN}[+] Added OAuth account:{Style.RESET} {acc.username} (ID: {acc.id})")
        if args.active:
            print(f"{Style.CYAN}[*] Switched active account to: {acc.username}{Style.RESET}")
        return 0
    except Exception as e:
        return fail(args, "Failed to import OAuth credentials", e)


def cmd_account_login(args: argparse.Namespace, mgr: AccountManager) -> int:
    print("Opening Kaggle sign-in in your browser. Whichever Kaggle account is signed in")
    print("there is the one that gets added; your current ~/.kaggle login is not touched.")
    print("Signing in to an account that is already here but invalid renews its credentials.")
    known = {a.id for a in mgr.list_accounts()}
    try:
        acc = mgr.login_oauth(label=args.label, make_active=args.active, no_browser=args.no_browser)
    except Exception as e:
        print(f"{Style.RED}[!] Login failed:{Style.RESET} {e}", file=sys.stderr)
        return 1
    verb = "Renewed credentials for" if acc.id in known else "Added OAuth account:"
    print(f"{Style.GREEN}[+] {verb}{Style.RESET} {acc.username} (ID: {acc.id})")
    ok, msg = mgr.test_account(acc.id)
    print(f"{Style.GREEN if ok else Style.RED}[{'PASS' if ok else 'FAIL'}] {msg}{Style.RESET}")
    return 0 if ok else 1


def cmd_account_switch(args: argparse.Namespace, mgr: AccountManager) -> int:
    try:
        acc = mgr.set_active_account(args.account)
        if args.json:
            return emit({"success": True, "active_account_id": acc.id, "username": acc.username})
        print(f"{Style.GREEN}[*] Active Kaggle account switched to:{Style.RESET} {acc.username} ({acc.id})")
        print(f"{Style.DIM}Environment symlink updated: {mgr.active_symlink}{Style.RESET}")
        return 0
    except Exception as e:
        return fail(args, "Switch failed", e)


def cmd_account_test(args: argparse.Namespace, mgr: AccountManager) -> int:
    target = args.account
    acc = mgr.get_account(target) if target else mgr.get_active_account()

    if not acc:
        return fail(args, "", "No account to test. Specify an account or activate one.")

    if args.json:
        ok, msg = mgr.test_account(acc.id)
        emit({"account_id": acc.id, "username": acc.username, "valid": ok, "message": msg})
        return 0 if ok else 1

    print(f"Testing authentication for account '{acc.username}' ({acc.id})...")
    ok, msg = mgr.test_account(acc.id)
    if ok:
        print(f"{Style.GREEN}[PASS] {msg}{Style.RESET}")
        return 0
    else:
        print(f"{Style.RED}[FAIL] {msg}{Style.RESET}")
        return 1


def cmd_account_remove(args: argparse.Namespace, mgr: AccountManager) -> int:
    ok = mgr.delete_account(args.account)
    if args.json:
        if not ok:
            return fail(args, "", f"Account '{args.account}' not found.")
        return emit({"success": True, "deleted": args.account})
    if ok:
        print(f"{Style.GREEN}[+] Account '{args.account}' removed successfully.{Style.RESET}")
        return 0
    else:
        print(f"{Style.RED}[!] Account '{args.account}' not found.{Style.RESET}", file=sys.stderr)
        return 1


def cmd_account_import(args: argparse.Namespace, mgr: AccountManager) -> int:
    imported = mgr.import_existing_credentials()
    if args.json:
        active_id = mgr.get_active_account_id()
        return emit({"imported_count": len(imported),
                     "accounts": [public_account(a, active_id) for a in imported]})
    if not imported:
        print(f"{Style.YELLOW}No new Kaggle credentials discovered in ~/.kaggle/{Style.RESET}")
        return 0

    print(f"{Style.GREEN}[+] Successfully imported {len(imported)} account(s):{Style.RESET}")
    for acc in imported:
        print(f"  - {acc.username} ({acc.auth_type}) [ID: {acc.id}]")
    return 0


# -------------------------------------------------------------------------
# Notebook Subcommands
# -------------------------------------------------------------------------

def cmd_notebook_list(args: argparse.Namespace, nb_mgr: NotebookManager) -> int:
    target_path = Path(args.path) if args.path else None
    notebooks = nb_mgr.list_notebooks(directory=target_path)

    if args.json:
        return emit({"directory": str((target_path or nb_mgr.notebooks_dir).resolve()),
                     "notebooks": [nb.to_dict() for nb in notebooks]})

    if not notebooks:
        print(f"{Style.YELLOW}No notebooks found in {target_path or nb_mgr.notebooks_dir}{Style.RESET}")
        return 0

    headers = ["Notebook File", "Kaggle Slug", "Cells (Code/MD)", "Size", "Derived Title"]
    rows = []
    for nb in notebooks:
        cells_str = f"{nb.total_cells} ({nb.code_cells}/{nb.markdown_cells})"
        size_kb = f"{nb.file_size_bytes / 1024:.1f} KB"
        rows.append([
            f"{Style.BOLD}{nb.name}{Style.RESET}",
            nb.slug,
            cells_str,
            size_kb,
            nb.title[:35],
        ])

    print(f"\n{Style.BOLD}Discovered Notebooks in {target_path or nb_mgr.notebooks_dir} ({len(notebooks)}):{Style.RESET}\n")
    print(format_table(headers, rows))
    print()
    return 0


def cmd_notebook_inspect(args: argparse.Namespace, nb_mgr: NotebookManager) -> int:
    try:
        info, nb_data = nb_mgr.inspect_notebook(args.notebook)
    except Exception as e:
        return fail(args, "Failed to inspect notebook", e)

    if args.json:
        preview = []
        for cell in nb_data.get("cells", [])[:5]:
            src = cell.get("source", [])
            preview.append({"cell_type": cell.get("cell_type", ""),
                            "preview": ("".join(src) if isinstance(src, list) else src)[:200]})
        return emit({"notebook": info.to_dict(), "cell_preview": preview})

    print(f"\n{Style.BOLD}=== Notebook Inspection: {info.name} ==={Style.RESET}")
    print(f"  {Style.CYAN}Path:{Style.RESET}            {info.path}")
    print(f"  {Style.CYAN}Derived Title:{Style.RESET}   {info.title}")
    print(f"  {Style.CYAN}Kernel Slug:{Style.RESET}     {info.slug}")
    print(f"  {Style.CYAN}Total Cells:{Style.RESET}     {info.total_cells} (Code: {info.code_cells}, Markdown: {info.markdown_cells})")
    print(f"  {Style.CYAN}File Size:{Style.RESET}       {info.file_size_bytes / 1024:.1f} KB")
    print(f"  {Style.CYAN}Last Modified:{Style.RESET}   {info.last_modified}")

    cells = nb_data.get("cells", [])
    print(f"\n{Style.BOLD}Cell Preview (first 3 cells):{Style.RESET}")
    for idx, cell in enumerate(cells[:3]):
        ctype = cell.get("cell_type", "unknown")
        src = cell.get("source", [])
        src_text = "".join(src) if isinstance(src, list) else src
        preview = src_text.strip().splitlines()
        first_line = preview[0][:80] if preview else "(empty cell)"
        print(f"  [{idx+1}] [{ctype.upper()}]: {first_line}")
    print()
    return 0


# -------------------------------------------------------------------------
# Run Subcommands
# -------------------------------------------------------------------------

def cmd_run_push(args: argparse.Namespace, runner: KaggleRunner) -> int:
    enable_gpu = not args.no_gpu
    enable_internet = not args.no_internet
    accelerator = "None" if args.no_gpu else (args.accelerator or "NvidiaTeslaT4")

    options = RunOptions(
        notebook=args.notebook,
        account_id=args.account,
        title=args.title,
        slug=args.slug,
        accelerator=accelerator,
        enable_gpu=enable_gpu,
        enable_internet=enable_internet,
        dataset_sources=args.dataset or [],
        competition_sources=args.competition or [],
        kernel_sources=args.kernel_source or [],
        model_sources=args.model or [],
        auto_download=args.auto_download,
        watch=args.watch,
        timeout=args.timeout or 12 * 3600,
    )

    say = (lambda *a, **k: None) if args.json else print
    try:
        say(f"Preparing staging environment for '{args.notebook}'...")
        job = runner.submit_job(options)
        say(f"{Style.GREEN}[+] Job staged:{Style.RESET} {job.id} (Kernel: {job.kernel_id})")

        say(f"Pushing to Kaggle (Accelerator: {job.accelerator}, GPU: {job.enable_gpu})...")
        job = runner.push_job(job.id, accelerator=job.accelerator,
                              timeout=getattr(args, "max_runtime", None))

        if job.status == JobStatus.ERROR:
            if args.json:
                emit({"success": False, "error": f"Push failed: {job.error_message}",
                      "job": job.to_dict()})
                return 1
            print(f"{Style.RED}[!] Push failed:{Style.RESET}\n{job.error_message}", file=sys.stderr)
            return 1

        if args.json:
            if args.watch:
                job = runner.run_job_sync(job.id, watch=True, auto_download=args.auto_download,
                                          timeout=options.timeout, push=False)
            emit({"success": job.status != JobStatus.ERROR, "job": job.to_dict()})
            return 0 if job.status != JobStatus.ERROR else 1

        print(f"{Style.GREEN}[+] Kernel pushed successfully! Status: {job.status}{Style.RESET}")

        if args.watch:
            print(f"Watching job {job.id}... (Ctrl+C to stop watching; execution continues on Kaggle)")
            # Already pushed above: push=False, or Kaggle starts a second run.
            job = runner.run_job_sync(job.id, watch=True, auto_download=args.auto_download,
                                      timeout=options.timeout, push=False)
            print(f"Final status: {job.status}")
            if job.status == JobStatus.COMPLETE and args.auto_download:
                print(f"{Style.GREEN}[+] Outputs downloaded to:{Style.RESET} {job.output_dir}")
        else:
            print(f"Monitor status with: `kfleet run status {job.id}`")

        return 0
    except Exception as e:
        return fail(args, "Push execution failed", e)


def cmd_run_status(args: argparse.Namespace, runner: KaggleRunner) -> int:
    target = args.job
    if not target:
        jobs = runner.list_jobs(limit=1)
        if not jobs:
            if args.json:
                return fail(args, "", "No jobs recorded yet.")
            print(f"{Style.YELLOW}No jobs recorded yet.{Style.RESET}")
            return 0
        job_id = jobs[0].id
    else:
        job = runner.get_job(target)
        if not job:
            # Maybe target is a kernel slug/id
            all_jobs = runner.list_jobs(limit=100)
            for j in all_jobs:
                if j.kernel_slug == target or j.kernel_id == target:
                    job = j
                    break
        if not job:
            return fail(args, "", f"Job '{target}' not found in local history.")
        job_id = job.id

    job = runner.check_status(job_id)
    if args.json:
        return emit({"job": job.to_dict()})
    print(f"\n{Style.BOLD}Job Status for: {job.id}{Style.RESET}")
    print(f"  Kernel:      {job.kernel_id}")
    print(f"  Account:     {job.username} ({job.account_id})")
    status_color = Style.GREEN if job.status in [JobStatus.COMPLETE, JobStatus.DOWNLOADED] else (
        Style.RED if job.status == JobStatus.ERROR else Style.YELLOW
    )
    print(f"  Status:      {status_color}{job.status}{Style.RESET}")
    print(f"  Accelerator: {job.accelerator}")
    print(f"  Created At:  {job.created_at}")
    print(f"  Snippet:     {job.last_log_snippet or '(no log output)'}")
    if job.output_dir:
        print(f"  Outputs:     {job.output_dir}")
    print()
    return 0


def _resolve_job_id(args: argparse.Namespace, runner: KaggleRunner) -> str | None:
    """Job id from args.job (id or kernel slug), or the latest job. None if absent."""
    if not args.job:
        jobs = runner.list_jobs(limit=1)
        return jobs[0].id if jobs else None
    job = runner.get_job(args.job)
    return job.id if job else None


def cmd_run_logs(args: argparse.Namespace, runner: KaggleRunner) -> int:
    job_id = _resolve_job_id(args, runner)
    if not job_id:
        return fail(args, "", f"Job '{args.job}' not found." if args.job else "No jobs found.")

    logs = runner.get_logs(job_id, follow=args.follow)
    if args.json:
        job = runner.get_job(job_id)
        return emit({"job_id": job_id, "status": job.status if job else None, "logs": logs})
    print(logs)
    return 0


def cmd_run_download(args: argparse.Namespace, runner: KaggleRunner) -> int:
    job_id = _resolve_job_id(args, runner)
    if not job_id:
        return fail(args, "", f"Job '{args.job}' not found." if args.job else "No jobs found.")

    try:
        out_dir = runner.download_outputs(job_id, output_dir=args.path)
    except Exception as e:
        return fail(args, "Download failed", e)
    if args.json:
        files = sorted(str(p.relative_to(out_dir)) for p in Path(out_dir).rglob("*") if p.is_file())
        return emit({"success": True, "job_id": job_id, "output_dir": out_dir, "files": files})
    print(f"{Style.GREEN}[+] Artifacts downloaded to:{Style.RESET} {out_dir}")
    return 0


def cmd_run_list(args: argparse.Namespace, runner: KaggleRunner) -> int:
    limit = 100_000 if args.all else (args.limit or 20)
    jobs = runner.list_jobs(limit=limit)

    if args.json:
        return emit({"jobs": [j.to_dict() for j in jobs]})

    if not jobs:
        print(f"{Style.YELLOW}No execution jobs recorded yet.{Style.RESET}")
        return 0

    headers = ["Job ID", "Kernel / Title", "Account", "Accelerator", "Status", "Started", "Outputs"]
    rows = []
    for j in jobs:
        st_color = Style.GREEN if j.status in [JobStatus.COMPLETE, JobStatus.DOWNLOADED] else (
            Style.RED if j.status == JobStatus.ERROR else Style.YELLOW
        )
        started_str = j.started_at[:16].replace("T", " ") if j.started_at else "-"
        outputs_str = "Yes" if j.output_dir else "-"
        rows.append([
            j.id,
            j.kernel_slug[:30],
            j.username,
            j.accelerator,
            f"{st_color}{j.status}{Style.RESET}",
            started_str,
            outputs_str,
        ])

    print(f"\n{Style.BOLD}Recent KaggleFleet Jobs ({len(jobs)}):{Style.RESET}\n")
    print(format_table(headers, rows))
    print()
    return 0


# -------------------------------------------------------------------------
# Serve Subcommand
# -------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    from .server.http_server import run_server

    host = args.host or "127.0.0.1"
    port = args.port or 8080

    print(f"{Style.BOLD}Starting KaggleFleet web panel on http://{host}:{port}{Style.RESET}")
    try:
        return run_server(host=host, port=port, open_browser=args.open,
                          notebooks_dir=args.notebooks_dir, outputs_dir=args.outputs_dir,
                          allowed_hosts=args.allow_host)
    except OSError as e:
        hint = " Is another server using it? Try --port." if e.errno in (48, 98, 10048) else ""
        return fail(args, f"Could not listen on {host}:{port}", f"{e.strerror or e}.{hint}")


# -------------------------------------------------------------------------
# Doctor Subcommand
# -------------------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace, mgr: AccountManager, nb_mgr: NotebookManager,
               runner: KaggleRunner) -> int:
    """Check the environment: Kaggle CLI, state dir, accounts, notebooks."""
    import shutil
    import subprocess

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "required": required})

    kbin = config.kaggle_bin()
    kpath = shutil.which(kbin)
    if not kpath:
        check("kaggle_cli", False,
              "`kaggle` not found. Install it with `pip install kaggle` or set KAGGLE_FLEET_KAGGLE_BIN.")
    else:
        try:
            proc = subprocess.run([kpath, "--version"], capture_output=True, text=True, timeout=30)
            out = (proc.stdout or proc.stderr).strip()
            if proc.returncode == 0:
                check("kaggle_cli", True, f"{out} ({kpath})")
            else:
                last = out.splitlines()[-1] if out else f"exit code {proc.returncode}"
                check("kaggle_cli", False, f"{kpath} is installed but fails to run: {last}")
        except Exception as e:  # pragma: no cover - depends on host
            check("kaggle_cli", False, f"{kpath} could not be executed: {e}")
    check("state_dir", mgr.base_dir.is_dir(), str(mgr.base_dir))

    accounts = mgr.list_accounts()
    active = mgr.get_active_account()
    check("accounts", bool(accounts), f"{len(accounts)} configured" if accounts else
          "No accounts yet. Run `kfleet account login` or `kfleet account add`.")
    check("active_account", active is not None,
          f"{active.username} ({active.id})" if active else "No active account.")
    notebooks = nb_mgr.list_notebooks()
    check("notebooks", bool(notebooks),
          f"{len(notebooks)} found in {nb_mgr.notebooks_dir}", required=False)
    check("outputs_dir", True, str(runner.outputs_dir), required=False)

    healthy = all(c["ok"] for c in checks if c["required"])
    if args.json:
        emit({"healthy": healthy, "checks": checks})
        return 0 if healthy else 1
    print(f"\n{Style.BOLD}KaggleFleet doctor{Style.RESET}\n")
    for c in checks:
        mark = (f"{Style.GREEN}ok  {Style.RESET}" if c["ok"] else
                f"{Style.RED}FAIL{Style.RESET}" if c["required"] else f"{Style.YELLOW}warn{Style.RESET}")
        print(f"  [{mark}] {c['check']:<15} {c['detail']}")
    print()
    return 0 if healthy else 1


# -------------------------------------------------------------------------
# Main Argument Parser
# -------------------------------------------------------------------------

def _common_options() -> argparse.ArgumentParser:
    """Global options, accepted before or after the subcommand
    (`kfleet --json run list` and `kfleet run list --json` both work)."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="Machine-readable JSON output (for scripts and AI agents)")
    common.add_argument("--notebooks-dir", default=argparse.SUPPRESS, metavar="DIR",
                        help="Where to find notebooks (default: $KAGGLE_FLEET_NOTEBOOKS_DIR, "
                             "./notebooks, or the current directory)")
    common.add_argument("--outputs-dir", default=argparse.SUPPRESS, metavar="DIR",
                        help="Where to download outputs (default: $KAGGLE_FLEET_OUTPUTS_DIR "
                             "or ./outputs)")
    return common


_COMMON = _common_options()


def _add(subparsers: Any, name: str, **kwargs: Any) -> argparse.ArgumentParser:
    """add_parser() that makes every subcommand accept the global options."""
    return subparsers.add_parser(name, parents=[_COMMON], **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kfleet",
        description=f"KaggleFleet v{__version__} - run local notebooks on Kaggle GPUs "
                    "across a team of Kaggle accounts",
        parents=[_COMMON],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Command category")
    _build_subcommands(subparsers)
    return parser


def _build_subcommands(subparsers: Any) -> None:

    # 1. account
    p_acc = _add(subparsers, "account", help="Multi-account credential management")
    acc_sub = p_acc.add_subparsers(dest="subcommand", help="Account action")

    # account list
    p_acc_list = _add(acc_sub, "list", help="List all configured accounts")
    p_acc_list.add_argument(
        "--refresh-usage", action="store_true",
        help="Read run times from Kaggle logs for finished jobs this quota week "
             "(one API call per job that lacks it)",
    )

    # account add
    p_acc_add = _add(acc_sub, "add", help="Add an API key account")
    p_acc_add.add_argument("--username", "-u", required=True, help="Kaggle username")
    p_acc_add.add_argument("--key", "-k", required=True, help="Kaggle API Key")
    p_acc_add.add_argument("--label", "-l", help="Descriptive label (e.g. 'CoAuthor GPU')")
    p_acc_add.add_argument("--active", action="store_true", help="Set as active immediately")
    p_acc_add.add_argument("--quota-notes", help="Quota notes (e.g. '30h weekly GPU')")
    p_acc_add.add_argument("--notes", help="Optional notes")

    # account add-oauth
    p_acc_oauth = _add(acc_sub, "add-oauth", help="Add an account from credentials.json")
    p_acc_oauth.add_argument("--credentials-file", "-f", required=True, help="Path to credentials.json")
    p_acc_oauth.add_argument("--label", "-l", help="Descriptive label")
    p_acc_oauth.add_argument("--active", action="store_true", help="Set as active immediately")
    p_acc_oauth.add_argument("--quota-notes", help="Quota notes")
    p_acc_oauth.add_argument("--notes", help="Optional notes")

    # account switch
    p_acc_login = _add(acc_sub,
        "login", help="Add an account via browser sign-in (sandboxed `kaggle auth login`)")
    p_acc_login.add_argument("--label", "-l", help="Descriptive label")
    p_acc_login.add_argument("--active", action="store_true", help="Set as active immediately")
    p_acc_login.add_argument("--no-browser", action="store_true",
                             help="Print the sign-in URL and paste the code instead")

    p_acc_sw = _add(acc_sub, "switch", help="Switch active Kaggle account")
    p_acc_sw.add_argument("account", help="Account ID or Username to activate")

    # account test
    p_acc_test = _add(acc_sub, "test", help="Test credentials with Kaggle CLI")
    p_acc_test.add_argument("account", nargs="?", help="Account ID or Username (defaults to active)")

    # account remove
    p_acc_rm = _add(acc_sub, "remove", help="Remove an account")
    p_acc_rm.add_argument("account", help="Account ID or Username to remove")

    # account import
    _add(acc_sub, "import", help="Auto-import ~/.kaggle/kaggle.json or credentials.json")

    # 2. notebook
    p_nb = _add(subparsers, "notebook", help="Local notebook discovery & inspection")
    nb_sub = p_nb.add_subparsers(dest="subcommand", help="Notebook action")

    # notebook list
    p_nb_list = _add(nb_sub, "list", help="List discovered notebooks")
    p_nb_list.add_argument("--path", "-p", help="Directory to scan (defaults to --notebooks-dir)")

    # notebook inspect
    p_nb_insp = _add(nb_sub, "inspect", help="Inspect notebook structure & metadata")
    p_nb_insp.add_argument("notebook", help="Notebook filename or path")

    # 3. run
    p_run = _add(subparsers, "run", help="Execution, push, status, logs & downloads")
    run_sub = p_run.add_subparsers(dest="subcommand", help="Run action")

    # run push
    p_run_push = _add(run_sub, "push", help="Push notebook to Kaggle for execution")
    p_run_push.add_argument("notebook", help="Notebook filename or path")
    p_run_push.add_argument("--account", "-a", help="Account ID or Username (defaults to active)")
    p_run_push.add_argument("--title", "-t", help="Kernel title on Kaggle")
    p_run_push.add_argument("--slug", "-s", help="Kernel slug")
    p_run_push.add_argument(
        "--accelerator",
        choices=["NvidiaTeslaT4", "NvidiaTeslaP100", "TpuV5E8", "None"],
        default="NvidiaTeslaT4",
        help="Hardware accelerator (default: NvidiaTeslaT4)",
    )
    p_run_push.add_argument("--no-gpu", action="store_true", help="Run on standard CPU")
    p_run_push.add_argument("--no-internet", action="store_true", help="Disable internet access")
    p_run_push.add_argument(
        "--max-runtime", type=int, default=None,
        help="Kill the kernel on Kaggle after this many seconds (default: Kaggle's 12h limit)")
    p_run_push.add_argument(
        "--timeout", type=int, default=12 * 3600,
        help="How long --watch keeps polling, in seconds (the kernel is unaffected)")
    p_run_push.add_argument("--dataset", action="append", metavar="OWNER/SLUG",
                            help="Attach a dataset (repeatable)")
    p_run_push.add_argument("--competition", action="append", metavar="SLUG",
                            help="Attach a competition's data (repeatable)")
    p_run_push.add_argument("--kernel-source", action="append", metavar="OWNER/SLUG",
                            help="Attach another kernel's output (repeatable)")
    p_run_push.add_argument("--model", action="append", metavar="OWNER/MODEL/FRAMEWORK/VARIATION/VERSION",
                            help="Attach a Kaggle model (repeatable)")
    p_run_push.add_argument("--watch", "-w", action="store_true", help="Stream logs until completion")
    p_run_push.add_argument("--auto-download", action="store_true", default=True, help="Auto download outputs")
    p_run_push.add_argument("--no-download", dest="auto_download", action="store_false",
                            help="Do not download outputs after --watch completes")

    # run status
    p_run_st = _add(run_sub, "status", help="Check status of a job")
    p_run_st.add_argument("job", nargs="?", help="Job ID or Kernel Slug (defaults to latest)")

    # run logs
    p_run_lg = _add(run_sub, "logs", help="Fetch or stream logs for a job")
    p_run_lg.add_argument("job", nargs="?", help="Job ID (defaults to latest)")
    p_run_lg.add_argument("--follow", "-f", action="store_true", help="Follow logs")

    # run download
    p_run_dl = _add(run_sub, "download", help="Download output artifacts")
    p_run_dl.add_argument("job", nargs="?", help="Job ID (defaults to latest)")
    p_run_dl.add_argument("--path", "-p", help="Custom output directory")

    # run list
    p_run_ls = _add(run_sub, "list", help="List past execution jobs")
    p_run_ls.add_argument("--all", action="store_true", help="Show all jobs")
    p_run_ls.add_argument("--limit", "-n", type=int, default=20, help="Max jobs to show")

    # 4. serve
    p_srv = _add(subparsers, "serve", help="Launch Modern Web UI Dashboard")
    p_srv.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    p_srv.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    p_srv.add_argument("--open", action="store_true", help="Open browser on start")
    p_srv.add_argument("--allow-host", action="append", metavar="HOST",
                       help="Extra hostname the panel may be reached as (repeatable), "
                            "e.g. when behind a reverse proxy")

    # 5. doctor
    _add(subparsers, "doctor", help="Check Kaggle CLI, state dir, accounts and notebooks")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    for name, default in (("json", False), ("notebooks_dir", None), ("outputs_dir", None)):
        if not hasattr(args, name):
            setattr(args, name, default)

    if not args.command:
        parser.print_help()
        return 0

    storage = StorageEngine()
    acc_mgr = AccountManager(storage=storage)
    nb_mgr = NotebookManager(notebooks_dir=Path(args.notebooks_dir) if args.notebooks_dir else None)
    runner = KaggleRunner(account_manager=acc_mgr, notebook_manager=nb_mgr, storage=storage,
                          outputs_dir=args.outputs_dir)

    if args.command == "account":
        if not args.subcommand or args.subcommand == "list":
            return cmd_account_list(args, acc_mgr)
        elif args.subcommand == "add":
            return cmd_account_add(args, acc_mgr)
        elif args.subcommand == "add-oauth":
            return cmd_account_add_oauth(args, acc_mgr)
        elif args.subcommand == "login":
            return cmd_account_login(args, acc_mgr)
        elif args.subcommand == "switch":
            return cmd_account_switch(args, acc_mgr)
        elif args.subcommand == "test":
            return cmd_account_test(args, acc_mgr)
        elif args.subcommand == "remove":
            return cmd_account_remove(args, acc_mgr)
        elif args.subcommand == "import":
            return cmd_account_import(args, acc_mgr)
        else:
            print(f"Unknown account subcommand: {args.subcommand}", file=sys.stderr)
            return 1

    elif args.command == "notebook":
        if not args.subcommand or args.subcommand == "list":
            return cmd_notebook_list(args, nb_mgr)
        elif args.subcommand == "inspect":
            return cmd_notebook_inspect(args, nb_mgr)
        else:
            print(f"Unknown notebook subcommand: {args.subcommand}", file=sys.stderr)
            return 1

    elif args.command == "run":
        if not args.subcommand or args.subcommand == "list":
            return cmd_run_list(args, runner)
        elif args.subcommand == "push":
            return cmd_run_push(args, runner)
        elif args.subcommand == "status":
            return cmd_run_status(args, runner)
        elif args.subcommand == "logs":
            return cmd_run_logs(args, runner)
        elif args.subcommand == "download":
            return cmd_run_download(args, runner)
        else:
            print(f"Unknown run subcommand: {args.subcommand}", file=sys.stderr)
            return 1

    elif args.command == "serve":
        return cmd_serve(args)

    elif args.command == "doctor":
        return cmd_doctor(args, acc_mgr, nb_mgr, runner)

    return 0
