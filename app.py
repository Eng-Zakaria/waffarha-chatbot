"""Waffarha backend shim.

Re-exports the FastAPI `app` from `core.app` so `uvicorn app:app` works
from the project root, regardless of which directory the developer launches
it from. The real application code (RAG engine wiring, HTTP routes, session
memory) lives in `core/app.py`; this file is just a stable import shim so
the existing `uvicorn app:app --reload` command and the Dockerfile
`CMD ["uvicorn", "app:app", ...]` keep working without changes.
"""
from core.app import app  # noqa: F401  -- re-exported for `uvicorn app:app`
