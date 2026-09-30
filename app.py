"""Vercel entrypoint.

Vercel auto-detects a FastAPI instance named ``app`` in a file called app.py. The real application lives in
src/hwsets/api.py (a package), so this thin wrapper puts src/ on the import path and mounts that API at "/".
Local use is unchanged:  uvicorn hwsets.api:app
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))   # the package is not pip-installed on Vercel

from fastapi import FastAPI  # noqa: E402

from hwsets.api import app as _api  # noqa: E402

app = FastAPI(title="Hardware Set Extractor", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/", _api)        # the mounted API serves /, /health, /docs, /extract, /results/...
