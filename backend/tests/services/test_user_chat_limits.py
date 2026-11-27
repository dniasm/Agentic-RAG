"""Chat message allowances kept by the User service (ReserveChatMessage, RefundChatMessage, ChatUsage): the service's
windows and wall-clock times, a restart on the same database, two callers racing for the last place, and the
orchestrator's Allowance against a real in-process service (and against none)."""
import threading
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest
from fastapi import HTTPException

from agentic_rag_proto import user_pb2 as pb
from agentic_rag_proto import user_pb2_grpc as pb_grpc
from backend import chat_limits, clients
from backend.config.settings import settings
from services.common.inprocess import RunningService
from services.user import db as store
from services.user.server import build_service

BURST = (300, 3, False)
DAY = (86400, 5, True)


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def windows(*ws):
    return [pb.ChatWindow(seconds=s, allowed=n, daily=d) for s, n, d in ws]


def reserve(stub, user, *ws):
    return stub.ReserveChatMessage(pb.ReserveChatMessageRequest(user_id=user, windows=windows(*ws)), timeout=20)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def running(tmp_path, clock):
    service = RunningService(lambda: build_service(store.open_db(tmp_path / "user.db"), clock)).start()
    yield service
    service.stop()


@pytest.fixture
def stub(running):
    with grpc.insecure_channel(running.address) as channel:
        yield pb_grpc.UserServiceStub(channel)


def test_reserve_up_to_the_limit_then_refused_with_retry_after(stub, clock):
    for _ in range(3):
        assert reserve(stub, "u1", BURST, DAY).ok
        clock.now += 10
    refused = reserve(stub, "u1", BURST, DAY)
    assert not refused.ok and not refused.daily
    assert refused.retry_after == 300 - 30 + 1   # the first message was sent 30 s ago
    assert reserve(stub, "u2", BURST, DAY).ok   # per account


def test_the_day_window_is_marked_daily(stub, clock):
    for _ in range(5):
        assert reserve(stub, "u1", DAY).ok
    refused = reserve(stub, "u1", (300, 50, False), DAY)
    assert not refused.ok and refused.daily and refused.retry_after > 3600


def test_messages_leave_the_window_and_zero_means_off(stub, clock):
    for _ in range(3):
        reserve(stub, "u1", BURST)
    assert not reserve(stub, "u1", BURST).ok
    clock.now += 301
    assert reserve(stub, "u1", BURST).ok
    for _ in range(20):
        assert reserve(stub, "u3", (300, 0, False), (86400, 0, True)).ok


def test_refund_gives_the_place_back_and_only_to_its_owner(stub):
    token = reserve(stub, "u1", (300, 1, False)).token
    assert not reserve(stub, "u1", (300, 1, False)).ok
    assert not stub.RefundChatMessage(pb.RefundChatMessageRequest(user_id="u2", token=token), timeout=20).refunded
    assert not stub.RefundChatMessage(pb.RefundChatMessageRequest(user_id="u1", token="nope"), timeout=20).refunded
    assert stub.RefundChatMessage(pb.RefundChatMessageRequest(user_id="u1", token=token), timeout=20).refunded
    assert reserve(stub, "u1", (300, 1, False)).ok


def test_usage_for_one_account_and_for_all(stub, clock):
    reserve(stub, "u1", BURST)
    clock.now += 100
    reserve(stub, "u1", BURST)
    reserve(stub, "u2", BURST)
    one = stub.ChatUsage(pb.ChatUsageRequest(user_id="u1", windows=windows(BURST, DAY)), timeout=20)
    assert len(one.rows) == 1
    burst, day = one.rows[0].windows
    assert (burst.used, round(burst.oldest_age_seconds)) == (2, 100) and day.used == 2
    nobody = stub.ChatUsage(pb.ChatUsageRequest(user_id="nobody", windows=windows(BURST)), timeout=20)
    assert nobody.rows[0].windows[0].used == 0 and nobody.rows[0].windows[0].oldest_age_seconds == -1
    everyone = stub.ChatUsage(pb.ChatUsageRequest(windows=windows(BURST)), timeout=20)
    assert {r.user_id: r.windows[0].used for r in everyone.rows} == {"u1": 2, "u2": 1}
    clock.now += 250   # u1's first message is now 350 s old
    later = stub.ChatUsage(pb.ChatUsageRequest(user_id="u1", windows=windows(BURST)), timeout=20)
    assert later.rows[0].windows[0].used == 1


