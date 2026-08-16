"""Echo subprocess: trivial newline-JSON line protocol for the EchoAdapter.

Reads JSON lines on stdin ({"type": "user_message"|"cancel"|"stop", ...}) and
writes JSON event lines on stdout mirroring the internal event types. On a user
message it deterministically streams the text back in fixed-size chunks with a
fake tool_start/tool_end pair and a turn_end, with a small delay between chunks
so streaming is visible.

Synthetic Phase-2 triggers (deterministic integration testing of slice 2D):

* a user message containing ``!git`` additionally emits a ``git_status`` event
  with a fixture PR url;
* a user message containing ``!permission`` emits a ``permission_request``
  event (fixed request_id ``echo-perm-1``) and holds the turn open until a
  ``{"type": "permission_response", "request_id", "option_id"}`` line arrives
  on stdin, then acks with agent_text + turn_end.

Run as: python -m ontheroad.adapters.echo_child
"""

from __future__ import annotations

import asyncio
import json
import sys

CHUNK_SIZE = 8
CHUNK_DELAY = 0.05
TOOL_CALL_ID = "echo-tool-1"
PERMISSION_REQUEST_ID = "echo-perm-1"
GIT_FIXTURE_URL = "https://github.com/example/on-the-road/pull/7"

# Set while a synthetic permission request awaits its answer.
pending_permission: dict | None = None


def emit(event_type: str, payload: dict) -> None:
    sys.stdout.write(json.dumps({"type": event_type, "payload": payload}) + "\n")
    sys.stdout.flush()


async def stream_turn(text: str) -> None:
    global pending_permission
    emit("status", {"state": "working"})
    emit("tool_start", {"tool_call_id": TOOL_CALL_ID, "name": "echo", "args": {"text": text}})
    emit("tool_end", {"tool_call_id": TOOL_CALL_ID, "status": "completed"})
    if "!git" in text:
        emit(
            "git_status",
            {
                "summary": "opened PR #7: echo fixture pull request",
                "branch": "feature/echo-fixture",
                "state": "pr_open",
                "pr_url": GIT_FIXTURE_URL,
            },
        )
    for i in range(0, len(text), CHUNK_SIZE):
        emit("agent_text", {"text": text[i : i + CHUNK_SIZE]})
        await asyncio.sleep(CHUNK_DELAY)
    if "!permission" in text:
        # Hold the turn open until a permission_response line arrives on stdin.
        pending_permission = {"request_id": PERMISSION_REQUEST_ID}
        emit(
            "permission_request",
            {
                "request_id": PERMISSION_REQUEST_ID,
                "tool_call": {"toolCallId": PERMISSION_REQUEST_ID, "title": "echo wants to touch a file"},
                "options": [
                    {"optionId": "allow_once", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
                ],
            },
        )
        return
    emit("turn_end", {"stop_reason": "end_turn"})


async def main() -> None:
    global pending_permission
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    emit("status", {"state": "ready"})
    turn_task: asyncio.Task | None = None
    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            emit("error", {"message": f"echo child: bad input line: {line[:200]!r}"})
            continue
        kind = msg.get("type")
        if kind == "user_message":
            turn_task = asyncio.create_task(stream_turn(str(msg.get("text", ""))))
        elif kind == "cancel":
            if turn_task is not None and not turn_task.done():
                turn_task.cancel()
                try:
                    await turn_task
                except asyncio.CancelledError:
                    pass
                emit("turn_end", {"stop_reason": "cancelled"})
            elif pending_permission is not None:
                pending_permission = None
                emit("turn_end", {"stop_reason": "cancelled"})
        elif kind == "permission_response":
            request_id = str(msg.get("request_id", ""))
            if pending_permission is not None and request_id == pending_permission["request_id"]:
                pending_permission = None
                option_id = str(msg.get("option_id", ""))
                emit("agent_text", {"text": f"permission answered: {option_id}"})
                emit("turn_end", {"stop_reason": "end_turn"})
            else:
                emit("error", {"message": f"echo child: no pending permission {request_id!r}"})
        elif kind == "stop":
            break
        else:
            emit("error", {"message": f"echo child: unknown message type {kind!r}"})


if __name__ == "__main__":
    asyncio.run(main())
