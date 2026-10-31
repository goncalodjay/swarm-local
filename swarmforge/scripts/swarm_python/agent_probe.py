"""Per-agent discovery for the swarm-init wizard: is the agent installed, is it
authenticated, and which models can it run right now.

The static provider-models.tsv is a guess that goes stale. Each supported CLI
is asked instead, in three tiers:

  1. auth      is the CLI installed and logged in? (never blocks the wizard,
               it only produces a warning with a login hint)
  2. listing   the CLI's own model listing, parsed defensively
  3. fallback  the static TSV rows, used when the CLI is missing, the listing
               command does not exist (Claude, Copilot, Grok), fails, times
               out, or prints something that cannot be parsed

Hermes keeps its dedicated probe (hermes_probe.py) and is delegated to.

Output contract (same as hermes_probe):
  --catalog   stdout: provider<TAB>provider_label<TAB>model_id<TAB>model_label
  --report    stderr: agent<TAB>installed<TAB>auth<TAB>source<TAB>detail<TAB>hint

Nothing here prints a secret: only the NAMES of credentials or environment
variables are reported, never their values.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

AUTH_TIMEOUT = 20
LIST_TIMEOUT = 45

AUTH_OK = "ok"
AUTH_MISSING = "missing"
AUTH_UNKNOWN = "unknown"
AUTH_NOT_INSTALLED = "not-installed"

SOURCE_LIVE = "live"
SOURCE_STATIC = "static"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

LOGIN_HINTS = {
    "claude": "claude auth login",
    "codex": "codex login",
    "opencode": "opencode auth login",
    "pi": "pi, then /login (or set a provider API key variable)",
    "kiro-cli": "kiro-cli login",
    "hermes": "hermes auth add <provider> --type api-key",
    "copilot": "copilot, then /login",
    "grok": "grok login",
}

# Binary name per agent, when it differs from the agent name.
BINARIES = {"kiro-cli": "kiro-cli"}

PI_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
    "MISTRAL_API_KEY",
    "COPILOT_GITHUB_TOKEN",
    "ANTHROPIC_OAUTH_TOKEN",
)

PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openai-codex": "OpenAI Codex",
    "opencode": "OpenCode Zen",
    "opencode-go": "OpenCode Go",
    "openrouter": "OpenRouter",
    "google": "Google",
    "gemini": "Google Gemini",
    "github-copilot": "GitHub Copilot",
    "xai": "xAI",
    "groq": "Groq",
    "mistral": "Mistral",
    "kiro": "Kiro (AWS)",
}

# opencode prints display names in `auth list`; the model ids use provider ids.
OPENCODE_LABEL_ALIASES = {
    "opencodezen": "opencode",
    "githubcopilot": "github-copilot",
}

Row = tuple  # (provider, provider_label, model_id, model_label)


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str = ""


Runner = Callable[[list, int], "RunResult | None"]


@dataclass(frozen=True)
class AgentReport:
    agent: str
    installed: bool
    auth: str
    source: str
    detail: str = ""
    hint: str = ""
    rows: tuple = field(default_factory=tuple)


# ---------------------------------------------------------------- subprocess


def run_command(argv: list, timeout: int) -> "RunResult | None":
    """Run a command without a shell. Returns None when it cannot run or times
    out. argv[0] must already be a resolved path (shutil.which), which also
    makes Windows .cmd shims launchable without shell=True.

    On timeout the whole process tree is killed: a .cmd shim leaves a
    grandchild (node, ...) holding the pipes open, and plain subprocess.run
    would block forever in its post-kill communicate()."""
    popen_kwargs: dict = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            **popen_kwargs,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.communicate(timeout=2)
        except (subprocess.SubprocessError, OSError, ValueError):
            pass  # a grandchild escaped the kill; give up on its pipes
        return None
    except (OSError, subprocess.SubprocessError, ValueError):
        _kill_process_tree(proc)
        return None
    return RunResult(proc.returncode, stdout or "", stderr or "")


def _kill_process_tree(proc: "subprocess.Popen") -> None:
    """Kill proc and every descendant. Best effort, never raises."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _sanitize(value: str) -> str:
    return re.sub(r"[\t\r\n]+", " ", str(value)).strip()


def _safe_token(value) -> str:
    """A short identifier-like value (auth method, account type), or empty.
    Anything else (free text, key fragments) is dropped, not echoed."""
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,24}", value.strip()):
        return value.strip()
    return ""


def codex_auth_label(text: str) -> str:
    """Whitelisted method label from `codex login status` text. The raw text
    can include a masked API-key fragment, so it is never echoed."""
    lowered = text.lower()
    if "chatgpt" in lowered:
        return "ChatGPT"
    if "api key" in lowered or "api-key" in lowered or "apikey" in lowered:
        return "API key"
    return "logged in"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _label_for(provider: str) -> str:
    return PROVIDER_LABELS.get(provider) or provider.replace("-", " ").title()


