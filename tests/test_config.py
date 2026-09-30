"""Configuration / secret handling. Only DUMMY values are ever used here - no real key, no network."""
import os
import tomllib
from pathlib import Path

import pytest

import hwsets
import hwsets.__main__ as cli
from hwsets import ConfigurationError, api_key_configured, require_api_key
from hwsets.extractor import Extractor

ROOT = Path(__file__).parents[1]
DUMMY = "dummy-not-a-real-key-0000"


@pytest.fixture
def cwd_with_env(tmp_path, monkeypatch):
    """Run in a temp folder that has its own .env (the loader looks in the CWD first)."""
    monkeypatch.chdir(tmp_path)
    for name in ("ANTHROPIC_API_KEY", "HWSETS_TEST_A", "HWSETS_TEST_B", "HWSETS_TEST_C", "HWSETS_TEST_D"):
        monkeypatch.setenv(name, "")                       # registers the original value so the test can't leak
    monkeypatch.delenv("HWSETS_TEST_A")
    monkeypatch.delenv("HWSETS_TEST_B")

    def write(text):
        (tmp_path / ".env").write_text(text, encoding="utf-8")
    return write


def test_env_file_is_loaded_and_understands_comments_quotes_and_bom(cwd_with_env, monkeypatch):
    cwd_with_env('﻿# a comment\nANTHROPIC_API_KEY="%s"\nHWSETS_TEST_A = plain\nHWSETS_TEST_B=\'quoted\'\n' % DUMMY)
    hwsets._load_env()
    assert os.environ["ANTHROPIC_API_KEY"] == DUMMY and api_key_configured()
    assert os.environ["HWSETS_TEST_A"] == "plain" and os.environ["HWSETS_TEST_B"] == "quoted"


def test_empty_key_in_env_file_means_not_configured(cwd_with_env):
    cwd_with_env("ANTHROPIC_API_KEY=\n")
    hwsets._load_env()
    assert not api_key_configured() and not os.environ.get("ANTHROPIC_API_KEY")


def test_template_placeholder_counts_as_not_configured(cwd_with_env, monkeypatch):
    cwd_with_env("ANTHROPIC_API_KEY=your_key_here\n")
    hwsets._load_env()                                     # copied .env.example without editing it
    assert not api_key_configured()
    with pytest.raises(ConfigurationError):
        require_api_key()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert not api_key_configured()


def test_real_environment_variable_wins_over_env_file(cwd_with_env, monkeypatch):
    monkeypatch.setenv("HWSETS_TEST_C", "from-shell")
    cwd_with_env("HWSETS_TEST_C=from-file\n")
    hwsets._load_env()
    assert os.environ["HWSETS_TEST_C"] == "from-shell"


def test_a_blank_shell_variable_does_not_block_the_env_file(cwd_with_env, monkeypatch):
    monkeypatch.setenv("HWSETS_TEST_D", "")
    cwd_with_env("HWSETS_TEST_D=from-file\n")
    hwsets._load_env()
    assert os.environ["HWSETS_TEST_D"] == "from-file"


def test_env_example_template_is_never_read(cwd_with_env, tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text("HWSETS_TEST_A=sentinel-from-example\nANTHROPIC_API_KEY=sentinel\n", encoding="utf-8")
    hwsets._load_env()
    assert os.environ.get("HWSETS_TEST_A") != "sentinel-from-example"
    assert os.environ.get("ANTHROPIC_API_KEY") != "sentinel"


def test_error_is_clear_and_never_contains_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(ConfigurationError) as e:
        require_api_key()
    msg = str(e.value)
    assert ".env" in msg and "ANTHROPIC_API_KEY" in msg and "--dry-run" in msg
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY)
    require_api_key()                                      # configured: no error, and nothing is returned or echoed


@pytest.fixture
def sdk_must_not_be_used(monkeypatch):
    import anthropic

    def boom(*a, **k):
        raise AssertionError("the Anthropic client must not be constructed without a configured key")
    monkeypatch.setattr(anthropic, "Anthropic", boom)


def test_extractor_without_a_key_fails_early_with_a_clear_error(monkeypatch, sdk_must_not_be_used):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY is not set"):
        Extractor()


