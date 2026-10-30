"""`swarm plan ...`: human-facing commands over the feature plan.

Output of `validate` is read by the specifier agent too, so it stays in
English; the other subcommands print Spanish text for the human.
"""
import os
import sys

from . import plan as planlib
from . import plan_flow
from .handoff.timefmt import now_iso

USAGE = """\
Uso: swarm plan <comando>

  validate                      valida el plan (errores y advertencias)
  show                          muestra el plan y el contexto de cada feature
  status                        muestra el estado de cada feature
  approve                       aprueba el plan (solo lo puede hacer una persona)
  publish <id>                  (re)publica una feature terminada: sube el commit y abre el PR
  sync                          detecta PRs mergeados, sincroniza y despacha la siguiente feature
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


def _decisions_line(plan, plan_dir):
    settled = planlib.settled_decisions(plan_dir)
    needed = planlib.min_decisions(plan)
    if settled is None:
        return f"Decisiones de la entrevista ({planlib.DECISIONS_FILE}): no existe (minimo {needed})"
    return f"Decisiones de la entrevista ({planlib.DECISIONS_FILE}): {len(settled)} cerradas (minimo {needed})"


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
    _out(_decisions_line(plan, plan_dir))
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
    if plan.get("base_branch"):
        _out(f"Rama base: {plan['base_branch']}")
    _out(_decisions_line(plan, plan_dir))
    for f in plan.get("features", []):
        _out(f"  {f.get('status', '?'):<15} {f.get('id', '?')}")
        if f.get("final_commit"):
            _out(f"      commit final: {f['final_commit'][:10]}")
        if f.get("pr_url"):
            _out(f"      PR: {f['pr_url']}")
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
    # Statuses move automatically with the handoffs and merge detection; this
    # manual edge stays for repairs (awaiting_merge -> merged when detection
    # cannot see the merge), plus --force. Checked against TRANSITIONS.
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


def _human_only(env, what):
    if env.get("SWARMFORGE_ROLE"):
        _err(
            f"Solo una persona puede {what}: este comando no se puede ejecutar "
            "desde dentro de un agente."
        )
        return False
    return True


def _flow(plan_dir, flow=None):
    if flow is not None:
        return flow
    return plan_flow.PlanFlow(planlib._project_root(plan_dir))


def cmd_publish(plan_dir, feature_id, env, flow=None):
    if not _human_only(env, "publicar una feature"):
        return 3
    flow = _flow(plan_dir, flow)
    result = flow.publish(feature_id)
    if not result.ok:
        _err(f"No se pudo publicar '{feature_id}': {result.message}")
        return 1
    _out(f"Feature {feature_id} publicada: {result.pr_url}")
    return 0


def cmd_sync(plan_dir, env, flow=None):
    if not _human_only(env, "sincronizar el plan"):
        return 3
    if not planlib.exists(plan_dir):
        _err(f"No hay plan en {planlib.plan_path(plan_dir)}.")
        return 1
    flow = _flow(plan_dir, flow)
    roles = plan_flow.roles_from_tsv(flow.root / ".swarmforge" / "roles.tsv")
    actions = flow.poll_merges(roles, force=True)
    if not actions:
        _out("Nada que sincronizar (ningun PR mergeado nuevo o la sincronizacion quedo pendiente; ver .swarmforge/logs/plan-flow.log).")
        return 0
    for action in actions:
        _out(f"- {action}")
    return 0


def main(argv, plan_dir=None, env=None, flow=None):
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
    if cmd == "publish" and len(rest) == 1:
        return cmd_publish(plan_dir, rest[0], env, flow)
    if cmd == "sync" and not rest:
        return cmd_sync(plan_dir, env, flow)
    if cmd == "set-status":
        force = "--force" in rest
        args = [a for a in rest if a != "--force"]
        if len(args) == 2:
            return cmd_set_status(plan_dir, args[0], args[1], env, force=force)
    _err(USAGE)
    return 1
