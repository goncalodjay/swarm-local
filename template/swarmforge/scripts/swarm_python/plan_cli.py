"""`swarm plan ...`: human-facing commands over the feature plan.

Output of `validate` is read by the specifier agent too, so it stays in
English; the other subcommands print Spanish text for the human.
"""
import os
import sys

from . import plan as planlib
from .handoff.timefmt import now_iso

USAGE = """\
Uso: swarm plan <comando>

  validate                      valida el plan (errores y advertencias)
  show                          muestra el plan y el contexto de cada feature
  status                        muestra el estado de cada feature
  approve                       aprueba el plan (solo lo puede hacer una persona)
  set-status <id> <estado> [--force]
                                cambia el estado de una feature (manual);
                                --force permite saltos que el ciclo no permite
"""


def _out(msg=""):
    print(msg)


def _err(msg):
    print(msg, file=sys.stderr)


def _load(plan_dir):
    try:
        return planlib.load(plan_dir)
    except planlib.PlanError as e:
        _err(str(e))
        return None


def cmd_validate(plan_dir):
    plan = _load(plan_dir)
    if plan is None:
        return 1
    errors, warnings = planlib.validate(plan, plan_dir)
    for w in warnings:
        _out(f"WARNING: {w}")
    for e in errors:
        _err(f"ERROR: {e}")
    if errors:
        _err(f"PLAN INVALID: {len(errors)} error(s), {len(warnings)} warning(s).")
        return 2
    _out(f"PLAN OK: {len(plan['features'])} feature(s), {len(warnings)} warning(s).")
    return 0


def cmd_show(plan_dir):
    plan = _load(plan_dir)
    if plan is None:
        return 1
    _out(f"Plan: {plan.get('title', '')} ({plan.get('status', '?')})")
    for f in plan.get("features", []):
        _out("")
        _out(f"[{f.get('status', '?')}] {f.get('id', '?')} - {f.get('title', '')}")
        _out(f"  Resumen: {f.get('summary', '')}")
        _out(f"  Depende de: {', '.join(f.get('depends_on', [])) or '-'}")
        _out(f"  Lineas estimadas: {f.get('est_lines', '?')}")
        for a in f.get("acceptance", []):
            _out(f"  - {a}")
    return 0


def cmd_status(plan_dir):
    plan = _load(plan_dir)
    if plan is None:
        return 1
    _out(f"Plan: {plan.get('title', '')} - estado: {plan.get('status', '?')}")
    for f in plan.get("features", []):
        _out(f"  {f.get('status', '?'):<15} {f.get('id', '?')}")
    nxt = planlib.next_ready_feature(plan)
    _out(f"Proxima feature lista: {nxt['id'] if nxt else 'ninguna'}")
    return 0


def cmd_approve(plan_dir, env):
    if env.get("SWARMFORGE_ROLE"):
        _err(
            "Solo una persona puede aprobar el plan: este comando no se puede "
            "ejecutar desde dentro de un agente (SWARMFORGE_ROLE esta definida). "
            "Ejecuta `swarm plan approve` desde tu propia terminal."
        )
        return 3
    plan = _load(plan_dir)
    if plan is None:
        return 1
    errors, warnings = planlib.validate(plan, plan_dir)
    for w in warnings:
        _out(f"Advertencia: {w}")
    if errors:
        _err("No se puede aprobar: el plan tiene errores.")
        for e in errors:
            _err(f"  - {e}")
        return 2
    try:
        planlib.approve(plan_dir, now_iso())
    except planlib.PlanError as e:
        _err(f"No se pudo aprobar el plan: {e}")
        return 1
    _out("Plan aprobado. El especificador ya puede entregar la primera feature al coder.")
    return 0


def cmd_set_status(plan_dir, feature_id, status, env, force=False):
    if env.get("SWARMFORGE_ROLE"):
        _err(
            "Solo una persona puede cambiar el estado de una feature: este comando "
            "no se puede ejecutar desde dentro de un agente."
        )
        return 3
    # Statuses move automatically with the handoffs; the only manual edge left
    # is awaiting_merge -> merged (until merge detection is automated), plus
    # --force for repairs. Anything else is checked against TRANSITIONS.
    try:
        planlib.set_feature_status(plan_dir, feature_id, status, force=force)
    except planlib.TransitionError as e:
        _err(
            f"Cambio de estado no permitido para '{e.feature_id}': {e.current} -> {e.target} "
            f"({e.reason}). Si estas seguro, repite el comando con --force."
        )
        return 1
    except planlib.PlanError as e:
        _err(str(e))
        return 1
    _out(f"Feature {feature_id}: estado cambiado a {status}.")
    return 0


def main(argv, plan_dir=None, env=None):
    env = os.environ if env is None else env
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out(USAGE)
        return 0 if argv else 1
    plan_dir = plan_dir or planlib.locate_plan_dir()
    cmd, rest = argv[0], argv[1:]
    if cmd == "validate" and not rest:
        return cmd_validate(plan_dir)
    if cmd == "show" and not rest:
        return cmd_show(plan_dir)
    if cmd == "status" and not rest:
        return cmd_status(plan_dir)
    if cmd == "approve" and not rest:
        return cmd_approve(plan_dir, env)
    if cmd == "set-status":
        force = "--force" in rest
        args = [a for a in rest if a != "--force"]
        if len(args) == 2:
            return cmd_set_status(plan_dir, args[0], args[1], env, force=force)
    _err(USAGE)
    return 1
