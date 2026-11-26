"""The request id and one access line per HTTP request (pure ASGI, so a streamed chat answer is timed to its end).

  * The id is the caller's X-Request-Id when it is a safe one (services/common/log.py: valid_request_id), else a new
    one. It is on every log line of the request, goes out on the response as X-Request-Id, and rides every gRPC call
    the request makes (backend/clients.py), so `python scripts/logs.py --request <id>` shows the whole trace.
  * An error nothing else handled is logged here, once, with its traceback; the caller gets a plain 500 that still
    carries the id.
  * The access line is `METHOD path -> status Nms`; an error status is logged as an error, the health check
    (polled constantly) only at debug level.
"""
import logging
import time

from starlette.responses import JSONResponse

from services.common.log import REQUEST_ID_HEADER, safe_path, set_request_id, valid_request_id

log = logging.getLogger(__name__)


class RequestLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = next((v.decode("latin-1") for k, v in scope.get("headers") or () if k == REQUEST_ID_HEADER.encode()), None)
        rid = set_request_id(valid_request_id(incoming))
        method, path = scope["method"], safe_path(scope["path"])
        started = time.perf_counter()
        status = 500
        begun = False

        async def send_with_id(message):
            nonlocal status, begun
            if message["type"] == "http.response.start":
                begun = True
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", ()), (REQUEST_ID_HEADER.encode(), rid.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            log.exception("unhandled %s %s", method, path)   # the one traceback of this request
            if not begun:
                await JSONResponse({"detail": "Internal Server Error"}, status_code=500)(scope, receive, send_with_id)
        finally:
            ms = int((time.perf_counter() - started) * 1000)
            level = logging.ERROR if status >= 500 else logging.DEBUG if path == "/api/health" else logging.INFO
            log.log(level, "%s %s -> %d %dms", method, path, status, ms)
