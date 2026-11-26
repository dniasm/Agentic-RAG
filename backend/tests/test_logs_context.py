"""The request id end to end: an HTTP request (header in, header out, access line, one traceback per error), across a
real gRPC call to a service on an ephemeral port, and into the chat answer's background thread."""
import asyncio
import logging
import re
import threading
from datetime import date
from unittest import mock

import grpc
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import get_current_user
from backend.api.routes import chat
from backend.clients import Clients
from backend.config.settings import Settings
from backend.request_log import RequestLogMiddleware
from services.common.log import get_request_id, set_request_id, setup_logging
from services.common.server import Service

HEX12 = re.compile(r"[0-9a-f]{12}")


def read(log_dir, service="orchestrator"):
    for handler in logging.getLogger().handlers:
        handler.flush()
    return (log_dir / service / f"{date.today().isoformat()}.log").read_text(encoding="utf-8").splitlines()


def small_app():
    app = FastAPI()

    @app.get("/api/thing")
    def thing():
        logging.getLogger("backend.thing").info("inside the route")
        return {"id": get_request_id()}

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("kaput")

    app.add_middleware(RequestLogMiddleware)
    return app


# ---------------------------------------------------------------- HTTP
def test_the_callers_id_is_continued_echoed_and_on_every_line(log_dir):
    setup_logging("orchestrator")
    response = TestClient(small_app()).get("/api/thing", headers={"X-Request-Id": "trace-abc.1"})
    assert response.headers["x-request-id"] == "trace-abc.1" and response.json() == {"id": "trace-abc.1"}
    mine = [line for line in read(log_dir) if "[trace-abc.1]" in line]
    assert any("backend.thing: inside the route" in line for line in mine)
    assert any(line.endswith("backend.request_log: GET /api/thing -> 200 " + line.rsplit(" ", 1)[1]) and "ms" in line for line in mine)


def test_a_missing_or_unsafe_id_is_replaced_by_a_new_one(log_dir):
    setup_logging("orchestrator")
    client = TestClient(small_app())
    assert HEX12.fullmatch(client.get("/api/thing").headers["x-request-id"])
    sent = client.get("/api/thing", headers={"X-Request-Id": "a b=c"}).headers["x-request-id"]
    assert HEX12.fullmatch(sent)


def test_an_unhandled_error_has_the_header_one_traceback_and_an_error_access_line(log_dir):
    setup_logging("orchestrator")
    response = TestClient(small_app(), raise_server_exceptions=False).get("/api/boom", headers={"X-Request-Id": "rid-500"})
    assert response.status_code == 500 and response.headers["x-request-id"] == "rid-500"
    text = "\n".join(read(log_dir))
    assert text.count("Traceback (most recent call last)") == 1 and "RuntimeError: kaput" in text
    assert re.search(r"ERROR\s+\[orchestrator\] \[rid-500\] backend\.request_log: GET /api/boom -> 500 \d+ms", text)


def test_a_forged_path_cannot_add_a_line(log_dir):
    setup_logging("orchestrator")
    TestClient(small_app()).get("/api/%0aERROR%20forged", headers={"X-Request-Id": "rid-f"})
    assert not any(line.startswith("ERROR forged") for line in read(log_dir))


def test_the_health_check_is_quiet_and_the_real_app_answers_with_an_id(log_dir, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    setup_logging("orchestrator")
    from backend.app import app
    response = TestClient(app).get("/api/health", headers={"X-Request-Id": "health-1"})
    assert response.status_code == 200 and response.headers["x-request-id"] == "health-1"
    assert not any("backend.request_log" in line and "/api/health" in line for line in read(log_dir))   # debug level only


# ---------------------------------------------------------------- gRPC
def echo_service():
    """A service with one method that logs and answers with the request id it sees."""
    service = Service("user")

    async def say(request, context):
        logging.getLogger("services.user").info("handling")
        return get_request_id().encode()

    service.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
        "test.Echo", {"Say": grpc.unary_unary_rpc_method_handler(say)}),))
    return service