def parse_json_prefix(text: str):
    """Decode the first JSON value found in text, ignoring any text around it
    (some CLIs append profile information after the JSON). None if absent."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "{[":
            try:
                value, _ = decoder.raw_decode(text[index:])
            except ValueError:
                continue
            return value
    return None


# -------------------------------------------------------------- pure parsers


def parse_claude_auth(text: str, returncode: int) -> tuple:
    """`claude auth status`: JSON with loggedIn and authMethod."""
    data = parse_json_prefix(text)
    if isinstance(data, dict) and "loggedIn" in data:
        if data.get("loggedIn"):
            method = _safe_token(data.get("authMethod"))
            return AUTH_OK, f"auth method: {method}" if method else ""
        return AUTH_MISSING, "not logged in"
    if returncode == 0:
        return AUTH_OK, ""
    return AUTH_MISSING, "not logged in"


def parse_codex_models(text: str) -> list:
    """`codex debug models`: schema undocumented, so look defensively for a
    list of objects with an id-like key. Returns (model_id, label) pairs."""
    data = parse_json_prefix(text)
    items = _find_object_list(data)
    models: list = []
    seen = set()
    for item in items:
        model_id = ""
        for key in ("slug", "id", "model", "name"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                model_id = value.strip()
                break
        if not model_id or model_id in seen:
            continue
        visibility = item.get("visibility")
        if isinstance(visibility, str) and visibility not in ("list", "visible", "public"):
            continue
        label = ""
        for key in ("display_name", "label", "title", "name"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                label = value.strip()
                break
        seen.add(model_id)
        models.append((model_id, label or model_id))
    return models


def _find_object_list(data) -> list:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("models", "data", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for value in data.values():
            if isinstance(value, list) and value and all(isinstance(i, dict) for i in value):
                return value
    return []


def parse_opencode_models(text: str) -> list:
    """`opencode models`: one `provider/model` per line. Returns
    (provider, model_id) pairs in order, skipping anything else."""
    pairs: list = []
    seen = set()
    for raw in strip_ansi(text).splitlines():
        line = raw.strip()
        if not line or " " in line or "/" not in line:
            continue
        provider, model = line.split("/", 1)
        if not provider or not model or (provider, model) in seen:
            continue
        seen.add((provider, model))
        pairs.append((provider, model))
    return pairs


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_opencode_auth(text: str) -> list:
    """`opencode auth list`: lines like `●  Anthropic api` (with ANSI colors).
    Returns the credential display names. Empty when nothing parseable."""
    names: list = []
    for raw in strip_ansi(text).splitlines():
        line = raw.strip()
        if not line.startswith(("●", "*", "-")):
            continue
        body = line.lstrip("●*- ").strip()
        # The last word is the credential type (api, oauth, env...).
        parts = body.split()
        if len(parts) >= 2:
            names.append(" ".join(parts[:-1]))
        elif parts:
            names.append(parts[0])
    return names


def authenticated_opencode_providers(names: list, known: set) -> set:
    """Map auth display names to provider ids present in the model listing."""
    by_norm = {_normalize(provider): provider for provider in known}
    matched = set()
    for name in names:
        norm = _normalize(name)
        norm = _normalize(OPENCODE_LABEL_ALIASES.get(norm, norm))
        if norm in by_norm:
            matched.add(by_norm[norm])
    return matched


def unverified_opencode_providers(pairs: list, auth_names: list) -> set:
    """Providers in the listing that could not be matched to a stored
    credential. They are NOT hidden (a provider may be authenticated through an
    environment variable, or its display name may not match its id); callers
    mark them instead. Empty when nothing matched at all, since then the auth
    list is unparseable and no provider can be called unverified."""
    known = {provider for provider, _ in pairs}
    matched = authenticated_opencode_providers(auth_names, known)
    if not matched:
        return set()
    matched.add("opencode")
    return known - matched


UNVERIFIED_SUFFIX = " (no verificado)"


def parse_pi_models(text: str) -> list:
    """`pi --list-models`: a text table. Parse defensively: skip headers and
    messages, take `provider model` from the first two columns. Returns
    (provider, model_id) pairs."""
    pairs: list = []
    seen = set()
    for raw in strip_ansi(text).splitlines():
        line = raw.strip()
        if not line or line.startswith(("No models", "Use ", "See", "-", "=")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        provider, model = parts[0], parts[1]
        if provider.lower() == "provider" and model.lower() == "model":
            continue
        if "/" in provider or ":" in provider or "\\" in provider:
            continue
        if (provider, model) in seen:
            continue
        seen.add((provider, model))
        pairs.append((provider, model))
    return pairs


def parse_kiro_models(text: str) -> list:
    """`kiro-cli chat --list-models --format json`. Returns (model_id, label)."""
    data = parse_json_prefix(text)
    models: list = []
    seen = set()
    for item in _find_object_list(data):
        model_id = item.get("model_id") or item.get("model_name")
        if not isinstance(model_id, str) or not model_id.strip() or model_id in seen:
            continue
        label = item.get("model_name")
        seen.add(model_id)
        models.append((model_id.strip(), label.strip() if isinstance(label, str) and label.strip() else model_id))
    return models


def parse_kiro_whoami(text: str, returncode: int) -> tuple:
    """`kiro-cli whoami --format json`."""
    if returncode != 0:
        return AUTH_MISSING, "not logged in"
    data = parse_json_prefix(text)
    if isinstance(data, dict):
        kind = _safe_token(data.get("accountType"))
        return AUTH_OK, f"account type: {kind}" if kind else ""
    return AUTH_UNKNOWN, "unexpected whoami output"


def detect_pi_auth(env: Mapping, home: Path) -> tuple:
    """Pi has no auth command: look at auth.json and provider variables.
    Only names are reported, never values."""
    names: list = []
    auth_file = Path(home) / ".pi" / "agent" / "auth.json"
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            names.extend(f"auth.json:{key}" for key in sorted(data) if data.get(key))
    except (OSError, ValueError):
        pass
    names.extend(f"env:{var}" for var in PI_ENV_VARS if env.get(var))
    if names:
        return AUTH_OK, ", ".join(names)
    return AUTH_MISSING, "no credentials found"


# ------------------------------------------------------------------- probes


@dataclass
class Context:
    binary: str
    runner: Runner
    env: Mapping
    home: Path


def _run(ctx: Context, args: list, timeout: int) -> "RunResult | None":
    return ctx.runner([ctx.binary, *args], timeout)


def _auth_claude(ctx: Context) -> tuple:
    result = _run(ctx, ["auth", "status"], AUTH_TIMEOUT)
    if result is None:
        return AUTH_UNKNOWN, "auth status unavailable"
    return parse_claude_auth(result.stdout, result.returncode)


def _auth_codex(ctx: Context) -> tuple:
    result = _run(ctx, ["login", "status"], AUTH_TIMEOUT)
    if result is None:
        return AUTH_UNKNOWN, "login status unavailable"
    text = _sanitize(strip_ansi(result.stdout or result.stderr))
    if result.returncode == 0:
        return AUTH_OK, codex_auth_label(text)
    return AUTH_MISSING, "not logged in"


def _models_codex(ctx: Context) -> list:
    result = _run(ctx, ["debug", "models"], LIST_TIMEOUT)
    if result is None or result.returncode != 0:
        return []
    return [("openai", "OpenAI", mid, label) for mid, label in parse_codex_models(result.stdout)]


def _opencode_auth_names(ctx: Context) -> "list | None":
    result = _run(ctx, ["auth", "list"], AUTH_TIMEOUT)
    if result is None or result.returncode != 0:
        return None
    return parse_opencode_auth(result.stdout)


def _auth_opencode(ctx: Context) -> tuple:
    names = _opencode_auth_names(ctx)
    if names is None:
        return AUTH_UNKNOWN, "auth list unavailable"
    if names:
        return AUTH_OK, ", ".join(names)
    return AUTH_MISSING, "no credentials stored"


def _models_opencode(ctx: Context) -> list:
    result = _run(ctx, ["models"], LIST_TIMEOUT)
    if result is None or result.returncode != 0:
        return []
    pairs = parse_opencode_models(result.stdout)
    unverified = unverified_opencode_providers(pairs, _opencode_auth_names(ctx) or [])
    # Verified providers first (stable), unverified ones marked, none hidden.
    ordered = sorted(pairs, key=lambda pair: pair[0] in unverified)
    return [
        (p, _label_for(p) + (UNVERIFIED_SUFFIX if p in unverified else ""), m, m)
        for p, m in ordered
    ]


def _auth_pi(ctx: Context) -> tuple:
    return detect_pi_auth(ctx.env, ctx.home)


def _models_pi(ctx: Context) -> list:
    result = _run(ctx, ["--list-models"], LIST_TIMEOUT)
    if result is None or result.returncode != 0:
        return []
    return [(p, _label_for(p), m, m) for p, m in parse_pi_models(result.stdout)]


def _auth_kiro(ctx: Context) -> tuple:
    result = _run(ctx, ["whoami", "--format", "json"], AUTH_TIMEOUT)
    if result is None:
        return AUTH_UNKNOWN, "whoami unavailable"
    return parse_kiro_whoami(result.stdout, result.returncode)


def _models_kiro(ctx: Context) -> list:
    result = _run(ctx, ["chat", "--list-models", "--format", "json"], LIST_TIMEOUT)
    if result is None or result.returncode != 0:
        return []
    return [("kiro", "Kiro (AWS)", mid, label) for mid, label in parse_kiro_models(result.stdout)]


def _auth_unknown(ctx: Context) -> tuple:
    return AUTH_UNKNOWN, "no auth command known"


# agent -> (auth probe, live model listing or None)
PROBES = {
    "claude": (_auth_claude, None),
    "codex": (_auth_codex, _models_codex),
    "opencode": (_auth_opencode, _models_opencode),
    "pi": (_auth_pi, _models_pi),
    "kiro-cli": (_auth_kiro, _models_kiro),
    "copilot": (_auth_unknown, None),
    "grok": (_auth_unknown, None),
}

SUPPORTED = tuple(PROBES)


def load_static_rows(agent: str, tsv_path: "str | Path | None") -> list:
    """The swarm's provider-models.tsv rows for one agent."""
    if not tsv_path:
        return []
    try:
        text = Path(tsv_path).read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list = []
    for line in text.splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) < 5 or fields[0] != agent:
            continue
        provider, provider_label, model_id, model_label = fields[1:5]
        if provider and model_id:
            rows.append((provider, provider_label or provider, model_id, model_label or model_id))
    return rows


