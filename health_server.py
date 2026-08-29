"""Минимальный HTTP-сервер состояния для Docker и будущего Mini App.

Он не принимает пользовательские данные и не зависит от Telegram или базы:
если процесс способен отвечать, контейнер считается живым.
"""

from __future__ import annotations

import asyncio
import os


HEALTH_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json; charset=utf-8\r\n"
    b"Cache-Control: no-store\r\n"
    b"Content-Length: 15\r\n"
    b"Connection: close\r\n\r\n"
    b'{"status":"ok"}'
)


async def _handle_health_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=2)
        path = request_line.split(b" ")[1] if len(request_line.split(b" ")) >= 2 else b"/"
        if path in {b"/", b"/health"}:
            writer.write(HEALTH_RESPONSE)
        else:
            writer.write(
                b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def start_health_server() -> asyncio.AbstractServer:
    host = os.getenv("HEALTH_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("HEALTH_PORT", "8080")))
    return await asyncio.start_server(_handle_health_request, host, port)
