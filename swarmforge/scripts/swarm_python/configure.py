#!/usr/bin/env python3
"""`swarm-kura change`: re-pick the agent, model or thinking level of some
roles without re-running the whole installation wizard.

Edits swarmforge/swarmforge.conf in place: only the rows of the chosen roles
change; comments, order, worktrees and receive modes are kept, and the previous
file is saved as swarmforge.conf.bak.<timestamp>.

Interactive (no flags):   configure.py [--project DIR] [role ...]
Scripted examples:
  configure.py coder --model opencode-go/kimi-k3
  configure.py --from-agent claude --agent codex --model gpt-5.6-luna
  configure.py --all --thinking high
  configure.py --list

A role whose agent or CLI arguments change starts a fresh agent session on its
next launch (the scheduler compares the recorded backend fingerprint), so no
state has to be edited here. A running swarm picks the change up on restart.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

SUPPORTED_AGENTS = ["claude", "codex", "copilot", "grok", "hermes", "kiro-cli", "opencode", "pi"]
THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
RECEIVE_MODES = ("task", "batch")
# Same table as swarm-init's AUTO_APPROVE_FLAGS. pi asks no per-tool permission.
AUTO_APPROVE_FLAGS = {
    "claude": ["--dangerously-skip-permissions"],
    "codex": ["--dangerously-bypass-approvals-and-sandbox"],
    "copilot": ["--allow-all"],
    "grok": ["--permission-mode", "bypassPermissions"],
    "hermes": ["--yolo"],
    "kiro-cli": ["--trust-all-tools"],
    "opencode": ["--auto"],
}
# Agents whose --model carries the provider as "<provider>/<model>".
SLASH_PROVIDER_AGENTS = {"opencode", "pi"}
# Agents where the wizard's thinking level reaches the CLI.
THINKING_AGENTS = {"hermes", "pi", "claude", "codex"}

BOLD = "\033[1m" if sys.stderr.isatty() else ""
DIM = "\033[2m" if sys.stderr.isatty() else ""
GREEN = "\033[32m" if sys.stderr.isatty() else ""
YELLOW = "\033[33m" if sys.stderr.isatty() else ""
RED = "\033[31m" if sys.stderr.isatty() else ""
RESET = "\033[0m" if sys.stderr.isatty() else ""


class ChangeError(Exception):
    pass


def say(msg: str = "") -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------
# swarmforge.conf model
# --------------------------------------------------------------------------

@dataclass
class Backend:
    """What a role's `window` row asks of its agent CLI."""
    agent: str
    provider: str = ""
    model: str = ""
    thinking: str = ""
    auto_approve: bool = False
    others: list[str] = field(default_factory=list)  # args the wizard does not own

    def model_display(self) -> str:
        if not self.model:
            return "(sin modelo)"
        if self.agent in SLASH_PROVIDER_AGENTS and self.provider:
            return f"{self.provider}/{self.model}"
        if self.agent == "hermes" and self.provider:
            return f"{self.provider} / {self.model}"
        return self.model


@dataclass
class Row:
    line_index: int
    role: str
    worktree: str
    receive_mode: str  # "" when the row did not spell one out
    backend: Backend


def parse_backend(agent: str, args: list[str]) -> Backend:
    b = Backend(agent=agent)
    auto = AUTO_APPROVE_FLAGS.get(agent, [])
    i = 0
    while i < len(args):
        tok = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if auto and args[i:i + len(auto)] == auto:
            b.auto_approve = True
            i += len(auto)
            continue
        if tok == "--model" and nxt is not None:
            if agent in SLASH_PROVIDER_AGENTS and "/" in nxt:
                b.provider, b.model = nxt.split("/", 1)
            else:
                b.model = nxt
            i += 2
            continue
        if tok.startswith("--model="):
            value = tok.split("=", 1)[1]
            if agent in SLASH_PROVIDER_AGENTS and "/" in value:
                b.provider, b.model = value.split("/", 1)
            else:
                b.model = value
            i += 1
            continue
        if agent == "hermes" and tok == "--provider" and nxt is not None:
            b.provider = nxt
            i += 2
            continue
        if ((agent == "hermes" and tok == "--reasoning") or (agent == "pi" and tok == "--thinking")
                or (agent == "claude" and tok == "--effort")) and nxt is not None:
            b.thinking = nxt
            i += 2
            continue
        if agent == "codex" and tok == "-c" and nxt is not None and nxt.startswith("model_reasoning_effort="):
            b.thinking = nxt.split("=", 1)[1]
            i += 2
            continue
        b.others.append(tok)
        i += 1
    if agent in ("hermes", "claude", "codex") and not b.thinking and b.model:
        b.thinking = "off"
    return b