def probe(
    agent: str,
    static_rows: list,
    runner: Runner = run_command,
    which: Callable = shutil.which,
    env: "Mapping | None" = None,
    home: "Path | None" = None,
) -> AgentReport:
    """Run the three tiers for one agent. Never raises."""
    hint = LOGIN_HINTS.get(agent, "")
    static = tuple(static_rows)
    if agent not in PROBES:
        return AgentReport(agent, False, AUTH_UNKNOWN, SOURCE_STATIC, "unsupported agent", hint, static)
    resolved = which(BINARIES.get(agent, agent))
    if not resolved:
        return AgentReport(agent, False, AUTH_NOT_INSTALLED, SOURCE_STATIC, "executable not found on PATH", hint, static)

    ctx = Context(
        binary=resolved,
        runner=runner,
        env=os.environ if env is None else env,
        home=Path.home() if home is None else home,
    )
    auth_probe, models_probe = PROBES[agent]
    try:
        auth, detail = auth_probe(ctx)
    except Exception as error:  # a probe must never break the wizard
        auth, detail = AUTH_UNKNOWN, f"auth check failed: {type(error).__name__}"
    live: list = []
    if models_probe is not None:
        try:
            live = models_probe(ctx)
        except Exception:
            live = []
    if live:
        return AgentReport(agent, True, auth, SOURCE_LIVE, detail, hint, tuple(live))
    return AgentReport(agent, True, auth, SOURCE_STATIC, detail, hint, static)