def test_old_rows_are_pruned(tmp_path, stub, running, clock):
    reserve(stub, "u1", BURST)
    clock.now += 2 * 86400
    reserve(stub, "u2", BURST)
    db = store.open_db(tmp_path / "user.db")   # a second handle on the same file
    try:
        with db.tx() as c:
            assert [r[0] for r in c.execute("SELECT user_id FROM chat_messages")] == ["u2"]
    finally:
        db.close()


def test_counts_survive_a_restart_of_the_service(tmp_path, clock):
    path = tmp_path / "user.db"
    first = RunningService(lambda: build_service(store.open_db(path), clock)).start()
    with grpc.insecure_channel(first.address) as channel:
        for _ in range(3):
            assert reserve(pb_grpc.UserServiceStub(channel), "u1", BURST).ok
    first.stop()
    clock.now += 60
    second = RunningService(lambda: build_service(store.open_db(path), clock)).start()
    try:
        with grpc.insecure_channel(second.address) as channel:
            refused = reserve(pb_grpc.UserServiceStub(channel), "u1", BURST)
            assert not refused.ok and refused.retry_after == 300 - 60 + 1
    finally:
        second.stop()


def test_two_callers_racing_for_the_last_place_one_wins(stub):
    for round_ in range(5):
        user = f"race{round_}"
        for _ in range(2):
            assert reserve(stub, user, BURST).ok   # one place left of 3
        barrier = threading.Barrier(2)

        def go(_):
            barrier.wait()
            return reserve(stub, user, BURST).ok
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(go, range(2)))
        assert sorted(results) == [False, True]
        used = stub.ChatUsage(pb.ChatUsageRequest(user_id=user, windows=windows(BURST)), timeout=20)
        assert used.rows[0].windows[0].used == 3


# ---------------------------------------------------------------- the orchestrator's Allowance
@pytest.fixture
def remote(running, monkeypatch):
    """The orchestrator dials this test's service instead of the session's."""
    monkeypatch.setattr(settings, "user_service_addr", running.address)
    clients.reset_clients()
    yield running
    monkeypatch.undo()
    clients.reset_clients()


def test_allowance_over_grpc_keeps_its_interface(remote, clock):
    a = chat_limits.Allowance(windows=[BURST, DAY])
    tokens = [a.reserve("u1") for _ in range(3)]
    with pytest.raises(chat_limits.ChatLimited) as e:
        a.reserve("u1")
    assert not e.value.daily and 1 <= e.value.retry_after <= 301
    a.refund("u1", tokens[0])
    a.reserve("u1")
    status = a.status("u1")
    assert [(s["seconds"], s["used"], s["left"], s["daily"]) for s in status] == [(300, 3, 0, False), (86400, 3, 2, True)]
    assert 290 <= status[0]["resets_in_seconds"] <= 301
    assert a.status("u9")[0]["used"] == 0 and a.status("u9")[0]["resets_in_seconds"] is None
    assert a.counts() == {"u1": [3, 3]}
    off = a.status("u1", [(300, 0, False), (60, 5, False)])   # a window that is off isn't listed
    assert [s["seconds"] for s in off] == [60]


def test_allowance_when_the_service_is_down_fails_closed_but_reads_dont_fail(monkeypatch, caplog):
    monkeypatch.setattr(settings, "user_service_addr", "localhost:1")
    clients.reset_clients()
    try:
        a = chat_limits.Allowance(windows=[BURST, DAY])
        with caplog.at_level("WARNING"):
            with pytest.raises(chat_limits.ChatLimited) as e:
                a.reserve("u1")
            assert (e.value.retry_after, e.value.daily) == (30, False)
            assert a.status("u1")[0]["used"] == 0 and a.counts() == {}
            a.refund("u1", "5")   # nothing raised
        assert "User service unavailable" in caplog.text
    finally:
        monkeypatch.undo()
        clients.reset_clients()


def test_an_admin_is_still_exempt(monkeypatch):
    from backend.api.routes import chat
    monkeypatch.setattr(chat.usage_limits, "is_unlimited", lambda uid: uid == "boss")
    monkeypatch.setattr(chat_limits, "allowance", chat_limits.Allowance(windows=[(300, 1, False)]))
    assert chat._allow("boss") == -1.0 and chat._allow("boss") == -1.0   # never counted
    chat.chat_limits.allowance.refund("boss", -1.0)   # a no-op, no call to the service
    assert chat._allow("someone")
    with pytest.raises(HTTPException) as e:
        chat._allow("someone")
    assert e.value.status_code == 429
