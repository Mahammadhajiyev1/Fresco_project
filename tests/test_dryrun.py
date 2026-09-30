import json
import os
import subprocess
import sys

import pytest

import hwsets.__main__ as cli
from hwsets.dryrun import BANNER, build_dry_run
from hwsets.ingest import load_pages
from hwsets.prompts import build_user_message


@pytest.fixture(autouse=True)
def no_api(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import anthropic

    def boom(*a, **k):
        raise AssertionError("dry run must never construct an Anthropic client")
    monkeypatch.setattr(anthropic, "Anthropic", boom)


def test_report_shape_and_no_api(furniture_pdf):
    r = build_dry_run(furniture_pdf, name="t.pdf")
    assert r.banner == BANNER == "DRY RUN — NO EXTERNAL API CALLS" and r.api_calls_made == 0
    assert r.candidate_pages == [1, 2, 3]
    assert [h.set_number for h in r.all_headers] == ["1", "2", "3"]
    assert len(r.chunks) == 1 and r.chunks[0].pages == [1, 2, 3]


def test_message_is_exactly_what_the_extractor_builds_and_is_clean(furniture_pdf):
    r = build_dry_run(furniture_pdf)
    pages = [p for p in load_pages(furniture_pdf) if p.number in r.chunks[0].pages]
    assert r.chunks[0].user_message == build_user_message(pages)
    assert "ACME TOWER" not in r.chunks[0].user_message and "Page 2 of 5" not in r.chunks[0].user_message
    assert len(r.chunks[0].hidden_furniture_line_ids) == 6            # header + footer on 3 pages


def test_every_line_is_traceable_to_the_source(furniture_pdf):
    r = build_dry_run(furniture_pdf)
    source = {l.id: l for p in load_pages(furniture_pdf) for l in p.lines}
    for l in r.chunks[0].lines:
        src = source[l.id]
        assert (l.original_text, l.bbox, l.page) == (src.text, src.bbox, src.page)
        assert f"[{l.id}] {l.model_text}" in r.chunks[0].user_message


def test_context_flag_matches_whether_the_block_is_in_the_message(furniture_pdf):
    for hint, expect in (([2, 3], True), ([1, 2, 3], False)):
        c = build_dry_run(furniture_pdf, pages_hint=hint).chunks[0]
        assert c.context_in_message is expect
        assert ("CONTINUATION CONTEXT" in c.user_message) is expect


def test_dryrun_module_does_not_import_the_anthropic_sdk():
    code = "import sys, hwsets.dryrun, hwsets.__main__; sys.exit(1 if 'anthropic' in sys.modules else 0)"
    env = {**os.environ, "PYTHONPATH": "src"}
    assert subprocess.run([sys.executable, "-c", code], env=env).returncode == 0


def test_cli_writes_json_and_text_and_prints_banner(furniture_pdf, tmp_path, capsys):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(furniture_pdf)
    out = tmp_path / "out" / "book.dryrun.json"
    assert cli.main([str(pdf), "--dry-run", "-o", str(out)]) == 0
    assert BANNER in capsys.readouterr().err
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["banner"] == BANNER and data["api_calls_made"] == 0 and data["chunks"]
    txt = out.with_suffix(".txt").read_text(encoding="utf-8")
    assert txt.startswith(BANNER) and txt.rstrip().endswith(BANNER) and "[user message sent to the model]" in txt


def test_default_output_dir_is_cwd_relative_and_overridable(furniture_pdf, tmp_path, monkeypatch, capsys):
    """No absolute/package-relative paths: files land in $HWSETS_OUTPUT_DIR, else ./outputs of the CWD."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(furniture_pdf)
    monkeypatch.setenv("HWSETS_OUTPUT_DIR", str(tmp_path / "custom"))
    assert cli.main([str(pdf), "--dry-run"]) == 0
    assert (tmp_path / "custom" / "book.dryrun.json").exists()
    monkeypatch.delenv("HWSETS_OUTPUT_DIR")
    monkeypatch.chdir(tmp_path)
    assert cli.main([str(pdf), "--dry-run"]) == 0
    assert (tmp_path / "outputs" / "book.dryrun.json").exists()