# --------------------------------------------------------------------- CLI


def catalog_rows(report: AgentReport) -> list:
    return [tuple(_sanitize(field) for field in row) for row in report.rows]


def report_line(report: AgentReport) -> str:
    fields = (
        report.agent,
        "yes" if report.installed else "no",
        report.auth,
        report.source,
        # Empty fields become "-": tab is shell whitespace, so `read` would
        # collapse consecutive tabs and shift the columns.
        _sanitize(report.detail) or "-",
        _sanitize(report.hint) or "-",
    )
    return "\t".join(fields)


def _force_lf_output() -> None:
    """Always emit LF. The wizard splits lines on LF only; Windows text mode
    would write CRLF and leave a trailing CR on the last field."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(newline="\n")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: "list | None" = None) -> int:
    _force_lf_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    agent = ""
    static_path = None
    want_report = "--report" in argv
    want_catalog = "--catalog" in argv or not want_report
    for index, value in enumerate(argv):
        if value == "--agent" and index + 1 < len(argv):
            agent = argv[index + 1]
        elif value == "--static-catalog" and index + 1 < len(argv):
            static_path = argv[index + 1]
    if not agent:
        print("usage: agent_probe.py --agent NAME [--catalog] [--report] [--static-catalog TSV]", file=sys.stderr)
        return 2

    if agent == "hermes":
        # Run as a script (the wizard does) there is no package context.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import hermes_probe

        forwarded = []
        if want_catalog:
            forwarded.append("--catalog")
        if want_report:
            forwarded.append("--report")
        if static_path:
            forwarded += ["--static-catalog", static_path]
        return hermes_probe.main(forwarded)

    report = probe(agent, load_static_rows(agent, static_path))
    if want_catalog:
        for row in catalog_rows(report):
            print("\t".join(row))
    if want_report:
        print(report_line(report), file=sys.stderr)
    return 0 if report.rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