def render_backend(b: Backend) -> list[str]:
    """CLI args for a backend; mirrors swarm-init's build_config_line."""
    out: list[str] = []
    thinking = b.thinking or "medium"
    if b.agent == "hermes":
        if b.provider:
            out += ["--provider", b.provider]
        out += ["--model", b.model]
        if thinking != "off":
            out += ["--reasoning", thinking]
    elif b.agent in SLASH_PROVIDER_AGENTS:
        model = f"{b.provider}/{b.model}" if b.provider and b.provider != "default" else b.model
        out += ["--model", model]
        if b.agent == "pi":
            out += ["--thinking", thinking]
    elif b.agent == "claude":
        out += ["--model", b.model]
        if thinking != "off":
            out += ["--effort", thinking]
    elif b.agent == "codex":
        out += ["--model", b.model]
        if thinking != "off":
            out += ["-c", f"model_reasoning_effort={thinking}"]
    else:
        out += ["--model", b.model]
    out += b.others
    if b.auto_approve:
        out += AUTO_APPROVE_FLAGS.get(b.agent, [])
    return out


def render_row(row: Row) -> str:
    parts = ["window", row.role, row.backend.agent, row.worktree]
    if row.receive_mode:
        parts.append(row.receive_mode)
    parts += render_backend(row.backend)
    return " ".join(parts)


def parse_conf(text: str) -> tuple[list[str], list[Row]]:
    lines = text.splitlines()
    rows: list[Row] = []
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if fields[0] != "window" or len(fields) < 4:
            continue
        _, role, agent, worktree, *rest = fields
        mode = ""
        if rest and rest[0] in RECEIVE_MODES:
            mode, rest = rest[0], rest[1:]
        rows.append(Row(idx, role, worktree, mode, parse_backend(agent.lower(), rest)))
    return lines, rows


# --------------------------------------------------------------------------
# Catalog discovery (live per agent, static TSV as fallback)
# --------------------------------------------------------------------------

def find_static_catalog(explicit: str | None, project: Path) -> Path | None:
    candidates = [explicit] if explicit else []
    candidates += [
        os.environ.get("SWARM_KURA_CATALOG", ""),
        str(project / "swarmforge" / "provider-models.tsv"),
    ]
    # Run from a swarm-kura install / the repo: <root>/template/swarmforge/scripts/swarm_python.
    if HERE.parents[2].name == "template":
        candidates.append(str(HERE.parents[3] / "provider-models.tsv"))
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def load_catalog(agent: str, static: Path | None, verify_hermes: bool = False) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """(rows, report lines). rows = (provider, provider_label, model_id, model_label)."""
    if agent == "hermes":
        cmd = [sys.executable, str(HERE / "hermes_probe.py"), "--catalog", "--report"]
        if verify_hermes:
            cmd.append("--verify")
    else:
        cmd = [sys.executable, str(HERE / "agent_probe.py"), "--agent", agent, "--catalog", "--report"]
    if static:
        cmd += ["--static-catalog", str(static)]
    rows: list[tuple[str, str, str, str]] = []
    report: list[str] = []
    if shutil.which(agent) or agent != "hermes":
        say(f"{DIM}Consultando los modelos de {agent}...{RESET}")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=180)
            for line in proc.stdout.splitlines():
                parts = line.rstrip("\r").split("\t")
                if len(parts) >= 3 and parts[0] and parts[2]:
                    parts += [""] * (4 - len(parts))
                    rows.append((parts[0], parts[1] or parts[0], parts[2], parts[3] or parts[2]))
            report = [l for l in proc.stderr.splitlines() if l.strip()]
        except (OSError, subprocess.SubprocessError) as exc:
            report = [f"probe falló: {exc}"]
    if not rows and static:
        rows = static_rows(agent, static)
    return rows, report


