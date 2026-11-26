"""scripts/gen_protos.py and scripts/run_all.py: generated code is current, and the launch plan is sane
(nothing is launched)."""
import importlib.util
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gen_protos = load_script("gen_protos")
run_all = load_script("run_all")


def test_gen_protos_check_passes_on_the_committed_code():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_protos.py"), "--check"],
                          capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stdout + done.stderr


def test_gen_protos_check_notices_stale_extra_and_missing_files(tmp_path):
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp)
        gen_protos.generate(fresh)
        copy = tmp_path / gen_protos.PACKAGE
        shutil.copytree(fresh / gen_protos.PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
        assert gen_protos.stale_files(fresh / gen_protos.PACKAGE, copy) == []
        (copy / "common_pb2.py").write_text("# edited\n")
        (copy / "gone_pb2.py").write_text("")
        (copy / "common_pb2.pyi").unlink()
        bad = gen_protos.stale_files(fresh / gen_protos.PACKAGE, copy)
    assert sorted(bad) == ["differs: common_pb2.py", "extra: gone_pb2.py", "missing: common_pb2.pyi"]


def test_run_all_plan_with_no_services_is_the_orchestrator_alone():
    plan = run_all.build_plan([])
    assert [p.name for p in plan] == ["orchestrator"]
    assert plan[0].argv[1:] == ("-m", "uvicorn", "app:app", "--port", "8000")
    assert plan[0].kind == "http" and plan[0].ready_path == "/api/health"
    assert (ROOT / "app.py").exists()  # uvicorn app:app runs from the repo root


def test_run_all_registry_starts_the_user_and_conversation_services_first():
    assert run_all.SERVICES == ["user", "conversation"]
    plan = run_all.build_plan()
    assert [p.name for p in plan] == ["user", "conversation", "orchestrator"]
    assert plan[0].argv[1:] == ("-m", "services.user") and plan[0].kind == "grpc"
    assert plan[1].argv[1:] == ("-m", "services.conversation") and plan[1].kind == "grpc"


def test_run_all_plan_starts_services_first_on_their_reserved_ports():
    plan = run_all.build_plan(["user", "conversation", "memory", "document"], port=9000, reload=True)
    assert [p.name for p in plan] == ["user", "conversation", "memory", "document", "orchestrator"]
    assert [p.port for p in plan] == [50061, 50062, 50063, 50064, 9000]
    assert plan[0].argv[1:] == ("-m", "services.user") and plan[0].kind == "grpc"
    assert plan[-1].argv[-1] == "--reload"
    assert len({p.port for p in plan}) == len(plan)


def test_run_all_registry_only_names_real_services():
    plan = run_all.build_plan(run_all.SERVICES)
    assert len(plan) == len(run_all.SERVICES) + 1
    with pytest.raises(ValueError):
        run_all.build_plan(["nope"])


def test_run_all_refuses_to_start_when_a_port_is_taken(capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert run_all.port_in_use(port)
        assert run_all.run([run_all.orchestrator_proc(port)]) == 1
    assert str(port) in capsys.readouterr().err


def test_stop_all_stops_the_orchestrator_before_the_services_and_waits_for_each(monkeypatch):
    events = []

    class Child:
        def __init__(self, name):
            self.name, self.alive = name, True

        def poll(self):
            return None if self.alive else 0

        def send_signal(self, _sig):
            events.append(f"signal {self.name}")

        def wait(self, _timeout=None):
            events.append(f"gone {self.name}")
            self.alive = False

    children = [(run_all.Proc(n, (), "grpc", 1), Child(n)) for n in ("user", "conversation", "orchestrator")]
    run_all.stop_all(children)
    assert events == ["signal orchestrator", "gone orchestrator", "signal conversation", "gone conversation",
                      "signal user", "gone user"]
