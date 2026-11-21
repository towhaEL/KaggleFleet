"""
Thread- and process-safe JSON storage engine for KaggleFleet.

Strict security permissions: directories 0700, credential files 0600.
The CLI, the web server and its watcher threads may all write at once, so
read-modify-write sequences run inside `transaction()`, which holds both a
thread lock and an advisory file lock (fcntl) on `<base_dir>/.lock`.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from collections.abc import Iterator

try:  # POSIX only; on other platforms the file lock is a no-op
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from . import config


class StorageEngine:
    """Thread-safe persistent JSON store for accounts and jobs history."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(base_dir or config.KAGGLE_FLEET_DIR).resolve()
        self.accounts_file = self.base_dir / "accounts.json"
        self.jobs_file = self.base_dir / "jobs.json"
        self._lock = threading.RLock()
        self._depth = 0
        self._lock_fh: Any = None
        self._ensure_init()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Exclusive, re-entrant lock across threads AND processes sharing base_dir."""
        with self._lock:
            if self._depth == 0 and fcntl is not None:
                self._lock_fh = open(self.base_dir / ".lock", "a+")
                fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX)
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if self._depth == 0 and self._lock_fh is not None:
                    try:
                        fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
                    finally:
                        self._lock_fh.close()
                        self._lock_fh = None

    def _ensure_init(self) -> None:
        """Create base directory with strict 0700 permissions."""
        with self._lock:
            self.base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Ensure permissions are 0700 even if directory previously existed
            try:
                os.chmod(self.base_dir, 0o700)
            except OSError:
                pass

    def _atomic_write(self, file_path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
        """Atomically write JSON data using a temporary file and atomic rename."""
        file_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp_name = f".{file_path.name}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_path = file_path.parent / tmp_name

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            try:
                os.chmod(tmp_path, mode)
            except OSError:
                pass

            os.replace(tmp_path, file_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _safe_read(self, file_path: Path, default_factory: Any) -> dict[str, Any]:
        """Safely read JSON data from file, falling back to default on error."""
        if not file_path.exists():
            return default_factory()
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return default_factory()
                return json.loads(content)
        except (json.JSONDecodeError, OSError):
            return default_factory()

    def load_accounts(self) -> dict[str, Any]:
        """Load accounts document."""
        with self._lock:
            return self._safe_read(
                self.accounts_file,
                lambda: {"active_account_id": None, "accounts": []},
            )

    def save_accounts(self, data: dict[str, Any]) -> None:
        """Save accounts document atomically with 0600 permissions."""
        with self._lock:
            self._atomic_write(self.accounts_file, data, mode=0o600)

    def load_jobs(self) -> dict[str, Any]:
        """Load jobs document."""
        with self._lock:
            return self._safe_read(
                self.jobs_file,
                lambda: {"jobs": []},
            )

    def save_jobs(self, data: dict[str, Any]) -> None:
        """Save jobs document atomically with 0600 permissions."""
        with self._lock:
            self._atomic_write(self.jobs_file, data, mode=0o600)