def static_rows(agent: str, static: Path) -> list[tuple[str, str, str, str]]:
    rows = []
    for n, line in enumerate(static.read_text(encoding="utf-8").splitlines()):
        if n == 0:
            continue
        parts = line.split("\t")
        if len(parts) >= 5 and parts[0] == agent and parts[1] and parts[3]:
            rows.append((parts[1], parts[2] or parts[1], parts[3], parts[4] or parts[3]))
    return rows


def describe_report(agent: str, report: list[str]) -> None:
    if agent == "hermes":
        for line in report:
            fields = line.split("\t")
            if len(fields) >= 3:
                mark = {"ok": "✓", "verified": "✓", "suspect": "~", "failed": "!"}.get(fields[1], "·")
                say(f"  {mark} {fields[0]:<22} {fields[2]}" + (f" ({fields[3]})" if len(fields) > 3 and fields[3] else ""))
            else:
                say(f"  ! {line}")
        return
    if not report:
        return
    fields = report[0].split("\t") + [""] * 6
    _, installed, auth, source, detail, hint = fields[:6]
    detail = "" if detail == "-" else detail
    hint = "" if hint == "-" else hint
    if auth == "not-installed":
        say(f"  {YELLOW}! {agent} no está instalado en este equipo (no está en el PATH).{RESET}")
    elif auth == "missing":
        say(f"  {YELLOW}! {agent} no está autenticado{f' ({detail})' if detail else ''}. Iniciá sesión con: {hint}{RESET}")
    elif auth == "ok":
        say(f"  {GREEN}✓ {agent} está autenticado{f' ({detail})' if detail else ''}{RESET}")
    if source == "static" and installed == "yes":
        say(f"  {DIM}Este CLI no lista modelos en vivo; la lista es estática. Usá \"Otro\" para cualquier id.{RESET}")


# --------------------------------------------------------------------------
# Interactive prompts
# --------------------------------------------------------------------------

def ask(prompt: str) -> str:
    try:
        sys.stderr.write(prompt)
        sys.stderr.flush()
        line = sys.stdin.readline()
    except KeyboardInterrupt:
        raise ChangeError("cancelado")
    if line == "":
        raise ChangeError("entrada cerrada (sin terminal)")
    return line.strip()


def choose(title: str, options: list[str], default: int | None = None, allow_back: bool = False) -> int:
    """Index of the picked option; -1 for back."""
    while True:
        say(f"\n{BOLD}{title}{RESET}")
        for i, opt in enumerate(options, 1):
            mark = f" {DIM}(actual){RESET}" if default is not None and i - 1 == default else ""
            say(f"  {i}) {opt}{mark}")
        if allow_back:
            say("  0) volver")
        hint = f" (Enter = {default + 1})" if default is not None else ""
        answer = ask(f"Opción [1-{len(options)}]{hint}: ")
        if not answer and default is not None:
            return default
        if allow_back and answer == "0":
            return -1
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        say(f"{RED}Selección no válida.{RESET}")


def ask_identifier(label: str, current: str = "") -> str:
    while True:
        hint = f" (Enter = {current})" if current else ""
        value = ask(f"{label}{hint}: ")
        if not value and current:
            return current
        if value and not any(c.isspace() for c in value):
            return value
        say(f"{RED}Indicá un identificador no vacío y sin espacios.{RESET}")


def pick_agent(current: str) -> str:
    labels = []
    for a in SUPPORTED_AGENTS:
        state = "" if shutil.which(a) else f" {DIM}— no instalado{RESET}"
        labels.append(f"{a}{state}")
    default = SUPPORTED_AGENTS.index(current) if current in SUPPORTED_AGENTS else None
    return SUPPORTED_AGENTS[choose("Agente de código:", labels, default)]


