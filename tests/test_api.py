from conftest import FakeExtractor
from fastapi.testclient import TestClient

import hwsets.api as api
from hwsets.pipeline import run_pipeline


def test_extract_get_save_and_page_image(table_pdf, table_pages, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-unused")
    monkeypatch.delenv("HWSETS_MOCK", raising=False)
    monkeypatch.setattr(
        api, "run_pipeline",
        lambda data, **kw: run_pipeline(data, **{**kw, "extractor": FakeExtractor(table_pages), "workers": 1}),
    )
    c = TestClient(api.app)
    r = c.post("/extract", files={"file": ("book.pdf", table_pdf, "application/pdf")})
    assert r.status_code == 200
    doc_id, result = r.json()["id"], r.json()["result"]
    assert {s["set_number"] for s in result["sets"]} == {"1", "2"}

    got = c.get(f"/results/{doc_id}").json()
    got["sets"][0]["components"][0]["qty"] = 9
    assert c.put(f"/results/{doc_id}", json=got).status_code == 200
    assert c.get(f"/results/{doc_id}").json()["sets"][0]["components"][0]["qty"] == 9

    png = c.get(f"/results/{doc_id}/page/2.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert c.post("/extract", files={"file": ("x.pdf", b"nope", "application/pdf")}).status_code == 400
    assert c.get("/results/../etc").status_code in (404, 400)


def test_no_key_gives_503_not_500(table_pdf, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HWSETS_MOCK", raising=False)
    r = TestClient(api.app).post("/extract", files={"file": ("b.pdf", table_pdf, "application/pdf")})
    assert r.status_code == 503 and "HWSETS_MOCK" in r.json()["detail"]


def test_mock_mode_is_labelled_and_needs_no_key(table_pdf, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("HWSETS_MOCK", "1")
    c = TestClient(api.app)
    assert c.get("/health").json()["mock"] is True
    res = c.post("/extract", files={"file": ("b.pdf", table_pdf, "application/pdf")}).json()["result"]
    assert res["document"].endswith("[MOCK]") and res["warnings"][0].startswith("MOCK MODE")
    assert {s["set_number"] for s in res["sets"]} == {"1", "2"}


def test_page_image_out_of_range_is_404(table_pdf, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.setenv("HWSETS_MOCK", "1")
    c = TestClient(api.app)
    doc_id = c.post("/extract", files={"file": ("b.pdf", table_pdf, "application/pdf")}).json()["id"]
    assert c.get(f"/results/{doc_id}/page/3.png").status_code == 200        # last page ok
    for bad in (4, 9999, 0, -1):
        assert c.get(f"/results/{doc_id}/page/{bad}.png").status_code == 404, bad
