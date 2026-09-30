"""Streamlit UI regression tests (offline: Streamlit's AppTest runs ui/app.py in-process, HTTP is faked)."""
import io
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from PIL import Image
from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "ui" / "app.py"


def _result():
    def comp(desc, qty=1):
        return {"qty": qty, "description": desc, "catalog_number": None, "mfr": None, "finish": None,
                "notes": None, "confidence": 0.9, "source_line_ids": [], "warnings": []}

    def a_set(number, status="active", comps=(), desc=None):
        box = {"x0": 50, "y0": 60, "x1": 300, "y1": 120}
        return {"set_number": number, "description": desc, "status": status, "confidence": 0.9, "warnings": [],
                "locations": [{"page": 1, "bbox": box, "line_ids": []}], "components": list(comps)}
    return {"document": "d.pdf [MOCK]", "total_pages": 1, "pages_scanned": [1], "layouts": [],
            "warnings": ["MOCK MODE: not real extraction"],
            "sets": [a_set("01", comps=[comp("HINGE")]), a_set("02", status="not_used", desc="Not Used"),
                     a_set("03", comps=[comp("CLOSER"), comp("STOP")])]}


class FakeHttp:
    """Stands in for `requests` inside the app."""

    def __init__(self):
        self.puts = []
        png = io.BytesIO()
        Image.new("RGB", (60, 80), "white").save(png, "PNG")
        self.png = png.getvalue()

    def get(self, url, **kw):
        class R:
            ok, status_code, text = True, 200, "{}"
            content = self.png
        return R()

    def put(self, url, json=None, **kw):
        self.puts.append((url, json))

        class R:
            ok, status_code, text = True, 200, ""
        return R()


@pytest.fixture
def app(monkeypatch):
    import requests
    fake = FakeHttp()
    monkeypatch.setattr(requests, "get", fake.get)
    monkeypatch.setattr(requests, "put", fake.put)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state["doc_id"] = "abc123"
    at.session_state["result"] = _result()
    at.run()
    at.fake = fake
    return at


def test_renders_all_sets_with_mock_warning_and_no_exceptions(app):
    assert not app.exception
    assert len(app.radio[0].options) == 3
    assert any("MOCK MODE" in w.value for w in app.warning)
    assert app.subheader[0].value.startswith("Set 01")


def test_selecting_a_not_used_set_without_components(app):
    app.radio[0].set_value(1).run()
    assert not app.exception and "[NOT USED]" in app.subheader[0].value


@pytest.mark.parametrize("idx", [0, 1, 2])
def test_editing_and_saving_any_set_keeps_the_selection_and_saves(app, idx):
    """Regression: the radio's labels used to contain the live description, so typing changed the widget identity,
    the page jumped back to Set 01 and the Save click was lost for every set except the first."""
    app.radio[0].set_value(idx).run()
    labels_before = list(app.radio[0].options)
    app.text_input(key=f"desc{idx}").set_value("EDITED BY TEST").run()
    assert app.radio[0].value == idx                                   # still on the same set after typing
    assert list(app.radio[0].options) == labels_before                 # labels are a stable snapshot
    app.button(key=f"save{idx}").click().run()
    assert [s.value for s in app.success] == ["Saved"] and not app.error and not app.exception
    (url, body), = app.fake.puts
    assert url.endswith("/results/abc123") and body["sets"][idx]["description"] == "EDITED BY TEST"
    assert app.radio[0].value == idx                                   # and the selection survives the save
    assert [s["set_number"] for s in body["sets"]] == ["01", "02", "03"]