def pick_model(agent: str, current: Backend | None, static: Path | None, cache: dict) -> tuple[str, str]:
    """(provider, model) for `agent`, from its live catalog."""
    if agent not in cache:
        verify = False
        if agent == "hermes" and shutil.which("hermes"):
            verify = ask("¿Verificamos los providers de Hermes con una llamada real? (s/N): ").lower() in ("s", "si", "sí", "y", "yes")
        cache[agent] = load_catalog(agent, static, verify)
    rows, report = cache[agent]
    describe_report(agent, report)
    same_agent = current is not None and current.agent == agent
    if not rows:
        say(f"No hay catálogo para {agent}; ingresá el modelo a mano.")
        provider = ""
        if agent in SLASH_PROVIDER_AGENTS or agent == "hermes":
            provider = ask_identifier("Proveedor", current.provider if same_agent else "")
        return provider, ask_identifier("Modelo", current.model if same_agent else "")

    providers: list[tuple[str, str]] = []
    for p, plabel, _, _ in rows:
        if p not in [x[0] for x in providers]:
            providers.append((p, plabel))
    if len(providers) == 1:
        provider = providers[0][0]
    else:
        default = None
        if same_agent:
            keys = [p for p, _ in providers]
            default = keys.index(current.provider) if current.provider in keys else None
        provider = providers[choose(f"Proveedor para {agent}:", [l for _, l in providers], default)][0]

    models = [(m, ml) for p, _, m, ml in rows if p == provider]
    labels = [ml if ml == m else f"{ml}  {DIM}[{m}]{RESET}" for m, ml in models] + ["Otro (ingresar el id a mano)"]
    default = None
    if same_agent and current.provider in ("", provider):
        ids = [m for m, _ in models]
        default = ids.index(current.model) if current.model in ids else None
    idx = choose(f"Modelo ({provider}):", labels, default)
    if idx == len(models):
        return provider, ask_identifier("Id del modelo")
    return provider, models[idx][0]


def pick_thinking(current: str, agent: str) -> str:
    if agent not in THINKING_AGENTS:
        say(f"{DIM}{agent} no recibe un nivel de thinking desde swarmforge.conf; se mantiene el valor.{RESET}")
        return current or "medium"
    default = THINKING_LEVELS.index(current) if current in THINKING_LEVELS else THINKING_LEVELS.index("medium")
    return THINKING_LEVELS[choose("Nivel de reasoning/thinking:", THINKING_LEVELS, default)]


# --------------------------------------------------------------------------
# Applying changes
# --------------------------------------------------------------------------

# Whether the user chose auto-approve at install time. A pi row cannot tell
# (pi has no approval flag), so it is taken from the other rows; with only pi
# rows the wizard's default (yes) applies. Set by main().
AUTO_APPROVE_HINT = True


def auto_approve_hint(rows: list[Row]) -> bool:
    flagged = [r.backend.auto_approve for r in rows if r.backend.agent in AUTO_APPROVE_FLAGS]
    return any(flagged) if flagged else True


def change_agent(row: Row, agent: str, provider: str, model: str, thinking: str | None) -> None:
    old = row.backend
    keep_others = old.others if agent == old.agent else []
    row.backend = Backend(
        agent=agent, provider=provider, model=model,
        thinking=thinking or old.thinking or "medium",
        auto_approve=old.auto_approve if old.agent in AUTO_APPROVE_FLAGS else AUTO_APPROVE_HINT,
        others=list(keep_others),
    )


def print_table(rows: list[Row], title: str = "Configuración actual") -> None:
    say(f"\n{BOLD}{title}{RESET}")
    say(f"  {'#':<3}{'rol':<12}{'agente':<10}{'modelo':<44}thinking")
    for i, r in enumerate(rows, 1):
        b = r.backend
        thinking = b.thinking if b.agent in THINKING_AGENTS else "-"
        say(f"  {i:<3}{r.role:<12}{b.agent:<10}{b.model_display():<44}{thinking}")


def swarm_running(project: Path) -> bool:
    pid_file = project / ".swarmforge" / "daemon" / "handoffd.pid"
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
            return f'"{pid}"' in out
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def write_conf(conf: Path, lines: list[str], rows: list[Row]) -> Path:
    for r in rows:
        lines[r.line_index] = render_row(r)
    backup = conf.with_name(f"{conf.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(conf, backup)
    fd, tmp = tempfile.mkstemp(dir=str(conf.parent), prefix=".swarmforge.conf.")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, conf)
    return backup


def select_rows(rows: list[Row], args) -> list[Row]:
    if args.all:
        return list(rows)
    selected = []
    if args.roles:
        by_name = {r.role: r for r in rows}
        for name in args.roles:
            if name not in by_name:
                raise ChangeError(f"el rol '{name}' no existe en swarmforge.conf (roles: {', '.join(by_name)})")
            selected.append(by_name[name])
    if args.from_agent:
        selected += [r for r in rows if r.backend.agent == args.from_agent and r not in selected]
        if not selected:
            raise ChangeError(f"ningún rol usa el agente '{args.from_agent}'")
    return selected


