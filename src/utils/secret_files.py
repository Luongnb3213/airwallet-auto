"""Writing and reaping files that hold credentials.

Two problems this addresses.

**Permissions.** `Path.write_text()` creates a file with the process umask
(0644 on POSIX: readable by every user on the machine). A file holding a
4-digit financial PIN should not be.

**Files that outlive their cleanup.** `scripts/airwallet_batch_flow.py` kills a
hung row with ``os.killpg(..., SIGKILL)``. SIGKILL cannot be caught, so a
``finally:`` block never runs and any temp file holding a PIN survives
indefinitely. The real fix is to not write the secret at all -- see
``scripts/airwallet_full_flow.py``, which now passes the profile in memory --
but a sweeper is still needed to reclaim what older runs left behind.

Platform note, stated plainly: the 0o600 mode is enforced by the kernel on
POSIX. On Windows ``os.chmod`` only toggles the read-only attribute and the file
otherwise inherits the directory's ACL, so the mode argument alone is advisory
there. :func:`restrict_to_owner` therefore drops inheritance and grants the
current user alone via ``icacls`` on Windows. It reports whether a real
restriction was applied, so a caller can warn instead of assuming.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import time

#: Owner read/write only.
PRIVATE_MODE = 0o600


def restrict_to_owner(path: Path) -> bool:
    """Limit ``path`` to its owner. Returns True only if that was enforced.

    POSIX: ``chmod 0600``. Windows: remove inherited ACEs and grant the current
    user full control, which is what actually governs access there.
    """
    path = Path(path)
    try:
        os.chmod(str(path), PRIVATE_MODE)
    except OSError:
        if os.name == "posix":
            return False
    if os.name != "nt":
        return True
    account = os.environ.get("USERNAME") or ""
    if not account:
        return False
    domain = os.environ.get("USERDOMAIN") or ""
    principal = f"{domain}\\{account}" if domain else account
    try:
        completed = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(R,W)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def write_private_bytes(path: Path, payload: bytes) -> Path:
    """Write ``payload`` to ``path``, creating it owner-only from the start.

    The file is opened with ``O_CREAT | O_EXCL``-like intent via an explicit
    mode so it is never briefly world-readable, which is what would happen if
    we wrote first and chmod'ed after.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_MODE)
    try:
        handle = os.fdopen(descriptor, "wb")
    except BaseException:
        os.close(descriptor)  # fdopen did not take ownership, so close it ourselves
        raise
    with handle:
        handle.write(payload)
    restrict_to_owner(path)
    return path


def write_private_json(path: Path, data, *, indent: int | None = None) -> Path:
    """Serialise ``data`` as UTF-8 JSON into an owner-only file."""
    payload = json.dumps(data, ensure_ascii=False, indent=indent).encode("utf-8")
    return write_private_bytes(Path(path), payload)


def is_owner_only(path: Path) -> bool:
    """Whether ``path`` is readable only by its owner, as far as we can tell.

    POSIX: the mode bits. Windows: parse ``icacls`` output and report whether
    any ACE grants a principal other than the current user or the local
    Administrators group. Returns False when it cannot be determined, so the
    caller errs toward warning.
    """
    path = Path(path)
    if os.name == "posix":
        try:
            import stat as stat_module
            return stat_module.S_IMODE(path.stat().st_mode) & 0o077 == 0
        except OSError:
            return False
    account = (os.environ.get("USERNAME") or "").lower()
    try:
        completed = subprocess.run(["icacls", str(path)], capture_output=True, text=True,
                                   timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    # icacls prints "<path> PRINCIPAL:(perms)" then further "PRINCIPAL:(perms)"
    # lines. The path itself contains a colon ("C:\..."), so drop it before
    # looking for principals rather than splitting the line on ":".
    output = completed.stdout.replace(str(path), " ")
    principals = re.findall(r"([A-Za-z0-9 _.\-]+(?:\\[A-Za-z0-9 _.\-]+)*):\(", output)
    if not principals:
        return False
    allowed = {account, "administrators", "system"}
    for principal in principals:
        name = principal.strip().split("\\")[-1].strip().lower()
        if name and name not in allowed:
            return False
    return True


def reap_stale(directory: Path, pattern: str, max_age_seconds: float,
               *, now: float | None = None) -> list[str]:
    """Delete files matching ``pattern`` older than ``max_age_seconds``.

    Returns the names removed, so the caller can log that a previous run left
    credentials behind -- that fact is worth a log line. Never raises: a sweeper
    that breaks the run it is protecting is not an improvement.
    """
    directory = Path(directory)
    removed: list[str] = []
    if max_age_seconds <= 0 or not directory.is_dir():
        return removed
    moment = time.time() if now is None else float(now)
    try:
        candidates = sorted(directory.glob(pattern))
    except OSError:
        return removed
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            if (moment - candidate.stat().st_mtime) < max_age_seconds:
                continue
            candidate.unlink()
        except OSError:
            continue
        removed.append(candidate.name)
    return removed
