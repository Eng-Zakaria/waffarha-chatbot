"""
Run the old cascade and the new agent backend side-by-side in ONE process.

Why one process instead of two `uvicorn` commands?  RagEngine opens the
Qdrant local index with a folder lock (data/index/<model>/qdrant), which
prevents two separate processes from opening the same index at once.  By
running both apps in the same event loop they share the module-level engine
singletons in core/app.py (get_engine / get_agent_engine), so exactly ONE
copy of the embedding model + vector index is loaded, and both ports answer
against it:

  :8000  ->  core/agent_server.py (same widget, /api/chat wired to AgentEngine)
  :8001  ->  core/agent_server.py (agent)   <== please use this one

The old-widget-on-:8000 route (core.app's plain RagEngine cascade) is
deliberately NOT run here -- see README. Switch the assignment below if you
want it back.

Run:
    python run_servers.py
"""
import asyncio

import uvicorn

# Port assignments (register each ASGI app + port pair here).
# The engine singleton lives in core.app, so importing core.app once at the
# top here (via agent_server's imports) gives both apps the same model.
SERVERS = [
    ("core.app:app", 8000, "old cascade:8000"),
    ("core.agent_server:app", 8001, "agent:8001"),
]


async def _serve(app_path: str, port: int, name: str):
    config = uvicorn.Config(
        app_path,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    print(f"[run_servers] {name} -> http://localhost:{port}", flush=True)
    await server.serve()


async def main():
    await asyncio.gather(
        *(_serve(path, port, name) for path, port, name in SERVERS)
    )


if __name__ == "__main__":
    asyncio.run(main())