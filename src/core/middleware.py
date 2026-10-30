from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send)
                return

        consumed = 0
        rejected = False

        async def limited_receive() -> Message:
            nonlocal consumed, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    rejected = True
                    await self._reject(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not rejected:
                await send(message)

        await self.app(scope, limited_receive, guarded_send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Request body exceeds the 2 MiB limit."}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
