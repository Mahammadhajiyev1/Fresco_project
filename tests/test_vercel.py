"""Deployment readiness: the Vercel entrypoint, config, write location and the optional access token."""
import importlib.util
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import hwsets
import hwsets.api as api

ROOT = Path(__file__).parents[1]


def _load_entrypoint():
    """Import app.py exactly like a server would: by file path, from a directory that is NOT the project's src/."""
    spec = importlib.util.spec_from_file_location("vercel_entrypoint", ROOT / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_root_app_py_exposes_a_fastapi_instance_named_app():
    mod = _load_entrypoint()
    from fastapi import FastAPI
    assert isinstance(mod.app, FastAPI)
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "app = FastAPI(" in text                       # visible to Vercel's static entrypoint detection


def test_entrypoint_serves_the_whole_api(furniture_pdf, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.setenv("HWSETS_MOCK", "1")
    c = TestClient(_load_entrypoint().app)
    root = c.get("/").json()
    assert root["service"].startswith("Hardware Set Extractor") and root["docs"] == "/docs"
    assert c.get("/health").json() == {"ok": True, "mock": True}
    assert c.get("/docs").status_code == 200 and "swagger" in c.get("/docs").text.lower()
    r = c.post("/extract", files={"file": ("b.pdf", furniture_pdf, "application/pdf")})
    assert r.status_code == 200 and r.json()["result"]["document"].endswith("[MOCK]")


def test_vercel_config_files():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert 30 <= cfg["functions"]["app.py"]["maxDuration"] <= 300      # real extraction takes 40-60 s per document
    ignore = (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
    assert {"tests/", "data/", "ui/"} <= set(ignore)                   # tests/test_ui.py also defines an 'app' variable


def test_output_dir_uses_tmp_on_vercel(monkeypatch):
    monkeypatch.delenv("HWSETS_OUTPUT_DIR", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert hwsets.output_dir().as_posix() == "/tmp/hwsets_outputs"      # the only writable place there
    monkeypatch.setenv("HWSETS_OUTPUT_DIR", "custom")
    assert hwsets.output_dir() == Path("custom")                        # an explicit setting still wins
    monkeypatch.delenv("VERCEL")
    monkeypatch.delenv("HWSETS_OUTPUT_DIR")
    assert hwsets.output_dir() == Path("outputs")


@pytest.fixture
def guarded(monkeypatch, tmp_path, furniture_pdf):
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.setenv("HWSETS_MOCK", "1")
    monkeypatch.setenv("HWSETS_ACCESS_TOKEN", "dummy-token-123")
    return TestClient(api.app), furniture_pdf


def test_access_token_protects_extract_and_save(guarded):
    c, pdf = guarded
    files = {"file": ("b.pdf", pdf, "application/pdf")}
    assert c.post("/extract", files=files).status_code == 401                                # no header
    assert c.post("/extract", files=files, headers={"x-access-token": "wrong"}).status_code == 401
    ok = c.post("/extract", files=files, headers={"x-access-token": "dummy-token-123"})
    assert ok.status_code == 200
    doc_id, result = ok.json()["id"], ok.json()["result"]
    assert c.put(f"/results/{doc_id}", json=result).status_code == 401                       # saving is guarded too
    assert c.put(f"/results/{doc_id}", json=result, headers={"x-access-token": "dummy-token-123"}).status_code == 200
    assert c.get("/health").status_code == 200 and c.get(f"/results/{doc_id}").status_code == 200   # reads stay open


def test_api_is_open_when_no_token_is_configured(monkeypatch, tmp_path, furniture_pdf):
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.setenv("HWSETS_MOCK", "1")
    monkeypatch.delenv("HWSETS_ACCESS_TOKEN", raising=False)
    r = TestClient(api.app).post("/extract", files={"file": ("b.pdf", furniture_pdf, "application/pdf")})
    assert r.status_code == 200                                                              # local use needs no token


def test_token_never_appears_in_error_responses(guarded):
    c, pdf = guarded
    r = c.post("/extract", files={"file": ("b.pdf", pdf, "application/pdf")}, headers={"x-access-token": "nope"})
    assert "dummy-token-123" not in r.text and r.status_code == 401
