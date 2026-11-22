"""What Hermes can actually run: the providers it has credentials for, and the
models each of them serves.

Hermes keeps its own credential store and its own model catalog, both
separate from the swarm's static provider-models.tsv. That table is a guess:
it lists providers a machine may or may not have configured, and it cannot
know that a key expired. A dead key only shows up when the agent is already
running, as a 401 twenty seconds after the swarm starts. So the wizard asks
Hermes instead:

  hermes auth list              which providers have credentials, which one
                                the pool will use, and the last failure it
                                recorded for that one
  provider_models_cache.json    which models each provider serves (Hermes'
                                own model picker cache)
  a real call                   the only ground truth about a credential: the
                                recorded failure is a LAST attempt and stays
                                stale after the key is replaced, so the wizard
                                can verify a provider before offering it

Nothing here prints a secret: `hermes auth list` is already redacted and the
model cache holds only model names.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# A credential whose last attempt failed (401/403, revoked, expired). This is
# a historical record, not a live health check: a key that was replaced still
# carries the marker until something overwrites it.
BROKEN_MARKERS = (
    "auth failed",
    "unauthorized",
    "forbidden",
    "expired",
    "revoked",
    "invalid token",
    "invalid_grant",
)
# A credential that is valid but temporarily unusable (quota, rate limit).
DEGRADED_MARKERS = (
    "rate-limited",
    "rate limit",
    "too many requests",
    "quota",
    "exhausted",
    "usage_limit",
)
# What a provider answers when the CREDENTIAL is the problem. Every other
# answer (a deprecated model, a model the plan does not cover) means the key
# did authenticate.
AUTH_FAILURE_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "api key has expired",
    "invalid_api_key",
)

# How many models to try when verifying a provider.
VERIFY_MODELS = 3

_PROVIDER_RE = re.compile(r"^(?P<name>\S+)\s+\((?P<count>\d+)\s+credentials?\):\s*$")
_CREDENTIAL_RE = re.compile(r"^\s+#(?P<index>\d+)\s+(?P<rest>\S.*?)\s*$")
_PRIORITY_RE = re.compile(r"priority=(?P<priority>\d+)")
_MARKER_RE = re.compile(r"[\u2190\u2192]")
_ACTIVE_MARKER = "\u2190"

# Hermes' own display names for the providers a machine is likely to have
# credentials for. Hermes keeps them in its internal catalog, which the
# wizard cannot import (it runs on the system python, not Hermes' venv).
PROVIDER_LABELS = {
    "nous": "Nous Portal",
    "openrouter": "OpenRouter",
    "anthropic": "Anthropic",
    "openai-codex": "ChatGPT or Codex Subscription",
    "openai-api": "OpenAI API",
    "gemini": "Google AI Studio",
    "deepseek": "DeepSeek",
    "xai": "xAI",
    "copilot": "GitHub Copilot",
    "opencode-go": "OpenCode Go",
    "opencode-zen": "OpenCode Zen",
    "kimi-coding": "Kimi / Kimi Coding Plan",
    "minimax": "MiniMax",
    "ollama-cloud": "Ollama Cloud",
    "fireworks": "Fireworks AI",
}

# The provider can serve a request.
STATE_OK = "ok"
# The credential the pool will use recorded a failure on its last attempt.
# It may be stale: the marker survives a key replacement.
STATE_SUSPECT = "suspect"
# A real call to the provider succeeded.
STATE_VERIFIED = "verified"
# A real call to the provider failed.
STATE_FAILED = "failed"

USABLE_STATES = (STATE_OK, STATE_VERIFIED, STATE_SUSPECT)


@dataclass(frozen=True)
class Credential:
    label: str
    status: str
    priority: int
    active: bool
    id: str = ""

    @property
    def broken(self) -> bool:
        lowered = self.status.lower()
        return any(marker in lowered for marker in BROKEN_MARKERS)

    @property
    def degraded(self) -> bool:
        lowered = self.status.lower()
        return any(marker in lowered for marker in DEGRADED_MARKERS)


@dataclass(frozen=True)
class ProviderAuth:
    name: str
    credentials: tuple[Credential, ...]

    @property
    def active_credential(self) -> Credential | None:
        """The credential the pool will use: the one Hermes marks as current,
        or the first by priority."""
        for credential in self.credentials:
            if credential.active:
                return credential
        if not self.credentials:
            return None
        return min(self.credentials, key=lambda credential: credential.priority)

    @property
    def last_failure(self) -> str:
        """The failure recorded for the credential that will be used, if any."""
        credential = self.active_credential
        if credential is None:
            return ""
        if credential.broken:
            return credential.status
        if credential.degraded:
            return credential.status
        return ""

    @property
    def state(self) -> str:
        credential = self.active_credential
        if credential is None:
            return STATE_SUSPECT
        if credential.broken:
            return STATE_SUSPECT
        if credential.degraded:
            return STATE_SUSPECT
        return STATE_OK


@dataclass(frozen=True)
class StaticCatalog:
    """The swarm's own provider-models.tsv rows for hermes: labels and the
    fallback models for a provider Hermes' cache does not know yet."""

    labels: dict[str, str]
    models: dict[str, tuple[tuple[str, str], ...]]

    @classmethod
    def from_tsv(cls, path: str | Path) -> "StaticCatalog":
        labels: dict[str, str] = {}
        models: dict[str, list[tuple[str, str]]] = {}
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return cls({}, {})
        for line in text.splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) < 5 or fields[0] != "hermes":
                continue
            provider, provider_label, model_id, model_label = fields[1:5]
            if not provider or not model_id:
                continue
            labels.setdefault(provider, provider_label or provider)
            models.setdefault(provider, []).append((model_id, model_label or model_id))
        return cls(labels, {key: tuple(value) for key, value in models.items()})

    def label_for(self, provider: str) -> str:
        return self.labels.get(provider) or PROVIDER_LABELS.get(provider, provider)

    def models_for(self, provider: str) -> tuple[tuple[str, str], ...]:
        return self.models.get(provider, ())


