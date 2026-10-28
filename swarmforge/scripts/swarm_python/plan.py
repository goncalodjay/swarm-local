"""Feature plan: the specifier splits a module into sequential features.

Layout, under the MASTER checkout's `.swarmforge/plan/` (the single source of
truth; role worktrees never keep a copy, they resolve this directory through
the shared git common dir):

    plan.json            machine state: statuses, dependencies, acceptance
    general.md           overview every feature shares (about 400 lines max)
    features/<id>.md     one slice per feature (about 400 lines max)

The ~400 line figure is a guide for sizing, never a hard limit: every size
check in this module only produces WARNINGS.

This module is pure (no git writes, no agents); callers pass directories.
"""
import contextlib
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

PLAN_STATUSES = ("draft", "approved")
FEATURE_STATUSES = ("pending", "coding", "review", "arch", "awaiting_merge", "merged")
# A feature in one of these statuses is between "handed to the coder" and
# "merged"; at most one feature may be in this state at any time.
IN_PROGRESS = ("coding", "review", "arch", "awaiting_merge")

LINE_GUIDE = 400
# Warn only when clearly beyond the guide, so ~400 does not become a limit.
EST_LINES_WARN = 600
MD_LINES_WARN = LINE_GUIDE

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
REQUIRED_FEATURE_FIELDS = (
    "id", "title", "summary", "depends_on", "allowed_paths",
    "acceptance", "est_lines", "status",
)
REQUIRED_PLAN_FIELDS = ("id", "title", "status", "general_file", "features")


class PlanError(Exception):
    pass


# --- location -------------------------------------------------------------

def locate_plan_dir(cwd=None):
    """Plan directory of the project owning `cwd`.

    SWARMFORGE_PLAN_DIR wins (tests, unusual layouts). Otherwise use the git
    common dir's parent, which is the master checkout for the master itself
    and for every worktree under .worktrees/, so all roles read one copy.
    """
    override = os.environ.get("SWARMFORGE_PLAN_DIR")
    if override:
        return Path(override)
    cwd = Path(cwd) if cwd else Path.cwd()
    root = cwd
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, cwd=str(cwd),
        )
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (cwd / common).resolve()
            if (common.parent / ".swarmforge").exists():
                root = common.parent
    except OSError:
        pass
    found = root / ".swarmforge" / "plan"
    if not found.exists():
        master = _master_checkout(cwd)
        if master is not None and (master / ".swarmforge" / "plan").exists():
            return master / ".swarmforge" / "plan"
    return found


def _master_checkout(cwd):
    """First entry of `git worktree list --porcelain` (the main checkout)."""
    try:
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, cwd=str(cwd),
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):].strip())
    return None


def plan_path(plan_dir):
    return Path(plan_dir) / "plan.json"


def feature_file(plan_dir, feature_id):
    return Path(plan_dir) / "features" / f"{feature_id}.md"


# --- load / save ----------------------------------------------------------

def exists(plan_dir):
    return plan_path(plan_dir).is_file()


