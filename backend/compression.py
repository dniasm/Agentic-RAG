"""Gzip for the site's text: the built scripts and styles (about 2.4 MB, most of a first load over a phone's mobile
data), the pages and the API's JSON.

Never compressed:
  * the chat's stream (text/event-stream): every word must arrive as it is written, not in one block at the end;
  * a request with a Range header (the PDF viewer may fetch parts of a file): gzip over a byte range breaks the range;
  * files that are compressed already or gain nothing: PDF, Word, images, fonts, archives, audio, video;
  * small bodies (under MIN_SIZE), where the header costs more than it saves.
"""
from starlette.datastructures import Headers
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

MIN_SIZE = 1024
LEVEL = 6   # most of level 9's saving at a fraction of the time (the worker script is 1.3 MB)
EXCLUDED = DEFAULT_EXCLUDED_CONTENT_TYPES + (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "image/*",
    "font/*",
)


class Compression:
    """GZipMiddleware with the exclusions above, and none at all for a ranged request."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=MIN_SIZE, compresslevel=LEVEL, exclude_content_types=EXCLUDED)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and "range" in Headers(scope=scope):
            return await self.app(scope, receive, send)
        await self.gzip(scope, receive, send)
