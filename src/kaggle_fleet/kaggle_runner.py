"""
Headless Kaggle kernel runner, status monitor, and output downloader.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import config
from .account_manager import AccountManager
from .models import JobRecord, JobStatus, RunOptions, utc_now_iso
from .notebook_manager import NotebookManager
from .storage import StorageEngine

_FINISHED = (JobStatus.COMPLETE, JobStatus.DOWNLOADED, JobStatus.ERROR, JobStatus.CANCELLED)
_LOG_TIME = re.compile(r'"time"\s*:\s*([0-9]+(?:\.[0-9]+)?)')


def empty_usage() -> dict[str, Any]:
    return {"gpu_hours": 0.0, "tpu_hours": 0.0, "active_jobs": 0}


def accelerator_kind(job: JobRecord) -> str | None:
    """'gpu', 'tpu' or None (CPU-only run) -- Kaggle meters GPU and TPU separately."""
    acc = (job.accelerator or "").lower()
    if acc.startswith("tpu"):
        return "tpu"
    if not job.enable_gpu or acc in ("", "none"):
        return None
    return "gpu"


def quota_info(now: Any = None) -> dict[str, Any]:
    """When the current Kaggle quota week started and when it next resets (UTC)."""
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    start, reset = config.quota_window(now)
    return {
        "quota_window_start": start.isoformat(),
        "quota_reset_at": reset.isoformat(),
        "quota_resets_in_seconds": int((reset - now).total_seconds()),
    }


def runtime_from_logs(logs: str) -> float | None:
    """
    Execution time in seconds from `kaggle kernels logs` output.

    Each log entry carries "time": seconds since the session started, so the
    largest one is how long the kernel ran (queue time is excluded).
    """
    times = [float(m) for m in _LOG_TIME.findall(logs or "")]
    return max(times) if times else None


class KaggleRunner:
    """Manages Kaggle CLI execution, tracking job status, logs, and artifacts."""

    def __init__(
        self,
        account_manager: AccountManager | None = None,
        notebook_manager: NotebookManager | None = None,
        storage: StorageEngine | None = None,
        outputs_dir: str | Path | None = None,
    ) -> None:
        self.storage = storage or StorageEngine()
        self.outputs_dir = Path(outputs_dir or config.DEFAULT_OUTPUTS_DIR).resolve()
        self.account_manager = account_manager or AccountManager(storage=self.storage)
        self.notebook_manager = notebook_manager or NotebookManager()
        self._lock = threading.RLock()

    def list_jobs(self, account_id: str | None = None, limit: int = 50) -> list[JobRecord]:
        """List past and current jobs, newest first."""
        with self._lock:
            doc = self.storage.load_jobs()
            jobs = [JobRecord.from_dict(j) for j in doc.get("jobs", [])]
            if account_id:
                jobs = [j for j in jobs if j.account_id == account_id]
            jobs.sort(key=lambda j: j.created_at or "", reverse=True)
            return jobs[:limit]

    def gpu_usage_by_account(self, now: Any = None) -> dict[str, dict[str, Any]]:
        """
        Accelerator hours per account in the current Kaggle quota week.

        Kaggle's weekly quota resets at a fixed moment (see
        `config.quota_window`), so only time since the last reset counts.
        A run's length is the execution time from its Kaggle logs when known
        (`runtime_seconds`); otherwise it is push -> observed end, capped at
        Kaggle's 12 h session limit, so a hub that was offline when a run
        finished can no longer bill it for the whole gap. GPU and TPU quotas
        are separate. Only jobs launched through KaggleFleet are counted.
        """
        from datetime import datetime, timedelta, timezone

        now = now or datetime.now(timezone.utc)
        since, _ = config.quota_window(now)
        cap = timedelta(seconds=config.KAGGLE_SESSION_LIMIT_SECONDS)
        usage: dict[str, dict[str, Any]] = {}

        def parse(ts: str | None):
            if not ts:
                return None
            try:
                return datetime.fromisoformat(ts)
            except ValueError:
                return None

        for job in self.list_jobs(limit=10_000):
            u = usage.setdefault(job.account_id, empty_usage())
            if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                u["active_jobs"] += 1
            kind = accelerator_kind(job)
            if kind is None:
                continue
            start = parse(job.started_at)
            if start is None:
                continue
            if job.runtime_seconds is not None:
                end = start + timedelta(seconds=max(0.0, float(job.runtime_seconds)))
                observed = parse(job.completed_at)
                if observed is not None:
                    # The hub sees a run end at or after its real end, so a
                    # longer log time belongs to some other run.
                    end = min(end, observed)
            elif job.status == JobStatus.QUEUED:
                continue                          # not using quota yet
            else:
                end = parse(job.completed_at)
                if end is None:
                    if job.status != JobStatus.RUNNING:
                        continue                  # finished but never observed
                    end = now
            end = min(end, start + cap, now)
            start = max(start, since)
            if end > start:
                u[f"{kind}_hours"] += (end - start).total_seconds() / 3600
        for u in usage.values():
            u["gpu_hours"] = round(u["gpu_hours"], 2)
            u["tpu_hours"] = round(u["tpu_hours"], 2)
        return usage

    def refresh_runtimes(self) -> int:
        """
        Fill in `runtime_seconds` from Kaggle logs for finished accelerator
        jobs in the current quota week that don't have it yet. Makes one
        `kaggle kernels logs` call per such job. Returns how many were updated.
        """
        since, _ = config.quota_window()
        updated = 0
        jobs = self.list_jobs(limit=10_000)          # newest first
        latest = {}
        for job in jobs:
            latest.setdefault(job.kernel_id, job.id)
        for job in jobs:
            # `kernels logs` returns the newest version's logs only, so a job
            # that was re-pushed under the same slug can't be measured.
            if (job.runtime_seconds is not None or not job.started_at
                    or latest[job.kernel_id] != job.id
                    or accelerator_kind(job) is None
                    or job.status not in _FINISHED
                    or (job.completed_at or job.started_at) < since.isoformat()):
                continue
            if self._record_runtime(job):
                self.save_job(job)
                updated += 1
        return updated

    def _record_runtime(self, job: JobRecord, logs: str | None = None) -> bool:
        """Set job.runtime_seconds from its Kaggle logs. Returns True if set."""
        if logs is None:
            account = self.account_manager.get_account(job.account_id)
            if not account:
                return False
            try:
                proc = self.account_manager.run_kaggle(
                    account, ["kernels", "logs", job.kernel_id], timeout=60,
                )
            except Exception:
                return False
            if proc.returncode != 0:
                return False
            logs = proc.stdout or ""
        runtime = runtime_from_logs(logs)
        if runtime is None:
            return False
        job.runtime_seconds = round(runtime, 1)
        return True

    def get_job(self, job_id: str) -> JobRecord | None:
        """Get a single job record by ID."""
        with self._lock:
            doc = self.storage.load_jobs()
            for j in doc.get("jobs", []):
                if j.get("id") == job_id or j.get("kernel_slug") == job_id:
                    return JobRecord.from_dict(j)
            return None

    def save_job(self, job: JobRecord) -> None:
        """Persist updated job record to storage."""
        with self._lock, self.storage.transaction():
            doc = self.storage.load_jobs()
            jobs_list = doc.get("jobs", [])
            updated = False
            for idx, item in enumerate(jobs_list):
                if item.get("id") == job.id:
                    jobs_list[idx] = job.to_dict()
                    updated = True
                    break
            if not updated:
                jobs_list.append(job.to_dict())
            doc["jobs"] = jobs_list
            self.storage.save_jobs(doc)

    def submit_job(self, options: RunOptions) -> JobRecord:
        """Prepare staging directory and register a new queued job."""
        account = None
        if options.account_id:
            account = self.account_manager.get_account(options.account_id)
        if not account:
            account = self.account_manager.get_active_account()

        if not account:
            raise ValueError(
                "No Kaggle account found. Please add or activate an account first."
            )

        # Inspect notebook to obtain slug and title
        nb_info, _ = self.notebook_manager.inspect_notebook(options.notebook)
        slug = options.slug or nb_info.slug
        title = options.title or nb_info.title
        kernel_id = f"{account.username}/{slug}"

        job_ts = time.strftime("%Y%m%d_%H%M%S")
        job_id = f"job_{job_ts}_{slug[:12]}_{uuid.uuid4().hex[:4]}"

        # Prepare staging
        staging_path = self.notebook_manager.prepare_staging(
            job_id=job_id,
            notebook_path=nb_info.path,
            options=options,
            account=account,
        )

        accelerator = config.normalize_accelerator(options.accelerator)

        job = JobRecord(
            id=job_id,
            account_id=account.id,
            username=account.username,
            kernel_slug=slug,
            kernel_id=kernel_id,
            title=title,
            notebook_path=nb_info.path,
            staging_path=str(staging_path),
            accelerator=accelerator,
            enable_gpu=options.enable_gpu,
            enable_internet=options.enable_internet,
            dataset_sources=options.dataset_sources,
            competition_sources=options.competition_sources,
            kernel_sources=options.kernel_sources,
            model_sources=options.model_sources,
            status=JobStatus.QUEUED,
            created_at=utc_now_iso(),
        )

        self.save_job(job)
        return job

    def push_job(
        self,
        job_id: str,
        timeout: int | None = None,
        accelerator: str | None = None,
    ) -> JobRecord:
        """
        Push prepared staging directory to Kaggle via `kaggle kernels push`.

        `timeout` is passed as `kernels push -t`, which is the kernel's maximum
        RUN TIME on Kaggle, not a network timeout. It is omitted unless given,
        so runs get Kaggle's full session limit (12h).
        """
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job '{job_id}' not found")

        account = self.account_manager.get_account(job.account_id)
        if not account:
            raise ValueError(f"Account '{job.account_id}' for job not found")

        target_acc = accelerator or job.accelerator
        target_acc = config.normalize_accelerator(target_acc)

        cmd = ["kernels", "push", "-p", str(job.staging_path)]
        if target_acc and target_acc != "None":
            cmd.extend(["--accelerator", target_acc])
        if timeout:
            cmd.extend(["-t", str(int(timeout))])

        try:
            proc = self.account_manager.run_kaggle(account, cmd, timeout=300)

            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            # The Kaggle CLI exits 0 on rejected pushes and reports them on
            # stdout ("Kernel push error: Maximum batch GPU session count of 2
            # reached."). Trusting the exit code records them as running.
            rejected = "push error" in stdout.lower()

            if proc.returncode == 0 and not rejected:
                job.status = JobStatus.RUNNING
                job.started_at = utc_now_iso()
                job.exit_code = 0
                job.error_message = None
                job.last_log_snippet = stdout
            else:
                job.status = JobStatus.ERROR
                job.exit_code = proc.returncode
                job.error_message = stderr or stdout
                job.last_log_snippet = stderr or stdout

        except Exception as e:
            job.status = JobStatus.ERROR
            job.error_message = f"Failed to execute push: {str(e)}"

        self.save_job(job)
        return job

    def check_status(self, job_id: str) -> JobRecord:
        """Query Kaggle for latest kernel status via `kaggle kernels status`."""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job '{job_id}' not found")

        account = self.account_manager.get_account(job.account_id)
        if not account:
            return job

        try:
            proc = self.account_manager.run_kaggle(
                account, ["kernels", "status", job.kernel_id], timeout=30,
            )

            output = (proc.stdout or proc.stderr or "").strip()
            lower = output.lower()

            # A failed query (network outage, 404, rate limit) says nothing
            # about the kernel. Parsing its text for "error" used to mark live
            # runs as ERROR, which made the scheduler re-push a running kernel.
            if proc.returncode != 0:
                job.last_log_snippet = f"Status check failed: {output[:300]}"
                self.save_job(job)
                return job

            if "complete" in lower or "has completed" in lower:
                job.status = JobStatus.COMPLETE
                if not job.completed_at:
                    job.completed_at = utc_now_iso()
            elif "running" in lower or "is running" in lower:
                job.status = JobStatus.RUNNING
            elif "queue" in lower or "is queued" in lower:
                job.status = JobStatus.QUEUED
            elif "fail" in lower or "error" in lower:
                job.status = JobStatus.ERROR
                job.error_message = output
                if not job.completed_at:
                    job.completed_at = utc_now_iso()
            elif "cancel" in lower:
                job.status = JobStatus.CANCELLED
                if not job.completed_at:
                    job.completed_at = utc_now_iso()

            job.last_log_snippet = output
            if job.status in _FINISHED and job.runtime_seconds is None:
                self._record_runtime(job)
            self.save_job(job)

        except Exception as e:
            job.last_log_snippet = f"Status check error: {e}"
            self.save_job(job)

        return job

    def get_logs(self, job_id: str, follow: bool = False) -> str:
        """Fetch kernel logs via `kaggle kernels logs`."""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job '{job_id}' not found")

        account = self.account_manager.get_account(job.account_id)
        if not account:
            raise ValueError(f"Account '{job.account_id}' not found")

        cmd = ["kernels", "logs", job.kernel_id]
        if follow:
            cmd.append("-f")

        try:
            proc = self.account_manager.run_kaggle(account, cmd, timeout=60)
            logs = proc.stdout or proc.stderr or ""
            # Update last snippet
            lines = logs.strip().splitlines()
            if lines:
                job.last_log_snippet = "\n".join(lines[-10:])
                if job.status in _FINISHED and job.runtime_seconds is None:
                    self._record_runtime(job, logs)
                self.save_job(job)
            return logs
        except Exception as e:
            return f"Error fetching logs: {e}"

    def download_outputs(
        self,
        job_id: str,
        output_dir: str | Path | None = None,
    ) -> str:
        """Download output files from Kaggle to local disk."""
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job '{job_id}' not found")

        account = self.account_manager.get_account(job.account_id)
        if not account:
            raise ValueError(f"Account '{job.account_id}' not found")

        target_dir = Path(output_dir or self.outputs_dir / job.kernel_slug).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        proc = self.account_manager.run_kaggle(
            account, ["kernels", "output", job.kernel_id, "-p", str(target_dir), "-o"],
            timeout=1800,
        )

        if proc.returncode == 0:
            job.output_dir = str(target_dir)
            job.status = JobStatus.DOWNLOADED
            self.save_job(job)
            return str(target_dir)
        else:
            err = proc.stderr or proc.stdout
            raise RuntimeError(f"Failed to download outputs: {err}")

    def run_job_sync(
        self,
        job_id: str,
        watch: bool = False,
        auto_download: bool = True,
        poll_interval: int = 10,
        timeout: int = 12 * 3600,
        push: bool = True,
    ) -> JobRecord:
        """
        Optionally push, then optionally monitor the job until completion.

        Pass push=False for a job that was already pushed: every push starts
        a NEW run on Kaggle and spends GPU quota again. `timeout` only bounds
        how long this process watches; the kernel keeps running regardless.
        """
        if push:
            job = self.push_job(job_id)
        else:
            job = self.get_job(job_id)
            if job is None:
                raise ValueError(f"Job '{job_id}' not found")
        if job.status == JobStatus.ERROR or not watch:
            return job

        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(poll_interval)
            job = self.check_status(job_id)
            if job.status in [JobStatus.COMPLETE, JobStatus.ERROR, JobStatus.CANCELLED]:
                break

        if auto_download and job.status == JobStatus.COMPLETE:
            try:
                self.download_outputs(job_id)
                job = self.get_job(job_id) or job
            except Exception:
                pass

        return job

    def run_job_async(
        self,
        job_id: str,
        auto_download: bool = True,
        poll_interval: int = 10,
        timeout: int = 12 * 3600,
        push: bool = False,
    ) -> threading.Thread:
        """
        Monitor (and, with push=True, first push) a job in a daemon thread.

        Defaults to push=False: callers push synchronously first so they can
        report push errors, and a second push would start a duplicate run.
        """
        thread = threading.Thread(
            target=self.run_job_sync,
            args=(job_id, True, auto_download, poll_interval, timeout, push),
            daemon=True,
        )
        thread.start()
        return thread