def load(plan_dir):
    path = plan_path(plan_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlanError(f"Plan not found: {path}")
    except (OSError, ValueError) as e:
        raise PlanError(f"Plan is unreadable ({path}): {e}")
    if not isinstance(data, dict):
        raise PlanError(f"Plan must be a JSON object: {path}")
    return data


SAVE_RETRIES = 6
SAVE_RETRY_DELAY = 0.15


def save(plan_dir, plan):
    """Atomic write: a reader never sees a half-written plan.json.

    The tmp name is unique per write (concurrent writers never clobber each
    other's tmp file). On Windows os.replace fails with PermissionError while
    another process (TUI, status poller) has plan.json open, so it is retried
    a few times; the final failure is raised as PlanError.
    """
    path = plan_path(plan_dir)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        last = None
        for attempt in range(SAVE_RETRIES):
            try:
                os.replace(tmp, path)
                return
            except OSError as e:
                last = e
                if attempt < SAVE_RETRIES - 1:
                    time.sleep(SAVE_RETRY_DELAY)
        raise PlanError(f"Could not write {path}: {last}")
    except OSError as e:
        raise PlanError(f"Could not write {path}: {e}")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# --- locking ----------------------------------------------------------------

LOCK_STALE_SECONDS = 30
LOCK_WAIT_SECONDS = 15
LOCK_POLL = 0.05


@contextlib.contextmanager
def plan_lock(plan_dir):
    """Exclusive cross-process lock (`plan.lock`, created with O_EXCL).

    A lock file older than LOCK_STALE_SECONDS belongs to a crashed process and
    is taken over. Not reentrant: callers inside the lock must use the
    `_unlocked` helpers.
    """
    lock = Path(plan_dir) / "plan.lock"
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise PlanError(f"Cannot create plan directory {lock.parent}: {e}")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.unlink()
                    continue
            except OSError:
                continue  # vanished or briefly locked: just retry
            if time.monotonic() >= deadline:
                raise PlanError(f"Plan is locked by another process ({lock}); try again shortly.")
            time.sleep(LOCK_POLL)
        except OSError as e:
            raise PlanError(f"Cannot create plan lock {lock}: {e}")
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


# --- validation -----------------------------------------------------------

def _line_count(path):
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def _valid_deps(f):
    """depends_on as a list of strings, or None when malformed."""
    deps = f.get("depends_on")
    if isinstance(deps, list) and all(isinstance(d, str) for d in deps):
        return deps
    return None


def _find_cycle(features):
    graph = {}
    for f in features:
        if not isinstance(f, dict) or not isinstance(f.get("id"), str):
            continue
        deps = _valid_deps(f)
        if deps is None:
            continue  # reported as a validation error; skip cycle detection
        graph[f["id"]] = deps
    state = {}

    def visit(node, stack):
        state[node] = 1
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if dep not in state:
                found = visit(dep, stack + [dep])
                if found:
                    return found
        state[node] = 2
        return None

    for node in graph:
        if node not in state:
            found = visit(node, [node])
            if found:
                return found
    return None


def validate(plan, plan_dir):
    """Return (errors, warnings). Only errors block approval."""
    errors, warnings = [], []
    for field in REQUIRED_PLAN_FIELDS:
        if field not in plan:
            errors.append(f"Plan is missing field '{field}'.")
    if plan.get("status") not in PLAN_STATUSES and "status" in plan:
        errors.append(f"Plan status must be one of {', '.join(PLAN_STATUSES)}; got '{plan.get('status')}'.")
    features = plan.get("features")
    if not isinstance(features, list):
        errors.append("Plan field 'features' must be a list.")
        features = []
    elif not features and "features" in plan:
        errors.append("Plan has no features.")

    seen = set()
    for index, f in enumerate(features):
        if not isinstance(f, dict):
            errors.append(f"Feature #{index + 1} must be an object.")
            continue
        fid = f.get("id")
        label = fid if isinstance(fid, str) and fid else f"#{index + 1}"
        for field in REQUIRED_FEATURE_FIELDS:
            if field not in f:
                errors.append(f"Feature {label} is missing field '{field}'.")
        if "id" in f:
            if not isinstance(fid, str) or not ID_RE.fullmatch(fid):
                errors.append(
                    f"Feature id '{fid}' must be a slug of lowercase letters, digits and "
                    f"hyphens (max 80 characters)."
                )
            elif fid in seen:
                errors.append(f"Duplicate feature id '{fid}'.")
            else:
                seen.add(fid)
        if "status" in f and f["status"] not in FEATURE_STATUSES:
            errors.append(
                f"Feature {label} has invalid status '{f['status']}'; "
                f"allowed: {', '.join(FEATURE_STATUSES)}."
            )
        if "acceptance" in f:
            acc = f["acceptance"]
            if (not isinstance(acc, list) or not acc
                    or not all(isinstance(a, str) and a.strip() for a in acc)):
                errors.append(f"Feature {label} needs a non-empty list of acceptance criteria.")
        for list_field in ("depends_on", "allowed_paths"):
            if list_field in f and not (
                isinstance(f[list_field], list)
                and all(isinstance(x, str) for x in f[list_field])
            ):
                errors.append(f"Feature {label} field '{list_field}' must be a list of strings.")
        if "est_lines" in f and (
            isinstance(f["est_lines"], bool) or not isinstance(f["est_lines"], int)
            or f["est_lines"] < 0
        ):
            errors.append(f"Feature {label} field 'est_lines' must be a non-negative integer.")
        elif isinstance(f.get("est_lines"), int) and f["est_lines"] > EST_LINES_WARN:
            warnings.append(
                f"Feature {label} estimates {f['est_lines']} lines (guide is ~{LINE_GUIDE}); "
                f"consider splitting it. This is advisory only."
            )
        if isinstance(fid, str) and ID_RE.fullmatch(fid):
            md = feature_file(plan_dir, fid)
            if not md.is_file():
                errors.append(f"Feature {label} is missing its file {md}.")
            elif _line_count(md) > MD_LINES_WARN:
                warnings.append(
                    f"Feature file {md.name} has {_line_count(md)} lines (guide is ~{MD_LINES_WARN})."
                )

    for f in features:
        if not isinstance(f, dict) or _valid_deps(f) is None:
            continue
        for dep in f["depends_on"]:
            if dep not in seen:
                errors.append(f"Feature {f.get('id')} depends on unknown feature '{dep}'.")
            elif dep == f.get("id"):
                errors.append(f"Feature {dep} depends on itself.")
    cycle = _find_cycle([f for f in features if isinstance(f, dict)])
    if cycle:
        errors.append("Dependency cycle: " + " -> ".join(cycle) + ".")

    general = plan.get("general_file")
    if isinstance(general, str) and general:
        gpath = Path(plan_dir) / general
        if not gpath.is_file():
            errors.append(f"General overview file is missing: {gpath}.")
        elif _line_count(gpath) > MD_LINES_WARN:
            warnings.append(
                f"{general} has {_line_count(gpath)} lines (guide is ~{MD_LINES_WARN})."
            )
    return errors, warnings


# --- queries --------------------------------------------------------------

def _features(plan):
    """Feature objects of a possibly malformed plan (non-dicts dropped)."""
    features = plan.get("features") if isinstance(plan, dict) else None
    if not isinstance(features, list):
        return []
    return [f for f in features if isinstance(f, dict)]


def feature_by_id(plan, feature_id):
    for f in _features(plan):
        if f.get("id") == feature_id:
            return f
    return None


def in_progress_features(plan):
    return [f for f in _features(plan) if f.get("status") in IN_PROGRESS]


def unmerged_dependencies(plan, feature):
    out = []
    for dep in _valid_deps(feature) or []:
        d = feature_by_id(plan, dep)
        if d is None or d.get("status") != "merged":
            out.append(dep)
    return out


def next_ready_feature(plan):
    """First pending feature whose dependencies are all merged, provided no
    other feature is in progress (features are strictly sequential)."""
    if in_progress_features(plan):
        return None
    for f in _features(plan):
        if f.get("status") == "pending" and not unmerged_dependencies(plan, f):
            return f
    return None


def slice_for(plan, feature_id, plan_dir):
    """Context for the coder: the general overview plus ONLY this feature."""
    feature = feature_by_id(plan, feature_id)
    if feature is None:
        raise PlanError(f"Unknown feature '{feature_id}'.")
    features = _features(plan)
    position = features.index(feature) + 1

    def read(path):
        try:
            return Path(path).read_text(encoding="utf-8").rstrip()
        except OSError:
            return f"(missing file: {path})"

    lines = [
        f"=== PLAN: {plan.get('title', plan.get('id', ''))} ===",
        "",
        "--- General overview ---",
        read(Path(plan_dir) / plan.get("general_file", "general.md")),
        "",
        f"--- Your feature ({position} of {len(features)}): {feature['id']} - {feature.get('title', '')} ---",
        f"Summary: {feature.get('summary', '')}",
        "",
        read(feature_file(plan_dir, feature["id"])),
        "",
        "Acceptance criteria:",
    ]
    lines += [f"- {a}" for a in feature.get("acceptance", [])]
    lines += ["", "Allowed paths:"]
    lines += [f"- {p}" for p in feature.get("allowed_paths", [])] or ["- (unrestricted)"]
    lines += [
        "",
        f"Size guide: about {LINE_GUIDE} lines of production code is approximate. "
        "Implement the whole feature; never truncate it or ask for a re-split.",
        "=== END PLAN ===",
    ]
    return "\n".join(lines) + "\n"


def coder_context(task, plan_dir=None, role=None):
    """Plan slice to show a coder picking up `task`, or "" when it does not
    apply (other roles, task is not a feature id of an existing plan). When
    the task looks like a feature id but the plan cannot be found/read, a
    one-line note says so instead of silently printing nothing. Never raises:
    a broken plan must not stop the coder from reading its handoff."""
    role = os.environ.get("SWARMFORGE_ROLE") if role is None else role
    if role != "coder" or not task:
        return ""
    looks_like_feature = bool(ID_RE.fullmatch(task))
    shown = plan_dir
    try:
        plan_dir = plan_dir or locate_plan_dir()
        shown = plan_dir
        if not exists(plan_dir):
            if looks_like_feature:
                return f"(plan not found at {plan_path(plan_dir)})\n"
            return ""
        plan = load(plan_dir)
        if feature_by_id(plan, task) is None:
            return ""
        return slice_for(plan, task, plan_dir)
    except PlanError as e:
        return f"(plan could not be read at {shown}: {e})\n"
    except Exception:  # noqa: BLE001 - never break the handoff display
        return ""


# --- approval integrity -----------------------------------------------------
#
# HARDENING, NOT A SECURITY BOUNDARY: an agent running as the same OS user can
# still edit ~/.swarmforge/approvals. The goal is only to stop an approved plan
# from silently changing underneath the human (an agent editing plan.json or a
# feature file after `swarm plan approve`). Status fields are excluded so the
# CLI / handoff transitions never invalidate the approval.

def approvals_dir():
    override = os.environ.get("SWARMFORGE_APPROVALS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".swarmforge" / "approvals"


def _project_root(plan_dir):
    return Path(plan_dir).resolve().parent.parent


def approval_file(plan_dir):
    key = hashlib.sha256(os.path.normcase(str(_project_root(plan_dir))).encode("utf-8")).hexdigest()
    return approvals_dir() / f"{key}.json"


def content_hash(plan_dir):
    """sha256 of the plan content minus statuses/approved_at, plus the bytes of
    general.md and features/*.md."""
    plan = load(plan_dir)
    canon = {k: v for k, v in plan.items() if k not in ("status", "approved_at")}
    raw_features = plan.get("features") if isinstance(plan.get("features"), list) else []
    canon["features"] = [
        {k: v for k, v in f.items() if k != "status"} if isinstance(f, dict) else f
        for f in raw_features
    ]
    h = hashlib.sha256()
    h.update(json.dumps(canon, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    general = plan.get("general_file")
    general = general if isinstance(general, str) and general else "general.md"
    files = [("general", Path(plan_dir) / general)]
    feats = Path(plan_dir) / "features"
    if feats.is_dir():
        files += [("feature/" + p.name, p) for p in sorted(feats.glob("*.md"), key=lambda p: p.name)]
    for name, path in files:
        try:
            data = path.read_bytes()
        except OSError:
            data = b"\0missing"
        h.update(b"\n" + name.encode("utf-8") + b"\n" + hashlib.sha256(data).hexdigest().encode("ascii"))
    return h.hexdigest()


def record_approval(plan_dir, now_iso=""):
    path = approval_file(plan_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps({
            "sha256": content_hash(plan_dir), "approved_at": now_iso,
            "plan_dir": str(Path(plan_dir).resolve()),
        }, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        raise PlanError(f"Could not record the approval at {path}: {e}")


def approval_errors(plan_dir):
    path = approval_file(plan_dir)
    try:
        recorded = json.loads(path.read_text(encoding="utf-8")).get("sha256")
    except (OSError, ValueError, AttributeError):
        return [
            "No recorded approval found for this plan; ask the user to run "
            "`swarm plan approve` again."
        ]
    try:
        current = content_hash(plan_dir)
    except PlanError as e:
        return [str(e)]
    if recorded != current:
        return [
            "plan content changed since approval; ask the user to run "
            "`swarm plan approve` again"
        ]
    return []


# --- mutations ------------------------------------------------------------

def _set_status_unlocked(plan_dir, feature_id, status):
    if status not in FEATURE_STATUSES:
        raise PlanError(f"Invalid status '{status}'; allowed: {', '.join(FEATURE_STATUSES)}.")
    plan = load(plan_dir)
    feature = feature_by_id(plan, feature_id)
    if feature is None:
        raise PlanError(f"Unknown feature '{feature_id}'.")
    feature["status"] = status
    save(plan_dir, plan)
    return plan


def set_feature_status(plan_dir, feature_id, status):
    with plan_lock(plan_dir):
        return _set_status_unlocked(plan_dir, feature_id, status)


def mark_coding(plan_dir, feature_id):
    """The single place where a feature enters `coding`."""
    return set_feature_status(plan_dir, feature_id, "coding")


def reserve_feature(plan_dir, task):
    """Under one lock: re-run the specifier -> coder gate, then mark the
    feature `coding`. Returns the gate's error list (empty = reserved)."""
    with plan_lock(plan_dir):
        errors = check_specifier_to_coder(plan_dir, task)
        if errors:
            return errors
        _set_status_unlocked(plan_dir, task, "coding")
    return []


def approve(plan_dir, now_iso):
    with plan_lock(plan_dir):
        plan = load(plan_dir)
        plan["status"] = "approved"
        plan["approved_at"] = now_iso
        save(plan_dir, plan)
        record_approval(plan_dir, now_iso)
    return plan


# --- handoff gate ---------------------------------------------------------

def check_specifier_to_coder(plan_dir, task):
    """Agent-facing errors for a specifier -> coder git_handoff (empty = ok)."""
    if not exists(plan_dir):
        return [
            f"No feature plan found at {plan_path(plan_dir)}. Create the plan "
            "(plan.json, general.md, features/<id>.md), run `swarm plan validate`, "
            "and ask the user to run `swarm plan approve` before handing work to the coder."
        ]
    try:
        plan = load(plan_dir)
    except PlanError as e:
        return [str(e)]
    if plan.get("status") != "approved":
        return [
            "The feature plan is not approved. Ask the user to run `swarm plan approve`; "
            "you cannot approve it yourself."
        ]
    # An approved plan may have been edited into an invalid state since.
    invalid, _warnings = validate(plan, plan_dir)
    if invalid:
        return ["The approved plan is now invalid:"] + [f"  - {e}" for e in invalid]
    changed = approval_errors(plan_dir)
    if changed:
        return changed
    if not task:
        return []  # the missing 'task' header is reported by the base checks
    feature = feature_by_id(plan, task)
    if feature is None:
        ids = ", ".join(str(f.get("id", "?")) for f in _features(plan))
        return [f"Header 'task' must equal a feature id from the plan; '{task}' is not one (features: {ids})."]
    errors = []
    if feature.get("status") != "pending":
        errors.append(f"Feature '{task}' is '{feature.get('status')}', not 'pending'; it cannot be handed off again.")
    unmerged = unmerged_dependencies(plan, feature)
    if unmerged:
        errors.append(f"Feature '{task}' depends on features that are not merged yet: {', '.join(unmerged)}.")
    busy = [f.get("id") for f in in_progress_features(plan) if f.get("id") != task]
    if busy:
        errors.append(
            f"Feature '{busy[0]}' is still in progress; features are sequential, so wait "
            "until it is merged before handing off the next one."
        )
    return errors
