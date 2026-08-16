"""HermesACPAdapter — headless Hermes over the Agent Client Protocol.

Includes a minimal hand-rolled ACP JSON-RPC 2.0 client (newline-delimited over
asyncio stdio streams). Only the client-side subset the adapter needs.

Protocol mapping (see spec/architecture.md → HermesACPAdapter):
- initialize → handshake; authMethods present → actionable error event
- session/new → sessionId (adapter_session_ref); session/load falling back to
  session/resume on re-attach
- session/prompt stays open for the turn; its stopReason → turn_end
- session/update notifications → AgentEvents (see ``map_acp_update``)
- session/request_permission (agent→client request) → permission_request event;
  Phase 1 auto-selects the reject-safe default with a labelled notice
- session/cancel notification → interrupt
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import uuid
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Sequence

from ontheroad.config import get_settings

from .base import AgentAdapter, AgentEvent, register_adapter

log = logging.getLogger("ontheroad.adapters.hermes_acp")

PROTOCOL_VERSION = 1


class ACPError(Exception):
    """A JSON-RPC error response (or transport failure)."""

    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(f"ACP error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


RequestHandler = Callable[[str, dict], Awaitable[dict]]
NotificationHandler = Callable[[str, dict], Awaitable[None]]


class ACPClient:
    """Minimal JSON-RPC 2.0 client over newline-delimited asyncio streams.

    ``reader`` needs ``readline()``; ``writer`` needs ``write(bytes)`` and
    ``drain()`` — satisfied by subprocess pipes and in-process test pairs.
    """

    def __init__(
        self,
        reader,
        writer,
        request_handler: RequestHandler | None = None,
        notification_handler: NotificationHandler | None = None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._request_handler = request_handler
        self._notification_handler = notification_handler
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._handler_tasks: set[asyncio.Task] = set()

    async def request(self, method: str, params: dict | None = None) -> dict:
        rid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            return await fut
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def run(self) -> None:
        """Read loop: dispatch responses, incoming requests, and notifications."""
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("acp: non-JSON line on stdout", extra={"output_summary": line[:200].decode(errors="replace") if isinstance(line, bytes) else str(line)[:200]})
                    continue
                if "method" in msg:
                    if "id" in msg:
                        task = asyncio.create_task(self._handle_request(msg))
                        self._handler_tasks.add(task)
                        task.add_done_callback(self._handler_tasks.discard)
                    elif self._notification_handler is not None:
                        await self._notification_handler(msg["method"], msg.get("params") or {})
                else:
                    self._resolve(msg)
        finally:
            self._fail_pending(ACPError(-32000, "connection closed"))

    def _resolve(self, msg: dict) -> None:
        fut = self._pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        if "error" in msg:
            err = msg["error"] or {}
            fut.set_exception(
                ACPError(int(err.get("code", -32000)), str(err.get("message", "unknown")), err.get("data"))
            )
        else:
            fut.set_result(msg.get("result") or {})

    async def _handle_request(self, msg: dict) -> None:
        rid = msg["id"]
        try:
            if self._request_handler is None:
                raise ACPError(-32601, f"method not supported: {msg['method']}")
            result = await self._request_handler(msg["method"], msg.get("params") or {})
            await self._send({"jsonrpc": "2.0", "id": rid, "result": result})
        except ACPError as exc:
            await self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": exc.code, "message": exc.message}})
        except Exception as exc:  # noqa: BLE001 — must answer the peer
            await self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(exc)}})

    def _fail_pending(self, exc: ACPError) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _send(self, obj: dict) -> None:
        self._writer.write(json.dumps(obj).encode() + b"\n")
        await self._writer.drain()


def _content_text(content: object) -> str:
    """Extract text from an ACP content block (dict, list of blocks, or str)."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text", ""))
    if isinstance(content, list):
        return "".join(_content_text(c) for c in content)
    return ""


def map_acp_update(update: dict) -> AgentEvent:
    """Map one ``session/update`` payload to an AgentEvent (forward compatible)."""
    kind = update.get("sessionUpdate")
    if kind == "agent_message_chunk":
        return AgentEvent("agent_text", {"text": _content_text(update.get("content"))})
    if kind == "agent_thought_chunk":
        return AgentEvent("agent_thought", {"text": _content_text(update.get("content"))})
    if kind == "tool_call":
        return AgentEvent("tool_start", dict(update))
    if kind == "tool_call_update":
        status = update.get("status")
        return AgentEvent("tool_end" if status in ("completed", "failed") else "tool_update", dict(update))
    if kind == "usage_update":
        return AgentEvent("usage", dict(update))
    # plan / current_mode_update / session_info_update / unknown kinds → status raw
    return AgentEvent("status", dict(update))