def parse_auth_list(text: str) -> list[ProviderAuth]:
    """Parse `hermes auth list` output.

    The provider line names the provider and its credential count; each
    credential line follows, indented, with its label, type, id, priority, the
    auth status Hermes recorded for it, and a marker on the credential the
    pool will use.
    """
    providers: list[ProviderAuth] = []
    current: list[Credential] = []
    current_name = ""
    for raw_line in text.splitlines():
        provider_match = _PROVIDER_RE.match(raw_line)
        if provider_match:
            if current_name:
                _merge_provider(providers, current_name, current)
            current_name = provider_match.group("name")
            current = []
            continue
        credential_match = _CREDENTIAL_RE.match(raw_line)
        if credential_match and current_name:
            current.append(_parse_credential(credential_match.group("rest")))
    if current_name:
        _merge_provider(providers, current_name, current)
    return providers


def _parse_credential(rest: str) -> Credential:
    label = rest.split()[0] if rest.split() else ""
    priority_match = _PRIORITY_RE.search(rest)
    priority = int(priority_match.group("priority")) if priority_match else 0
    id_match = re.search(r"\bid=(\S+)", rest)
    # The row reads: label, type, id, priority, source, status, marker.
    # The status is what follows the priority field.
    status = rest.split("priority=", 1)[1] if "priority=" in rest else rest
    status = re.sub(r"^\d+\s*", "", status)
    active = _ACTIVE_MARKER in rest
    status = _MARKER_RE.sub("", status).strip()
    return Credential(
        label=label,
        status=status,
        priority=priority,
        active=active,
        id=id_match.group(1) if id_match else "",
    )


