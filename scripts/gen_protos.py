"""Generate the Python gRPC package `agentic_rag_proto/` (repo root) from protos/agentic_rag_proto/*.proto.

    python scripts/gen_protos.py          # (re)generate
    python scripts/gen_protos.py --check  # exit 1 when the committed generated code is stale (CI)

protoc runs with -Iprotos, so the generated modules import each other as `agentic_rag_proto.<name>_pb2`
and need no import rewriting.
"""
import argparse
import pathlib
import sys
import tempfile

from grpc_tools import protoc

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "protos"
PACKAGE = "agentic_rag_proto"


def generate(out: pathlib.Path) -> int:
    protos = sorted(PROTO_DIR.rglob("*.proto"))
    if not protos:
        sys.exit("no .proto files found under protos/")
    code = protoc.main([
        "grpc_tools.protoc", f"-I{PROTO_DIR}",
        f"--python_out={out}", f"--pyi_out={out}", f"--grpc_python_out={out}",
        *[str(p) for p in protos],
    ])
    if code != 0:
        sys.exit(f"protoc failed with exit code {code}")
    (out / PACKAGE / "__init__.py").touch()
    return len(protos)


def stale_files(fresh: pathlib.Path, committed: pathlib.Path) -> list[str]:
    fresh_files = {p.relative_to(fresh).as_posix() for p in fresh.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    have = {p.relative_to(committed).as_posix() for p in committed.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".pyi")}
    bad = [f"missing: {f}" for f in sorted(fresh_files - have)] + [f"extra: {f}" for f in sorted(have - fresh_files)]
    for f in sorted(fresh_files & have):
        # line endings don't count: a Windows checkout may turn LF into CRLF
        if (fresh / f).read_bytes() != (committed / f).read_bytes().replace(b"\r\n", b"\n"):
            bad.append(f"differs: {f}")
    return bad


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when the generated code is not up to date")
    args = parser.parse_args()
    if not args.check:
        print(f"generated code for {generate(ROOT)} proto file(s)")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        generate(tmp_path)
        bad = stale_files(tmp_path / PACKAGE, ROOT / PACKAGE)
    for line in bad:
        print(line)
    print("generated code is stale: run python scripts/gen_protos.py" if bad else "generated code is up to date")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