def call_echo(log_dir, rid):
    """One Say call through backend.clients with `rid` as the current request id; returns the id the service saw."""
    async def scenario():
        service = echo_service()
        port = service.bind("localhost:0")
        await service.start()
        clients = Clients(Settings(user_service_addr=f"localhost:{port}"))
        try:
            def go():
                set_request_id(rid)
                return clients.call(clients.channel("user").unary_unary("/test.Echo/Say"), b"").decode()
            return await asyncio.to_thread(go)
        finally:
            clients.close()
            await service.stop(grace=0.1)

    setup_logging("user")
    return asyncio.run(scenario())


def test_the_request_id_crosses_a_real_grpc_call(log_dir):
    assert call_echo(log_dir, "grpc-trace-1") == "grpc-trace-1"
    lines = [line for line in read(log_dir, "user") if "[grpc-trace-1]" in line]
    assert any("[user] [grpc-trace-1] services.user: handling" in line for line in lines)
    assert any(re.search(r"services\.common\.server: Echo/Say -> OK \d+ms$", line) for line in lines)


def test_a_call_with_no_request_starts_its_own_trace_in_the_service(log_dir):
    seen = call_echo(log_dir, "-")
    assert HEX12.fullmatch(seen)


def test_a_service_ignores_an_unsafe_id_from_a_caller(log_dir):
    async def scenario():
        service = echo_service()
        port = service.bind("localhost:0")
        await service.start()
        try:
            async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
                call = channel.unary_unary("/test.Echo/Say")
                return (await call(b"", metadata=(("x-request-id", "bad id=1"),))).decode()
        finally:
            await service.stop(grace=0.1)

    setup_logging("user")
    assert HEX12.fullmatch(asyncio.run(scenario()))


def test_a_failing_rpc_is_one_error_line_with_a_traceback(log_dir):
    async def scenario():
        service = Service("user")

        async def broken(request, context):
            raise RuntimeError("service bug")

        service.server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
            "test.Echo", {"Broken": grpc.unary_unary_rpc_method_handler(broken)}),))
        port = service.bind("localhost:0")
        await service.start()
        try:
            async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
                try:
                    await channel.unary_unary("/test.Echo/Broken")(b"", metadata=(("x-request-id", "boom-1"),))
                except grpc.aio.AioRpcError:
                    pass
        finally:
            await service.stop(grace=0.1)

    setup_logging("user")
    asyncio.run(scenario())
    text = "\n".join(read(log_dir, "user"))
    assert re.search(r"ERROR\s+\[user\] \[boom-1\] services\.common\.server: Echo/Broken failed: RuntimeError: service bug", text)
    assert text.count("Traceback (most recent call last)") == 1   # grpc's own, with the call's request id


# ---------------------------------------------------------------- the chat answer's thread
def test_the_chat_answer_thread_logs_with_the_requests_id(log_dir):
    setup_logging("orchestrator")
    done = threading.Event()
    thread_ids = []

    def agent(*args, **kwargs):
        thread_ids.append(threading.current_thread().name)
        logging.getLogger("backend.graph").info("agent running")
        yield "final", {"answer": "ok", "tool": "direct", "intent": "direct", "trace": []}

    def persist(conv, state, *args, **kwargs):
        logging.getLogger("backend.chat").info("answer saved")
        done.set()
        return {"conversation_id": conv["id"], "message_id": "m1", "answer": state["answer"]}

    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "tester"}
    app.add_middleware(RequestLogMiddleware)
    conv = {"id": "c1", "title": "t"}
    with mock.patch.object(chat, "_prepare", return_value=(conv, [], [], [], {"conversation_id": "c1"})), \
            mock.patch.object(chat, "stream_agent", side_effect=agent), \
            mock.patch.object(chat, "_persist", side_effect=persist):
        body = TestClient(app).post("/chat/stream", json={"message": "hi"}, headers={"X-Request-Id": "chat-77"}).text
    assert "event: final" in body and done.wait(5)
    assert thread_ids and thread_ids[0].startswith("chat-")   # it did run in its own thread
    lines = read(log_dir)
    assert any("[chat-77] backend.graph: agent running" in line for line in lines)
    assert any("[chat-77] backend.chat: answer saved" in line for line in lines)