@register_adapter
class HermesACPAdapter(AgentAdapter):
    name = "hermes"

    def __init__(self, command: Sequence[str] | None = None) -> None:
        if command is None:
            command = [get_settings().hermes_python, "-m", "acp_adapter.entry"]
        self._command = list(command)
        self._proc: asyncio.subprocess.Process | None = None
        self._client: ACPClient | None = None
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._run_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._turn_task: asyncio.Task | None = None
        self._pending_permissions: dict[str, asyncio.Future] = {}
        self._session_id: str | None = None
        self._stopping = False

    async def start(self, workdir: Path, resume_ref: str | None) -> str:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._command,
                cwd=str(workdir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            self._emit("error", {"message": f"failed to spawn Hermes ACP subprocess: {exc}"})
            raise
        self._client = ACPClient(
            self._proc.stdout,
            self._proc.stdin,
            request_handler=self._on_request,
            notification_handler=self._on_notification,
        )
        self._run_task = asyncio.create_task(self._client.run())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        init = await self._client.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
            },
        )
        auth_methods = init.get("authMethods") or []
        # Hermes always advertises a terminal setup method for fresh installs,
        # even when a runtime provider is already configured — so its presence
        # alone doesn't mean auth is missing. Only treat it as a real setup
        # requirement when no other (provider-specific) method is offered.
        if auth_methods and all(m.get("type") == "terminal" for m in auth_methods):
            self._emit(
                "error",
                {
                    "message": "Hermes requires authentication setup: run `hermes acp --setup` on the VM",
                    "auth_methods": auth_methods,
                },
            )
        if resume_ref:
            params = {"sessionId": resume_ref, "cwd": str(workdir), "mcpServers": []}
            try:
                await self._client.request("session/load", params)
            except ACPError:
                await self._client.request("session/resume", params)
            self._session_id = resume_ref
        else:
            res = await self._client.request("session/new", {"cwd": str(workdir), "mcpServers": []})
            self._session_id = str(res["sessionId"])
        return self._session_id

    async def send_user_message(self, text: str) -> None:
        assert self._client is not None and self._session_id is not None, "adapter not started"
        self._turn_task = asyncio.create_task(self._run_prompt(text))

    async def events(self) -> AsyncIterator[AgentEvent]:
        while True:
            yield await self._queue.get()

    async def respond_permission(self, request_id: str, option_id: str) -> None:
        fut = self._pending_permissions.get(request_id)
        if fut is not None and not fut.done():
            fut.set_result(option_id)

    async def cancel(self) -> None:
        if self._client is not None and self._session_id is not None:
            await self._client.notify("session/cancel", {"sessionId": self._session_id})

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._turn_task, self._run_task, self._stderr_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        self._proc = None

    # -- internals --------------------------------------------------------

    def _emit(self, event_type: str, payload: dict) -> None:
        self._queue.put_nowait(AgentEvent(event_type, payload))

    async def _run_prompt(self, text: str) -> None:
        assert self._client is not None
        try:
            res = await self._client.request(
                "session/prompt",
                {"sessionId": self._session_id, "prompt": [{"type": "text", "text": text}]},
            )
            self._emit("turn_end", {"stop_reason": res.get("stopReason", "end_turn")})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — turn failure becomes an error event
            self._emit("error", {"message": f"agent turn failed: {exc}"})

    async def _on_notification(self, method: str, params: dict) -> None:
        if method == "session/update":
            self._queue.put_nowait(map_acp_update(dict(params.get("update") or {})))
        else:
            self._emit("status", {"notification": method, "params": params})

    async def _on_request(self, method: str, params: dict) -> dict:
        if method != "session/request_permission":
            raise ACPError(-32601, f"client method not supported: {method}")
        tool_call = params.get("toolCall") or {}
        request_id = str(tool_call.get("toolCallId") or f"perm-{uuid.uuid4().hex}")
        options = list(params.get("options") or [])
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_permissions[request_id] = fut
        self._emit(
            "permission_request",
            {"request_id": request_id, "options": options, "tool_call": tool_call},
        )
        # Phase 1: auto-select the reject-safe default, labelled in the transcript.
        if not fut.done():
            safe = self._safe_option(options)
            self._emit(
                "status",
                {
                    "notice": (
                        "Phase 1: permission request "
                        f"{request_id} auto-answered with safe default "
                        f"{safe['optionId'] if safe else 'cancelled'!r} "
                        "(interactive approvals arrive in Phase 2)"
                    )
                },
            )
            fut.set_result(safe["optionId"] if safe else None)
        try:
            option_id = await fut
        finally:
            self._pending_permissions.pop(request_id, None)
        if option_id is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": option_id}}

    @staticmethod
    def _safe_option(options: list[dict]) -> dict | None:
        for opt in options:
            if "reject" in str(opt.get("kind", "")).lower():
                return opt
        for opt in options:
            oid = str(opt.get("optionId", "")).lower()
            if "reject" in oid or "deny" in oid:
                return opt
        return None

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        stderr = self._proc.stderr
        while True:
            line = await stderr.readline()
            if not line:
                break
            log.info(
                "hermes stderr",
                extra={"output_summary": line.decode(errors="replace")[:200]},
            )