def interactive_pick_rows(rows: list[Row]) -> list[Row]:
    while True:
        answer = ask(f"\n¿Qué rol(es) cambiar? [1-{len(rows)}, varios con coma, 'a' = todos, 'q' = salir]: ").lower()
        if answer in ("q", "quit", "salir"):
            raise ChangeError("sin cambios")
        if answer in ("a", "all", "todos", "t"):
            return list(rows)
        picked = []
        ok = True
        for part in answer.replace(" ", "").split(","):
            if part.isdigit() and 1 <= int(part) <= len(rows):
                if rows[int(part) - 1] not in picked:
                    picked.append(rows[int(part) - 1])
            else:
                names = [r for r in rows if r.role == part]
                if names:
                    picked += [n for n in names if n not in picked]
                else:
                    ok = False
        if ok and picked:
            return picked
        say(f"{RED}Selección no válida.{RESET}")


def interactive_edit(row: Row, static: Path | None, cache: dict) -> None:
    b = row.backend
    say(f"\n{BOLD}── {row.role}{RESET}: {b.agent} · {b.model_display()}"
        + (f" · thinking {b.thinking}" if b.agent in THINKING_AGENTS else ""))
    what = choose("¿Qué querés cambiar?", [
        "Solo el modelo (mismo agente)",
        "El agente (y su modelo)",
        "Solo el nivel de thinking",
        "Nada, dejar este rol como está",
    ], default=0)
    if what == 3:
        return
    if what == 2:
        b.thinking = pick_thinking(b.thinking, b.agent)
        return
    agent = b.agent if what == 0 else pick_agent(b.agent)
    provider, model = pick_model(agent, b, static, cache)
    thinking = pick_thinking(b.thinking, agent)
    change_agent(row, agent, provider, model, thinking)


