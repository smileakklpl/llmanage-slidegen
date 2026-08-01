"""AWS/container-compatible ASGI entrypoint for the production backend.

The FastAPI application remains defined in ``src/backend/app/main.py``.  This
module only makes the repository's source roots importable and re-exports that
single application so platforms can use either ``python main.py`` or
``uvicorn main:app`` from the repository root.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_ROOT = REPO_ROOT / "src"
BACKEND_ROOT = SRC_ROOT / "backend"

for source_root in (SRC_ROOT, BACKEND_ROOT):
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

from app.main import app as app  # noqa: E402


def main() -> None:
    """Run the canonical FastAPI application for process-based platforms."""
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
