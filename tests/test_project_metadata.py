"""Guards for repository hygiene: dependency lists stay in sync, no secrets / control characters / local paths."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _reqs(path):
    return {re.split(r"[<>=!~ ]", line.strip())[0].lower()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith(("#", "-"))}


def test_requirements_txt_matches_pyproject_dependencies():
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert _reqs(ROOT / "requirements.txt") == {re.split(r"[<>=!~ ]", d)[0].lower() for d in proj["dependencies"]}
    dev = {re.split(r"[<>=!~ ]", d)[0].lower() for d in proj["optional-dependencies"]["dev"]}
    assert _reqs(ROOT / "requirements-dev.txt") == dev
    assert proj["requires-python"] == ">=3.12"


def test_every_pinned_constraint_is_a_declared_dependency():
    declared = _reqs(ROOT / "requirements.txt") | _reqs(ROOT / "requirements-dev.txt") | _reqs(ROOT / "requirements-ui.txt")
    assert _reqs(ROOT / "constraints-tested.txt") <= declared


def test_no_control_characters_or_local_paths_or_keys_in_project_files():
    files = [*ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("ui").rglob("*.py"),
             *ROOT.joinpath("tests").rglob("*.py"), ROOT / "README.md", ROOT / "pyproject.toml", ROOT / ".env.example"]
    for p in files:
        text = p.read_text(encoding="utf-8")
        bad = [c for c in text if (ord(c) < 32 and c not in "\n\r\t") or ord(c) == 0x7F]
        assert not bad, f"control character in {p.name}: {bad[:3]!r}"
        if p.name != Path(__file__).name:
            assert not re.search(r"[A-Za-z]:[\\/](Users|Test_project)|/home/\w+|/Users/\w+", text), f"local path in {p.name}"
            assert not re.search(r"sk-ant-[A-Za-z0-9_\-]{10,}", text), f"API key-like string in {p.name}"


def test_env_example_holds_only_a_safe_placeholder():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    lines = dict(l.split("=", 1) for l in text.splitlines() if "=" in l and not l.lstrip().startswith("#"))
    assert lines["ANTHROPIC_API_KEY"].strip() in ("", "your_key_here")             # a template, never a key
    assert not re.search(r"sk-ant-[A-Za-z0-9_\-]{20,}", text)
    assert not re.search(r"(?i)(api[_-]?key|secret|token|password)\s*=\s*(?!your_key_here\b)[A-Za-z0-9_\-]{24,}", text)


def test_no_tracked_looking_file_contains_a_secret_like_value():
    """Scan every file that is not git-ignored for Anthropic-style and generic key assignments (names only on failure)."""
    ignored_dirs = {".venv", "doc", "__pycache__", "outputs", ".git", ".pytest_cache"}
    rx = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}\b|"
                    r"(?i:(api[_-]?key|secret|token|password))\s*[=:]\s*['\"]?(?!your_key_here|\s|['\"])[A-Za-z0-9_\-]{24,}")
    offenders = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if (not p.is_file() or set(rel.parts) & ignored_dirs or p.name == ".env" or p.suffix in {".pdf", ".png"}
                or rel.parts[0] == "data" and p.suffix == ".pdf"):
            continue
        if rx.search(p.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(rel))                                              # names only - never the value
    assert not offenders, f"secret-like values in: {offenders}"


def test_gitignore_covers_generated_and_sensitive_paths():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = {l.strip() for l in ignored.splitlines()}
    for needed in (".env", ".env.*", "*.env", ".envrc", "!.env.example", "*.pem", "*.key"):
        assert needed in lines, needed                                              # secrets ignored, template kept
    for needed in (".env", "__pycache__/", ".pytest_cache/", "pytest-cache-files-*/", "outputs/", "data/*.pdf", "doc/"):
        assert needed in ignored, needed
    assert "data/ground_truth" not in ignored.replace("# data/ground_truth/", "")     # human labels stay tracked


def test_ui_requirements_match_the_ui_extra_and_the_api_stays_slim():
    """The API (and the Vercel bundle) must not carry the UI stack (streamlit pulls in pandas/pyarrow: hundreds of MB)."""
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    ui = {re.split(r"[<>=!~ ]", d)[0].lower() for d in proj["optional-dependencies"]["ui"]}
    assert _reqs(ROOT / "requirements-ui.txt") == ui
    base = {re.split(r"[<>=!~ ]", d)[0].lower() for d in proj["dependencies"]}
    assert not base & {"streamlit", "pandas", "requests", "pyarrow", "numpy"}