def _merge_provider(providers: list[ProviderAuth], name: str, credentials: list[Credential]) -> None:
    """Add a provider block, folding it into an existing one with the same
    name: Hermes lists a provider once, but a split block must not read as a
    provider with no working credential."""
    for index, provider in enumerate(providers):
        if provider.name == name:
            providers[index] = ProviderAuth(
                name, provider.credentials + tuple(credentials)
            )
            return
    providers.append(ProviderAuth(name, tuple(credentials)))


def hermes_home(hermes_bin: str = "hermes") -> Path | None:
    """The Hermes home directory, read from its own config path."""
    result = _run([hermes_bin, "config", "path"], timeout=30)
    if result is None or result.returncode != 0:
        return None
    path = (result.stdout or "").strip()
    if not path:
        return None
    return Path(path).parent


def load_model_cache(home: Path | None) -> dict[str, list[str]]:
    """Hermes' model picker cache: provider -> the models it serves."""
    if home is None:
        return {}
    try:
        raw = json.loads((home / "provider_models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    models: dict[str, list[str]] = {}
    for provider, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        names = entry.get("models")
        if not isinstance(names, list):
            continue
        clean = [str(name) for name in names if isinstance(name, str) and name]
        if clean:
            models[str(provider)] = clean
    return models


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def read_auth_list(hermes_bin: str = "hermes") -> list[ProviderAuth]:
    """Ask Hermes which providers have credentials. Never raises."""
    if shutil.which(hermes_bin) is None:
        return []
    result = _run([hermes_bin, "auth", "list"], timeout=60)
    if result is None or result.returncode != 0:
        return []
    return parse_auth_list(result.stdout or "")


def verify_provider(
    hermes_bin: str,
    provider: str,
    models: list[tuple[str, str]],
    timeout: int = 120,
) -> tuple[str, str, str]:
    """Check a provider with real calls: the only ground truth about a
    credential.

    `hermes auth list` records the LAST attempt, and that marker survives a
    key replacement, so it cannot say whether a provider works right now.
    A call tells more than that: a 401 means the credential is dead, while
    any other answer (a deprecated model, a model the plan does not cover)
    means the provider DID authenticate the key. The calls are tagged
    `--source tool` so their sessions stay out of the user's session list.

    Returns (state, detail, model used).
    """
    model_error = ""
    for model_id, _ in models[:VERIFY_MODELS]:
        ok, error = _one_call(hermes_bin, provider, model_id, timeout)
        if ok:
            return STATE_VERIFIED, "", model_id
        if _is_auth_failure(error):
            return STATE_FAILED, error, ""
        model_error = error
    if model_error:
        # The provider answered, so the key authenticated: the model is the
        # problem, not the credential.
        return STATE_SUSPECT, model_error, ""
    return STATE_SUSPECT, "sin respuesta del provider", ""


def _one_call(hermes_bin: str, provider: str, model: str, timeout: int) -> tuple[bool, str]:
    result = _run(
        [
            hermes_bin,
            "chat",
            "--source",
            "tool",
            "--oneshot",
            "-q",
            "ping",
            "--provider",
            provider,
            "--model",
            model,
        ],
        timeout=timeout,
    )
    if result is None:
        return False, "hermes no respondió"
    if result.returncode == 0:
        return True, ""
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return False, _error_line(output)


def _is_auth_failure(error: str) -> bool:
    lowered = error.lower()
    return any(marker in lowered for marker in AUTH_FAILURE_MARKERS)


def _error_line(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("Error", "❌", "⚠")) or "rejected" in stripped:
            return stripped
    return output.splitlines()[-1].strip() if output.splitlines() else "fallo desconocido"


def _credential_label(provider: ProviderAuth) -> str:
    """The credential the pool will use, as "label (id)"."""
    credential = provider.active_credential
    if credential is None:
        return ""
    if credential.label and credential.id:
        return f"{credential.label} ({credential.id})"
    return credential.label or credential.id


def probe(
    hermes_bin: str = "hermes",
    static: StaticCatalog | None = None,
    verify: bool = False,
) -> dict:
    """The full picture: every provider Hermes knows about, the credential the
    pool will use, the models it serves, and (optionally) whether a real call
    to it succeeds."""
    static = static or StaticCatalog({}, {})
    providers = read_auth_list(hermes_bin)
    models = load_model_cache(hermes_home(hermes_bin))
    catalog: list[dict] = []
    for provider in providers:
        cached = [(model_id, model_id) for model_id in models.get(provider.name, [])]
        # The swarm's curated models come first: they are the ones a human
        # picked, and the provider's own list is unordered (it can start with
        # models the provider has already deprecated).
        curated = list(static.models_for(provider.name))
        seen = {model_id for model_id, _ in curated}
        entries = curated + [entry for entry in cached if entry[0] not in seen]
        state = provider.state
        detail = provider.last_failure
        if verify and entries:
            state, detail, used_model = verify_provider(hermes_bin, provider.name, entries)
            if used_model:
                detail = f"verificado con {used_model}"
        elif verify and not entries:
            detail = "sin modelos conocidos para verificar"
        catalog.append(
            {
                "name": provider.name,
                "label": static.label_for(provider.name),
                "state": state,
                "detail": detail,
                "credential": _credential_label(provider),
                "usable": state in USABLE_STATES,
                "models": entries,
            }
        )
    return {"providers": catalog, "verified": verify}


def catalog_rows(report: dict) -> list[tuple[str, str, str, str]]:
    """Catalog lines for the providers that can serve a request: provider,
    label, model id, model label.

    A provider whose last attempt failed still gets offered when no live
    check ran: the marker is stale more often than not. A provider that
    failed the live check does not.
    """
    rows: list[tuple[str, str, str, str]] = []
    for provider in report.get("providers", []):
        state = provider.get("state", STATE_SUSPECT)
        if state == STATE_FAILED:
            continue
        if not provider.get("usable"):
            continue
        label = provider.get("label") or provider.get("name") or ""
        for model_id, model_label in provider.get("models", []):
            rows.append((provider.get("name", ""), label, model_id, model_label or model_id))
    return rows


def report_rows(report: dict) -> list[tuple[str, str, str, str, str]]:
    """Provider, state, label, detail and the credential the pool will use,
    for the wizard's status summary."""
    rows = []
    for provider in report.get("providers", []):
        credential = provider.get("credential", "")
        rows.append(
            (
                provider.get("name", ""),
                provider.get("state", STATE_SUSPECT),
                provider.get("label") or provider.get("name") or "",
                provider.get("detail", ""),
                credential,
            )
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    hermes_bin = "hermes"
    static_path: str | None = None
    want_catalog = "--catalog" in argv
    want_report = "--report" in argv
    verify = "--verify" in argv
    for index, value in enumerate(argv):
        if value == "--hermes-bin" and index + 1 < len(argv):
            hermes_bin = argv[index + 1]
        elif value == "--static-catalog" and index + 1 < len(argv):
            static_path = argv[index + 1]
    if not want_catalog and not want_report:
        want_catalog = True

    static = StaticCatalog.from_tsv(static_path) if static_path else StaticCatalog({}, {})
    if shutil.which(hermes_bin) is None:
        print(f"hermes executable not found: {hermes_bin}", file=sys.stderr)
        return 2

    report = probe(hermes_bin, static, verify=verify)
    if want_catalog:
        for provider, label, model_id, model_label in catalog_rows(report):
            print(f"{provider}\t{label}\t{model_id}\t{model_label}")
    if want_report:
        for provider, state, label, detail, credential in report_rows(report):
            print(f"{provider}\t{state}\t{label}\t{detail}\t{credential}", file=sys.stderr)
    if not any(provider.get("usable") for provider in report.get("providers", [])):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
