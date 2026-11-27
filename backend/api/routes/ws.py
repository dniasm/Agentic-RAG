"""The WebSocket: GET /api/ws, one per browser tab, server -> client. Everything the server pushes goes through it
(chat answers as they are written, the queue's progress, indexing stages); the browser asks for things over REST.
Message types: backend/realtime/schema.py.

  * Auth is the HttpOnly login cookie, which the browser sends on the handshake. A page on another site is refused
    (Origin must be this site, a local page or a CORS_ORIGINS entry: the cookie alone would let any site open it).
    No valid login: the socket is accepted and closed with 4401 (so the page can read the code and stops retrying).
  * Or `?ticket=<t>` instead of the cookie: a single-use, 60 s ticket from POST /api/ws/ticket (backend/realtime/
    tickets.py), for a page whose host proxies REST but not WebSockets (Vercel in front of Render). It stands for the
    login that asked for it: logging out closes the socket it opened. Same origin rule.
  * `?after=<seq>` resumes: events published since that seq are replayed first (see backend/realtime/hub.py); a
    cursor too old for the log gets system/resync and the page reads its state over REST.
  * system/heartbeat goes out every HEARTBEAT_SECONDS; the client may send {"type":"ping"} and gets system/pong.
  * Each connection has its own request id (X-Request-Id on the handshake, else a new one) on its log lines.
"""
import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from backend import security
from backend.api.dependencies import get_current_user
from backend.library import auth
from backend.realtime import schema
from backend.realtime.hub import CLOSE, SIGNED_OUT, hub
from backend.realtime.tickets import TTL_SECONDS, tickets
from services.common.log import REQUEST_ID_HEADER, set_request_id, valid_request_id

router = APIRouter()
log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 20.0
UNAUTHENTICATED_CLOSE = 4401
POLICY_CLOSE = 1008
TRY_AGAIN_CLOSE = 1013
GOING_AWAY_CLOSE = 1001
MAX_CLIENT_MESSAGE = 1024


def origin_allowed(websocket: WebSocket) -> bool:
    """Browsers always send Origin on a WebSocket handshake. No Origin = not a browser (a script, the tests): let it
    through, the login cookie is still required."""
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    host = websocket.headers.get("host") or ""
    return security.allowed_host(host) and security.same_site(origin, host)


@router.post("/ws/ticket")
def ws_ticket(request: Request, user: dict = Depends(get_current_user)):
    """A ticket to open the WebSocket with (`/api/ws?ticket=`) where the login cookie can't reach it."""
    return {"ticket": tickets.issue(user["id"], request.cookies.get(auth.COOKIE) or ""),
            "expires_in": int(TTL_SECONDS)}


@router.websocket("/ws")
async def session_socket(websocket: WebSocket, after: int | None = None, ticket: str | None = None):
    rid = set_request_id(valid_request_id(websocket.headers.get(REQUEST_ID_HEADER)))
    if not origin_allowed(websocket):
        log.warning("WS refused: origin %s", websocket.headers.get("origin"))
        await websocket.close(code=POLICY_CLOSE)
        return
    token = websocket.cookies.get(auth.COOKIE)
    redeemed = tickets.redeem(ticket) if ticket is not None else None
    if ticket is not None:   # a bad, used or expired ticket is not signed in, whatever cookie came with it
        token = redeemed[1] if redeemed else None
    try:
        user = auth.user_for(token)
        if user and redeemed and user["id"] != redeemed[0]:
            user = None
        unavailable = False
    except auth.UserServiceDown:   # can't tell who this is: not signed in, and the browser is told to try again later
        user, unavailable = None, True
    await websocket.accept()   # accepted first so the browser can read our close code
    if unavailable:
        log.warning("WS refused: the User service is unavailable")
        await websocket.close(code=TRY_AGAIN_CLOSE, reason="Try again shortly.")
        return
    if not user:
        log.info("WS refused: not signed in")
        await websocket.close(code=UNAUTHENTICATED_CLOSE, reason="Please log in.")
        return
    uid = user["id"]
    queue, backlog, resync, latest = hub.attach(uid, after, token)
    started = time.perf_counter()
    log.info("WS open user=%s after=%s replay=%d resync=%s", uid, after, len(backlog), resync)
    sent = len(backlog)
    try:
        await websocket.send_json(schema.envelope("system", "hello", {"user": auth.public(user), "seq": latest},
                                                  request_id=rid))
        if resync:
            await websocket.send_json(schema.envelope("system", "resync", {}, request_id=rid))
        for event in backlog:
            await websocket.send_json(event)
        recv = asyncio.ensure_future(websocket.receive())
        get = asyncio.ensure_future(queue.get())
        try:
            while True:
                done, _ = await asyncio.wait({recv, get}, timeout=HEARTBEAT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    await websocket.send_json(schema.envelope("system", "heartbeat", {}, request_id=rid))
                    continue
                if recv in done:
                    message = recv.result()
                    if message["type"] == "websocket.disconnect":
                        return
                    if _is_ping(message.get("text")):
                        await websocket.send_json(schema.envelope("system", "pong", {}, request_id=rid))
                    recv = asyncio.ensure_future(websocket.receive())
                if get in done:
                    item = get.result()
                    if item is SIGNED_OUT:
                        await websocket.close(code=UNAUTHENTICATED_CLOSE, reason="Please log in.")
                        return
                    if item is CLOSE:
                        await websocket.close(code=GOING_AWAY_CLOSE)
                        return
                    await websocket.send_json(item)
                    sent += 1
                    get = asyncio.ensure_future(queue.get())
        finally:
            recv.cancel()
            get.cancel()
    except (WebSocketDisconnect, RuntimeError):   # the tab went away mid-send
        pass
    finally:
        hub.detach(uid, queue)
        log.info("WS closed user=%s sent=%d %dms", uid, sent, int((time.perf_counter() - started) * 1000))


def _is_ping(text: str | None) -> bool:
    if not text or len(text) > MAX_CLIENT_MESSAGE:
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("type") == "ping"