def test_live_cli_without_a_key_reports_configuration_error_not_a_traceback(furniture_pdf, tmp_path, monkeypatch, capsys,
                                                                            sdk_must_not_be_used):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    pdf, out = tmp_path / "book.pdf", tmp_path / "result.json"
    pdf.write_bytes(furniture_pdf)
    assert cli.main([str(pdf), "-o", str(out)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("configuration error:") and "ANTHROPIC_API_KEY" in err and "Traceback" not in err
    assert not out.exists()                                # nothing was extracted or written


def test_offline_workflows_work_with_an_empty_key(furniture_pdf, tmp_path, monkeypatch, sdk_must_not_be_used):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(furniture_pdf)
    assert cli.main([str(pdf), "--dry-run", "-o", str(tmp_path / "d.json")]) == 0            # dry run
    from fastapi.testclient import TestClient
    import hwsets.api as api
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.setenv("HWSETS_MOCK", "1")
    r = TestClient(api.app).post("/extract", files={"file": ("b.pdf", furniture_pdf, "application/pdf")})
    assert r.status_code == 200 and r.json()["result"]["document"].endswith("[MOCK]")        # API mock mode


def test_api_treats_the_placeholder_like_a_missing_key(furniture_pdf, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import hwsets.api as api
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.delenv("HWSETS_MOCK", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "your_key_here")
    r = TestClient(api.app).post("/extract", files={"file": ("b.pdf", furniture_pdf, "application/pdf")})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_offline_tests_run_without_any_key_even_if_env_file_had_one():
    """conftest strips ANTHROPIC_API_KEY from every non-live test, so a real key in .env can't reach the suite."""
    assert os.getenv("ANTHROPIC_API_KEY") is None


def test_live_tests_are_opt_in_only():
    """A plain `pytest` must never spend money: the live marker is deselected by default (run with -m live)."""
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert "not live" in cfg["addopts"] and "live" in " ".join(cfg["markers"])


# ---- API-side failures: clear messages, workspace header ----------------------------------------------------------
class _Recorder:
    """Stands in for anthropic.Anthropic and remembers how it was constructed."""
    calls = []

    def __init__(self, **kw):
        type(self).calls.append(kw)
        self.messages = None


def test_workspace_header_is_sent_only_when_configured(monkeypatch):
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", _Recorder)
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY)
    _Recorder.calls.clear()
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    assert hwsets.client_headers() == {}
    Extractor()
    assert _Recorder.calls[-1] == {}                                          # default construction, no extra header
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", ' "wrkspc_dummy_123" ')
    assert hwsets.client_headers() == {"anthropic-workspace-id": "wrkspc_dummy_123"}
    Extractor()
    assert _Recorder.calls[-1] == {"default_headers": {"anthropic-workspace-id": "wrkspc_dummy_123"}}
    assert DUMMY not in str(_Recorder.calls)                                  # the key is never passed as a header


def test_describe_api_error_is_one_safe_line_with_a_useful_hint():
    class Err(Exception):
        def __init__(self, status, message):
            super().__init__(message)
            self.status_code, self.message = status, message
    ws = hwsets.describe_api_error(Err(400, "This API key is not scoped to a workspace, so ..."))
    assert "HTTP 400" in ws and "ANTHROPIC_WORKSPACE_ID" in ws and "\n" not in ws
    assert "ANTHROPIC_API_KEY in .env" in hwsets.describe_api_error(Err(401, "invalid x-api-key"))
    assert "Rate limited" in hwsets.describe_api_error(Err(429, "slow down"))
    assert hwsets.describe_api_error(RuntimeError("no status")).startswith("Anthropic API error: no status")


def _api_error():
    import anthropic
    return anthropic.APIError("upstream said no", request=object(), body=None)


def test_cli_reports_api_errors_without_a_traceback(furniture_pdf, tmp_path, monkeypatch, capsys):
    import hwsets.pipeline
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY)
    monkeypatch.setattr(hwsets.pipeline, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(_api_error()))
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(furniture_pdf)
    assert cli.main([str(pdf), "-o", str(tmp_path / "r.json")]) == 3
    err = capsys.readouterr().err
    assert err.startswith("Anthropic API error") and "upstream said no" in err and "Traceback" not in err and DUMMY not in err


def test_api_maps_upstream_failures_to_502(furniture_pdf, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import hwsets.api as api
    monkeypatch.setattr(api, "STORE", tmp_path)
    monkeypatch.delenv("HWSETS_MOCK", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY)
    monkeypatch.setattr(api, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(_api_error()))
    r = TestClient(api.app).post("/extract", files={"file": ("b.pdf", furniture_pdf, "application/pdf")})
    assert r.status_code == 502 and "upstream said no" in r.json()["detail"] and DUMMY not in r.text
