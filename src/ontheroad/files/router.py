"""REST endpoints for git diff + file reads (Phase 3, slice 3C)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/sessions/{session_id}/files", tags=["files"])

MAX_FILE_SIZE = 512 * 1024  # 512 KB


def api_error(
    status: int, code: str, message: str, detail: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": detail or {}}},
    )


async def _get_workdir(request: Request, session_id: str) -> str | None:
    """Return the session workdir or None if session not found."""
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        return None
    session = await manager.get_session(session_id)
    if session is None:
        return None
    return session["workdir"]


def _is_path_safe(path: str) -> bool:
    """Reject paths containing '..' components (path traversal)."""
    parts = PurePosixPath(path).parts
    return ".." not in parts


async def _run_git(workdir: str, *args: str) -> tuple[int, str, str]:
    """Run a git command in workdir and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def _parse_diff_stats(diff_text: str) -> dict[str, Any]:
    """Parse unified diff to extract files_changed and insertion/deletion counts."""
    files: list[str] = []
    insertions = 0
    deletions = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            # Extract b/ path
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                files.append(parts[1])
        elif line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"files_changed": files, "stats": {"insertions": insertions, "deletions": deletions}}


@router.get("/diff")
async def get_diff(
    request: Request,
    session_id: str,
    ref: str = "HEAD",
    cached: bool = False,
) -> JSONResponse:
    workdir = await _get_workdir(request, session_id)
    if workdir is None:
        return api_error(404, "session_not_found", "Session not found")

    args = ["diff"]
    if cached:
        args.append("--cached")
    else:
        args.append(ref)

    rc, stdout, stderr = await _run_git(workdir, *args)
    if rc != 0 and "not a git repository" in stderr.lower():
        return JSONResponse(content={
            "diff": "",
            "files_changed": [],
            "stats": {"insertions": 0, "deletions": 0},
            "error": "not a git repository",
        })

    parsed = _parse_diff_stats(stdout)
    return JSONResponse(content={
        "diff": stdout,
        "files_changed": parsed["files_changed"],
        "stats": parsed["stats"],
    })


@router.get("/diff/{path:path}")
async def get_diff_file(
    request: Request,
    session_id: str,
    path: str,
) -> JSONResponse:
    workdir = await _get_workdir(request, session_id)
    if workdir is None:
        return api_error(404, "session_not_found", "Session not found")

    if not _is_path_safe(path):
        return api_error(400, "invalid_path", "Path traversal not allowed")

    rc, stdout, stderr = await _run_git(workdir, "diff", "--", path)
    return JSONResponse(content={"diff": stdout, "path": path})


@router.get("/read/{path:path}")
async def read_file(
    request: Request,
    session_id: str,
    path: str,
) -> JSONResponse:
    workdir = await _get_workdir(request, session_id)
    if workdir is None:
        return api_error(404, "session_not_found", "Session not found")

    if not _is_path_safe(path):
        return api_error(400, "invalid_path", "Path traversal not allowed")

    file_path = Path(workdir) / path
    if not file_path.is_file():
        return api_error(404, "file_not_found", f"File not found: {path}")

    try:
        size = file_path.stat().st_size
    except OSError:
        return api_error(404, "file_not_found", f"File not found: {path}")

    if size > MAX_FILE_SIZE:
        return api_error(413, "file_too_large", f"File exceeds {MAX_FILE_SIZE} byte limit")

    try:
        content = file_path.read_text(errors="replace")
    except OSError as exc:
        return api_error(500, "read_error", str(exc))

    return JSONResponse(content={
        "path": path,
        "content": content,
        "lines": content.count("\n") + (1 if content and not content.endswith("\n") else 0),
    })


@router.get("/tree")
async def get_tree(
    request: Request,
    session_id: str,
) -> JSONResponse:
    workdir = await _get_workdir(request, session_id)
    if workdir is None:
        return api_error(404, "session_not_found", "Session not found")

    rc, stdout, stderr = await _run_git(workdir, "ls-files")
    if rc != 0 and "not a git repository" in stderr.lower():
        # Fallback: top-level directory listing
        try:
            entries = sorted(
                e.name for e in Path(workdir).iterdir() if not e.name.startswith(".")
            )
        except OSError:
            entries = []
        return JSONResponse(content={"files": entries, "count": len(entries)})

    files = [f for f in stdout.splitlines() if f]
    return JSONResponse(content={"files": files, "count": len(files)})