def apply_flags(row: Row, args, static: Path | None, cache: dict, can_prompt: bool) -> None:
    b = row.backend
    agent = args.agent or b.agent
    if agent not in SUPPORTED_AGENTS:
        raise ChangeError(f"agente no soportado: {agent} (soportados: {', '.join(SUPPORTED_AGENTS)})")
    provider, model = b.provider, b.model
    if args.model:
        model = args.model
        provider = args.provider or ""
        if agent in SLASH_PROVIDER_AGENTS and "/" in model and not args.provider:
            provider, model = model.split("/", 1)
        elif agent == "hermes" and not provider:
            provider = b.provider if b.agent == "hermes" else ""
            if not provider:
                raise ChangeError("hermes necesita --provider (p. ej. --provider opencode-go)")
    elif args.provider:
        provider = args.provider
    elif agent != b.agent:
        if not can_prompt:
            raise ChangeError(f"al cambiar {row.role} a {agent} hay que indicar --model")
        say(f"\n{BOLD}{row.role}{RESET}: elegí el modelo para {agent}")
        provider, model = pick_model(agent, None, static, cache)
    if agent in SLASH_PROVIDER_AGENTS and not provider:
        raise ChangeError(f"{agent} necesita el modelo como proveedor/modelo (p. ej. opencode-go/kimi-k3)")
    thinking = args.thinking or b.thinking
    if args.thinking and args.thinking not in THINKING_LEVELS:
        raise ChangeError(f"thinking inválido: {args.thinking} (usa {', '.join(THINKING_LEVELS)})")
    if agent == b.agent:
        b.provider, b.model, b.thinking = provider, model, thinking or b.thinking
    else:
        change_agent(row, agent, provider, model, thinking)
    if args.auto_approve is not None:
        row.backend.auto_approve = args.auto_approve


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swarm-kura change", description=__doc__.split("\n\n")[0])
    ap.add_argument("roles", nargs="*", help="roles a cambiar (por defecto se preguntan)")
    ap.add_argument("-C", "--project", default=os.getcwd(), help="raíz del proyecto (default: directorio actual)")
    ap.add_argument("--agent", help="nuevo agente para los roles elegidos")
    ap.add_argument("--model", help="nuevo modelo (opencode/pi: proveedor/modelo)")
    ap.add_argument("--provider", help="proveedor del modelo (hermes, o para separar proveedor/modelo)")
    ap.add_argument("--thinking", help="nivel de thinking: " + ", ".join(THINKING_LEVELS))
    ap.add_argument("--from-agent", help="aplicar a todos los roles que hoy usan este agente")
    ap.add_argument("--all", action="store_true", help="aplicar a todos los roles")
    ap.add_argument("--auto-approve", dest="auto_approve", action="store_true", default=None)
    ap.add_argument("--no-auto-approve", dest="auto_approve", action="store_false")
    ap.add_argument("--catalog", help="catálogo estático provider-models.tsv")
    ap.add_argument("--list", action="store_true", help="solo mostrar la configuración actual")
    ap.add_argument("-y", "--yes", action="store_true", help="no pedir confirmación")
    args = ap.parse_args(argv)

    project = Path(args.project).resolve()
    conf = project / "swarmforge" / "swarmforge.conf"
    if not conf.is_file():
        say(f"{RED}Error:{RESET} {project} no tiene una instalación de SwarmForge (falta swarmforge/swarmforge.conf).")
        say("Instalá una con: swarm-kura init")
        return 1
    lines, rows = parse_conf(conf.read_text(encoding="utf-8"))
    if not rows:
        say(f"{RED}Error:{RESET} swarmforge.conf no tiene filas `window`.")
        return 1

    global AUTO_APPROVE_HINT
    AUTO_APPROVE_HINT = auto_approve_hint(rows)
    print_table(rows)
    if args.list:
        return 0

    before = {r.role: render_row(r) for r in rows}
    static = find_static_catalog(args.catalog, project)
    cache: dict = {}
    can_prompt = sys.stdin.isatty() or os.environ.get("SWARMFORGE_ASSUME_TTY") == "1"
    scripted = any(v is not None for v in (args.agent, args.model, args.provider, args.thinking, args.auto_approve))
    try:
        if scripted:
            targets = select_rows(rows, args)
            if not targets:
                if not can_prompt:
                    raise ChangeError("indicá los roles (o --all / --from-agent)")
                targets = interactive_pick_rows(rows)
            for r in targets:
                apply_flags(r, args, static, cache, can_prompt)
        else:
            if not can_prompt:
                raise ChangeError("sin terminal: usá flags (--agent/--model/--thinking) para cambiar roles")
            targets = select_rows(rows, args) if (args.roles or args.from_agent or args.all) else interactive_pick_rows(rows)
            for r in targets:
                if args.from_agent or args.all:
                    say(f"\n{BOLD}{r.role}{RESET}")
                interactive_edit(r, static, cache)
    except ChangeError as exc:
        say(f"\n{YELLOW}{exc}. No se modificó nada.{RESET}")
        return 1 if str(exc) != "sin cambios" else 0

    changed = [r for r in rows if render_row(r) != before[r.role]]
    if not changed:
        say("\nSin cambios.")
        return 0
    say(f"\n{BOLD}Cambios:{RESET}")
    for r in changed:
        say(f"  {RED}- {before[r.role]}{RESET}")
        say(f"  {GREEN}+ {render_row(r)}{RESET}")
    missing = sorted({r.backend.agent for r in changed if not shutil.which(r.backend.agent)})
    if missing:
        say(f"\n{YELLOW}Aviso: no se encontró en el PATH: {', '.join(missing)}. Instalalo antes de arrancar el swarm.{RESET}")
    if not args.yes and can_prompt:
        try:
            answer = ask("\n¿Guardar? (S/n): ").lower()
        except ChangeError:
            answer = "n"
        if answer not in ("", "s", "si", "sí", "y", "yes"):
            say("Cancelado. No se modificó nada.")
            return 1
    backup = write_conf(conf, lines, rows)
    say(f"\n{GREEN}Guardado swarmforge/swarmforge.conf{RESET} (anterior en {backup.name}).")
    say("Los roles cambiados arrancan una sesión nueva del agente en su próximo lanzamiento.")
    if swarm_running(project):
        say(f"{YELLOW}El swarm está corriendo: reinicialo para aplicar los cambios "
            f"(swarm-kura stop && swarm-kura run, o ./swarm stop && ./swarm).{RESET}")
    print_table(rows, "Configuración nueva")
    return 0


if __name__ == "__main__":
    sys.exit(main())
