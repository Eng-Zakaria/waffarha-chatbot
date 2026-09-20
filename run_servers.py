"""
Run both chat servers side-by-side in ONE process:

  :8000  ->  core/app.py           (old RagEngine cascade chat widget)
  :8001  ->  core/agent_server.py  (new AgentEngine chat widget)

Why one process?  RagEngine opens the Qdrant local index with a folder
lock (data/index/<model>/qdrant), so two separate processes can't open the
same index at once.  Running both apps in the same event loop shares the
module-level engine singletons (get_engine / get_agent_engine) so only ONE
copy of the embedding model + vector index is loaded.

Run:
    python run_servers.py

Open http://localhost:8000 and http://localhost:8001 -- same widget, two
engines, identical /api/chat + /api/chat/stream endpoints.
"""
import asyncio

import uvicorn

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