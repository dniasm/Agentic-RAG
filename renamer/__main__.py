"""Command line.

    python -m renamer serve [--port 8200]     chat page + API on http://127.0.0.1:8200
    python -m renamer ingest FILE_OR_FOLDER…   index files (folders recursively) (calls Gemini for embeddings and the 120B for titles)
    python -m renamer list                     documents in the library
    python -m renamer chat                     chat with the agent in the terminal
"""
import argparse
import sys
import uuid
from pathlib import Path


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # titles can hold characters cp1252 lacks
    parser = argparse.ArgumentParser(prog="renamer")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8200)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("paths", nargs="+", type=Path)
    sub.add_parser("list")
    sub.add_parser("chat")
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        uvicorn.run("renamer.api:app", host=args.host, port=args.port)
        return

    from renamer import logs
    logs.setup()
    from renamer import catalog, library

    if args.command == "ingest":
        files = []
        for p in args.paths:
            files += sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in library.SUPPORTED_EXTENSIONS) \
                if p.is_dir() else [p]
        for f in files:
            print(f"{f.name}: ", end="", flush=True)
            try:
                doc, duplicate = library.ingest_path(f)
            except library.LibraryError as e:
                print(f"skipped ({e})")
                continue
            if duplicate:
                print(f'already in the library as "{doc["title"]}"')
            elif doc["status"] == "indexed":
                print(f'"{doc["title"]}" ({doc["chunks"]} chunks)')
            else:
                print(f"failed: {doc['error']}")

    elif args.command == "list":
        docs = catalog.list_documents()
        if not docs:
            print("The library is empty.")
        for d in docs:
            print(f'{d["id"]}  [{d["status"]}]  {d["title"]}   (from {d["original_name"]})')

    elif args.command == "chat":
        from renamer import agent
        session_id = uuid.uuid4().hex
        print("Ask me to rename a document. Empty line to quit.")
        while True:
            try:
                message = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not message:
                break
            result = agent.chat(session_id, message)
            print(f"\nagent> {result['reply']}")
            for a in result["actions"]:
                print(f"  {agent._action_text(a)}")


if __name__ == "__main__":
    sys.exit(main())
