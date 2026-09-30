"""Hardware-set extraction from Division 08 specbooks."""
import os
from pathlib import Path

# Values that mean "no key has been configured" (an empty entry, or the template placeholder left unedited).
PLACEHOLDER_KEYS = {"", "your_key_here"}


class ConfigurationError(RuntimeError):
    """A setting that live extraction needs (e.g. ANTHROPIC_API_KEY) is missing. Raised only when it is needed."""


def _load_env() -> None:
    """Load KEY=VALUE pairs from the nearest .env (no extra dependency).

    A real, non-empty environment variable always wins. Empty entries in .env are skipped, and an env var that is
    set but blank does not block a value from .env. The template file .env.example is never read.
    """
    for d in [Path.cwd(), *Path(__file__).resolve().parents]:
        f = d / ".env"
        if f.is_file():
            for raw in f.read_text(encoding="utf-8-sig").splitlines():
                k, sep, v = raw.strip().partition("=")
                k, v = k.strip(), v.strip().strip("\"'")
                if sep and k and not k.startswith("#") and v and not os.environ.get(k):
                    os.environ[k] = v
            return


def api_key_configured() -> bool:
    """True when ANTHROPIC_API_KEY holds something other than blank / the template placeholder. Never returns the key."""
    return os.getenv("ANTHROPIC_API_KEY", "").strip().strip("\"'") not in PLACEHOLDER_KEYS


def require_api_key() -> None:
    """Raise a clear ConfigurationError if live extraction is attempted without a key. Offline code never calls this."""
    if not api_key_configured():
        raise ConfigurationError(
            "ANTHROPIC_API_KEY is not set. Put your key in the git-ignored .env file (ANTHROPIC_API_KEY=...) or "
            "export it as an environment variable. Tests, --dry-run, the evaluator, API mock mode "
            "(HWSETS_MOCK=1) and the UI do not need a key."
        )


def client_headers() -> dict[str, str]:
    """Extra headers for the Anthropic client. A key that is not scoped to a workspace needs the workspace id:
    set ANTHROPIC_WORKSPACE_ID (Console > Settings > Workspaces). Empty by default. Never contains the key."""
    ws = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip().strip("\"'")
    return {"anthropic-workspace-id": ws} if ws else {}


def describe_api_error(exc: Exception) -> str:
    """One safe line for an Anthropic SDK error (status + the API's own message; the request and key are never included)."""
    status = getattr(exc, "status_code", None)
    msg = getattr(exc, "message", None) or str(exc)
    hint = ""
    if status == 401:
        hint = " Check ANTHROPIC_API_KEY in .env (revoked or mistyped?)."
    elif status == 400 and "workspace" in str(msg).lower():
        hint = " Use a key created inside a workspace, or set ANTHROPIC_WORKSPACE_ID in .env."
    elif status == 429:
        hint = " Rate limited; retry later or use fewer parallel chunks."
    return f"Anthropic API error{f' (HTTP {status})' if status else ''}: {msg}{hint}"


def output_dir() -> Path:
    """Where dry-run files and stored API results go: $HWSETS_OUTPUT_DIR, else ./outputs (relative to the CWD,
    so it behaves the same from a checkout and from an installed package)."""
    default = "/tmp/hwsets_outputs" if os.getenv("VERCEL") else "outputs"      # Vercel: only /tmp is writable
    return Path(os.getenv("HWSETS_OUTPUT_DIR") or default)


_load_env()
