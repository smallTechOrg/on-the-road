"""Echo subprocess: trivial newline-JSON line protocol for the EchoAdapter.

Reads JSON lines on stdin ({"type": "user_message"|"cancel"|"stop", ...}) and
writes JSON event lines on stdout mirroring the internal event types. On a user
message it deterministically streams the text back in fixed-size chunks with a
fake tool_start/tool_end pair and a turn_end, with a small delay between chunks
so streaming is visible.

Run as: python -m ontheroad.adapters.echo_child
"""

from __future__ import annotations

import asyncio
import json
import sys

CHUNK_SIZE = 8
CHUNK_DELAY = 0.05
TOOL_CALL_ID = "echo-tool-1"


def emit(event_type: str, payload: dict) -> None:
    sys.stdout.write(json.dumps({"type": event_type, "payload": payload}) + "\n")
    sys.stdout.flush()


async def stream_turn(text: str) -> None:
    emit("status", {"state": "working"})
    emit("tool_start", {"tool_call_id": TOOL_CALL_ID, "name": "echo", "args": {"text": text}})
    emit("tool_end", {"tool_call_id": TOOL_CALL_ID, "status": "completed"})
    for i in range(0, len(text), CHUNK_SIZE):
        emit("agent_text", {"text": text[i : i + CHUNK_SIZE]})
        await asyncio.sleep(CHUNK_DELAY)
    emit("turn_end", {"stop_reason": "end_turn"})


async def main() -> None:
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
        elif kind == "stop":
            break
        else:
            emit("error", {"message": f"echo child: unknown message type {kind!r}"})


if __name__ == "__main__":
    asyncio.run(main())
