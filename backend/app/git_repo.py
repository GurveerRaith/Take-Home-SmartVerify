"""Git storage for policy file content.

Git is the system of record for file content; PostgreSQL holds metadata. This
module is the only place that runs git commands against the policy repository.

Content is read back at a specific commit rather than from the working tree, so
a metadata row always resolves to the exact bytes that were stored with it.
See DESIGN.md decisions 1 and 5.
"""

import os
import subprocess
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = REPO_ROOT / "data" / "policy-repo"

# Git's index is a single file and is not safe for concurrent writers. FastAPI
# runs synchronous endpoints in a thread pool, so two uploads really can
# overlap. One lock around every write serialises them.
_write_lock = threading.Lock()


class GitError(RuntimeError):
    """A git command failed."""


def repo_path() -> Path:
    """Location of the policy repository.

    Read on every call rather than captured at import time, so tests can
    redirect it with POLICY_REPO_PATH after this module has been imported.
    """
    return Path(os.environ.get("POLICY_REPO_PATH", DEFAULT_REPO))


def build_path(customer_folder: str, tenant_folder: str, filename: str) -> str:
    """Path inside the repository for one tenant's file.

    The folder names come from the validated tenant scope and the filename has
    already been checked against the allowed pattern, so a file cannot be
    written outside its own tenant's directory.
    """
    return f"{customer_folder}/{tenant_folder}/{filename}"


def _git(*args: str) -> str:
    """Run a git command in the policy repository and return its output.

    The return code is checked by hand rather than with check=True, because
    CalledProcessError does not carry stderr and a git failure would surface as
    an exit status with no reason attached.
    """
    result = subprocess.run(
        ["git", *args], cwd=repo_path(), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def write_file(git_path: str, content: bytes, message: str) -> str:
    """Write a file, commit it, and return the commit SHA.

    Takes bytes rather than text so the stored content is exactly what was
    uploaded, which is what makes a download byte-identical.
    """
    with _write_lock:
        full_path = repo_path() / git_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        _git("add", "--", git_path)
        _git("commit", "-m", message)
        return _git("rev-parse", "HEAD").strip()


def read_file(commit_sha: str, git_path: str) -> bytes:
    """Read a file's content as it was at a specific commit.

    Reading at the commit recorded on the metadata row, rather than from the
    working tree, means the row and the content cannot drift apart -- and a
    file deleted from the working tree is still readable from history.
    """
    result = subprocess.run(
        ["git", "show", f"{commit_sha}:{git_path}"],
        cwd=repo_path(),
        capture_output=True,
    )
    if result.returncode != 0:
        raise GitError(
            f"cannot read {git_path} at {commit_sha}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def delete_file(git_path: str, message: str) -> str:
    """Remove a file from the working tree and commit the removal.

    The content stays in history, so a deleted policy file remains recoverable
    and the commit log remains a record of what was in place when.
    """
    with _write_lock:
        _git("rm", "--quiet", "--", git_path)
        _git("commit", "-m", message)
        return _git("rev-parse", "HEAD").strip()


def discard_last_commit() -> None:
    """Undo the most recent commit, discarding its changes.

    Best-effort cleanup for the case where the Git write succeeds but the
    metadata insert that follows it fails. Orphaned content is harmless -- it
    is invisible without a metadata row -- but removing it keeps the repository
    tidy. Failures here are deliberately not raised: the caller is already
    handling an error, and this must not mask it.
    """
    try:
        with _write_lock:
            _git("reset", "--hard", "HEAD~1")
    except GitError:
        pass
