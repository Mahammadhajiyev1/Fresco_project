"""Live smoke test against the real Claude API.  Run: pytest -m live  (needs ANTHROPIC_API_KEY)."""
from pathlib import Path

import pytest

from hwsets import api_key_configured
from hwsets.pipeline import run_pipeline

PDFS = sorted((Path(__file__).parents[1] / "data").glob("*.pdf"))


@pytest.mark.live
@pytest.mark.skipif(not (api_key_configured() and PDFS), reason="needs a real ANTHROPIC_API_KEY (.env) and a PDF in data/")
def test_real_extraction_smoke():
    res = run_pipeline(PDFS[0])
    assert res.sets, res.warnings
    assert all(s.locations for s in res.sets)
