"""services.common: the gRPC bootstrap answers health checks, config reads the environment, the SQLite helper works,
and the orchestrator's clients probe a real server."""
import asyncio
import importlib.util
import sqlite3
from pathlib import Path

import grpc
import pytest

from backend.clients import SERVICES, Clients
from backend.config.settings import Settings
from services.common import config
from services.common.server import Service
from services.common.sqlite_db import ServiceDB

ROOT = Path(__file__).resolve().parents[3]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


healthcheck = load_script("grpc_healthcheck")


def test_health_server_answers_on_an_ephemeral_port_and_stops_gracefully():
    async def scenario():
        service = Service("test")
        closed = []
        service.add_closer(lambda: closed.append(True))
        port = service.bind("localhost:0")
        assert port > 0
        await service.start()
        try:
            assert await asyncio.to_thread(healthcheck.check, port) is True
        finally:
            await service.stop(grace=0.1)
        assert closed == [True]
        with pytest.raises(grpc.RpcError):
            await asyncio.to_thread(healthcheck.check, port, "localhost", 1.0)

    asyncio.run(scenario())


def test_registered_servicer_name_is_reported_serving():
    from grpc_health.v1 import health_pb2, health_pb2_grpc

    async def scenario():
        service = Service("test")
        service.register(lambda servicer, server: None, object(), "agentic_rag.v1.Thing")
        port = service.bind("localhost:0")
        await service.start()
        try:
            async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
                reply = await health_pb2_grpc.HealthStub(channel).Check(
                    health_pb2.HealthCheckRequest(service="agentic_rag.v1.Thing"), timeout=3)
            return reply.status
        finally:
            await service.stop(grace=0.1)

    assert asyncio.run(scenario()) == health_pb2.HealthCheckResponse.SERVING


def test_clients_probe_reports_up_and_down():
    async def scenario():
        service = Service("test")
        port = service.bind("localhost:0")
        await service.start()
        clients = Clients(Settings(user_service_addr=f"localhost:{port}", conversation_service_addr="localhost:1",
                                   grpc_health_timeout_seconds=1.0))
        try:
            return await asyncio.to_thread(lambda: (clients.probe("user"), clients.probe("conversation")))
        finally:
            clients.close()
            await service.stop(grace=0.1)

    up, down = asyncio.run(scenario())
    assert up["status"] == "up" and up["error"] is None
    assert down["status"] == "down" and down["error"]


def test_service_ports_are_reserved_and_settings_follow_them():
    defaults = {name: config.default_address(name).rsplit(":", 1)[1] for name in config.SERVICE_ADDRESSES}
    assert defaults == {"user": "50061", "conversation": "50062", "memory": "50063", "document": "50064"}
    settings = Settings()
    assert set(SERVICES) == set(config.SERVICE_ADDRESSES)
    for name, (_, attr) in SERVICES.items():
        assert hasattr(settings, attr)


def test_address_comes_from_the_environment_and_listen_host_replaces_the_host(monkeypatch):
    monkeypatch.setenv("USER_SERVICE_ADDR", "user-svc:6000")
    monkeypatch.delenv("GRPC_LISTEN_HOST", raising=False)
    assert config.service_address("user") == "user-svc:6000"
    assert config.listen_address("user") == "user-svc:6000"
    monkeypatch.setenv("GRPC_LISTEN_HOST", "0.0.0.0")
    assert config.listen_address("user") == "0.0.0.0:6000"
    assert Settings().user_service_addr == "user-svc:6000"


def test_data_dir_defaults_under_backend_data_and_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("SERVICES_DATA_DIR", raising=False)
    assert config.data_dir() == ROOT / "backend" / "data" / "services"
    monkeypatch.setenv("SERVICES_DATA_DIR", str(tmp_path))
    assert config.data_dir() == tmp_path


def test_service_db_is_its_own_file_with_foreign_keys_and_rollback(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVICES_DATA_DIR", str(tmp_path / "nested"))
    db = ServiceDB("demo", "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT NOT NULL);")
    try:
        with db.tx() as c:
            c.execute("INSERT INTO t (v) VALUES ('a')")
        with pytest.raises(sqlite3.IntegrityError):
            with db.tx() as c:
                c.execute("INSERT INTO t (v) VALUES ('b')")
                c.execute("INSERT INTO t (v) VALUES (NULL)")
        with db.tx() as c:
            assert [r["v"] for r in c.execute("SELECT v FROM t")] == ["a"]
            assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.path == tmp_path / "nested" / "demo.db" and db.path.exists()
    finally:
        db.close()


def test_calls_that_wait_for_voyage_get_a_deadline_longer_than_the_wait(monkeypatch):
    """Search, Rerank, SearchEpisodes and StoreEpisode may wait in the service for a Voyage slot: the orchestrator's
    deadline must outlast that wait, or it cancels the call and the answer loses its documents (seen live: 5 s cut
    off a search waiting up to 10 s for a rerank slot). Plain calls keep the short standard deadline."""
    from agentic_rag_proto import document_pb2, memory_pb2
    from backend.config.settings import settings
    from backend.documents import rag
    from backend.memory import store

    seen = {}

    class FakeClients:
        def __getattr__(self, service):
            return type("Stub", (), {"__getattr__": lambda _self, rpc: rpc})()

        def call(self, rpc, request, metadata=None, timeout=None):
            seen[rpc] = timeout
            return {"Search": document_pb2.SearchReply(), "Rerank": document_pb2.RerankReply(),
                    "SearchEpisodes": memory_pb2.SearchEpisodesReply(),
                    "StoreEpisode": memory_pb2.StoreEpisodeReply(id="e1"),
                    "CountPassages": document_pb2.CountPassagesReply()}[rpc]

    monkeypatch.setattr(rag, "get_clients", FakeClients)
    monkeypatch.setattr(store, "get_clients", FakeClients)
    rag.search(["q"], "u1")
    rag.rerank_texts("q", ["a"])
    store.search_memories("u1", "q")
    store.save_memory("u1", "text")
    rag.count_passages("u1")

    assert seen["Search"] > settings.voyage_rerank_max_wait + settings.voyage_rerank_timeout
    assert seen["Rerank"] > settings.voyage_rerank_max_wait + settings.voyage_rerank_timeout
    assert seen["SearchEpisodes"] > settings.voyage_recall_max_wait + settings.voyage_rerank_timeout
    assert seen["StoreEpisode"] > settings.voyage_save_max_wait + settings.voyage_rerank_timeout
    assert seen["CountPassages"] is None          # the standard deadline
