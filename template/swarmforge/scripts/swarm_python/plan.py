"""Feature plan: the specifier splits a module into sequential features.

Layout, under the MASTER checkout's `.swarmforge/plan/` (the single source of
truth; role worktrees never keep a copy, they resolve this directory through
the shared git common dir):

    plan.json            machine state: statuses, dependencies, acceptance
    general.md           overview every feature shares (about 400 lines max)
    decisions.md         record of the questions settled in the interview
    features/<id>.md     one slice per feature (about 400 lines max)

The ~400 line figure is a guide for sizing CODE, never a hard limit and never
a reason to thin out a specification: every size check in this module only
produces WARNINGS. What validation does enforce is that the interview happened
(decisions.md) and that each feature file has its required sections filled in.

This module is pure (no git writes, no agents); callers pass directories.
"""
import contextlib
import copy
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

PLAN_STATUSES = ("draft", "approved", "done")
FEATURE_STATUSES = ("pending", "coding", "review", "arch", "awaiting_merge", "merged")
# A feature in one of these statuses is between "handed to the coder" and
# "merged"; at most one feature may be in this state at any time.
IN_PROGRESS = ("coding", "review", "arch", "awaiting_merge")

# Legal feature status moves. The swarm drives most of them from handoffs;
# the human CLI (`swarm plan set-status`) is checked against the same table.
# `pending` is reachable from `coding` only as a manual/rollback reset;
# `awaiting_merge -> coding` is the human sending a reviewed feature back.
TRANSITIONS = {
    "pending": frozenset({"coding"}),
    "coding": frozenset({"review", "pending"}),
    "review": frozenset({"coding", "arch"}),
    "arch": frozenset({"coding", "awaiting_merge"}),
    "awaiting_merge": frozenset({"merged", "coding"}),
    "merged": frozenset(),
}
# Fields the swarm writes at runtime; they never count as plan content.
RUNTIME_FEATURE_FIELDS = (
    "status", "review_rounds", "escalated", "metrics", "base_commit",
    # Publish / merge lifecycle (written by plan_flow, never by the specifier).
    "final_commit", "pr_url", "published_at", "merge_commit", "synced_at", "report",
    "pr_closed_at", "pub_branch", "merge_source", "defer_noted_at",
)
# Runtime fields that hold a plain string.
STRING_RUNTIME_FIELDS = (
    "final_commit", "pr_url", "published_at", "merge_commit", "synced_at", "report",
    "pr_closed_at", "pub_branch", "merge_source", "defer_noted_at",
)
# Plan-level delivery settings. They decide HOW finished work is delivered, not
# WHAT is built, so editing them never invalidates the approval (see content_hash).
DELIVERY_PLAN_FIELDS = ("base_branch", "auto_publish", "auto_next", "flow_version")
# Version of the delivery flow that has processed this plan (see migrate_flow).
FLOW_VERSION = 1
BASE_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}")
DEFAULT_MAX_REVIEW_ROUNDS = 3

LINE_GUIDE = 400
# Warn only when clearly beyond the guide, so ~400 does not become a limit.
EST_LINES_WARN = 600
MD_LINES_WARN = LINE_GUIDE

# The interview record. Each settled question is a `## D<n>: <question>`
# section holding a non-empty `Decision: <answer>` line (and optional `Why:`).
DECISIONS_FILE = "decisions.md"
DEFAULT_MIN_DECISIONS = 5
DECISION_HEADING_RE = re.compile(r"^##[ \t]+D(\d+)[ \t]*:[ \t]*(\S.*?)[ \t]*$")
DECISION_LINE_RE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\**Decision\**[ \t]*:[ \t]*\**[ \t]*(.*)$", re.IGNORECASE)
WHY_LINE_RE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\**Why\**[ \t]*:", re.IGNORECASE)
FENCE_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$")
# Answers that look filled in but settle nothing (compared after normalization).
PLACEHOLDER_ANSWERS = frozenset({
    "tbd", "tba", "todo", "na", "none", "pending", "unknown", "answer", "your answer",
    "fill in", "fill me in", "fill in here", "to be decided", "to be defined", "to be determined",
    "later", "wip",
})
MIN_ANSWER_ALNUM = 3
# How a stuck author gets out of a failed interview/section check.
RECOVERY = (
    "To fix an existing plan: write decisions.md with at least {needed} entries, each `## D<n>: <question>` "
    "plus a real `Decision: <answer>` line; make sure every features/<id>.md has non-empty "
    "'## Behavior', '## Edge cases', '## Acceptance examples', '## Out of scope' and '## Files' sections; "
    "then ask the user to run `swarm plan approve` again."
)
# Sections every features/<id>.md must contain, non-empty. Size is never
# checked here: short features must not mean thin specs.
REQUIRED_FEATURE_SECTIONS = ("Behavior", "Edge cases", "Acceptance examples", "Out of scope", "Files")

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
REQUIRED_FEATURE_FIELDS = (
    "id", "title", "summary", "depends_on", "allowed_paths",
    "acceptance", "est_lines", "status",
)
REQUIRED_PLAN_FIELDS = ("id", "title", "status", "general_file", "features")


class PlanError(Exception):
    pass


class TransitionError(PlanError):
    """An illegal feature status move (human CLI, without --force)."""

    def __init__(self, feature_id, current, target, reason):
        super().__init__(
            f"Illegal status change for feature '{feature_id}': {current} -> {target} ({reason})."
        )
        self.feature_id, self.current, self.target, self.reason = feature_id, current, target, reason


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


def valid_base_branch(name):
    """True for a branch name that is safe to use as a PR base: never a swarm
    delivery branch, never an option-looking or path-escaping name."""
    if not isinstance(name, str) or not name or name != name.strip():
        return False
    if not BASE_BRANCH_RE.fullmatch(name):
        return False
    if name == "swarm" or name.startswith("swarm/"):
        return False
    if ".." in name or "//" in name or name.endswith(("/", ".")) or "/." in name:
        return False
    return not any(part.endswith(".lock") for part in name.split("/"))


def git_origin_url(root):
    """URL of the `origin` remote of `root`, '' when there is none. Never raises."""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True,
            cwd=str(root), timeout=15, stdin=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


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
def plan_lock(plan_dir, name="plan.lock", stale=None):
    """Exclusive cross-process lock (`plan.lock`, created with O_EXCL).

    A lock file older than LOCK_STALE_SECONDS belongs to a crashed process and
    is taken over. Not reentrant: callers inside the lock must use the
    `_unlocked` helpers.
    """
    lock = Path(plan_dir) / name
    stale = LOCK_STALE_SECONDS if stale is None else stale
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
                if time.time() - lock.stat().st_mtime > stale:
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


def validate(plan, plan_dir, interview=True):
    """Return (errors, warnings). Only errors block approval.

    `interview=False` skips the checks added with the mandatory interview
    (decisions.md, feature sections): the handoff gate uses it so a plan
    approved before those checks existed is not stuck."""
    errors, warnings = [], []
    for field in REQUIRED_PLAN_FIELDS:
        if field not in plan:
            errors.append(f"Plan is missing field '{field}'.")
    if plan.get("status") not in PLAN_STATUSES and "status" in plan:
        errors.append(f"Plan status must be one of {', '.join(PLAN_STATUSES)}; got '{plan.get('status')}'.")
    for flag in ("auto_publish", "auto_next"):
        if flag in plan and not isinstance(plan[flag], bool):
            errors.append(f"Plan field '{flag}' must be true or false.")
    if "base_branch" in plan and not (
            isinstance(plan["base_branch"], str) and plan["base_branch"].strip()):
        errors.append("Plan field 'base_branch' must be a non-empty string.")
    elif isinstance(plan.get("base_branch"), str) and not valid_base_branch(plan["base_branch"].strip()):
        errors.append(
            "Plan field 'base_branch' is not a safe branch name (no 'swarm/' prefix, '..', "
            "whitespace, leading '-' or unusual characters).")
    if "max_review_rounds" in plan:
        cap = plan["max_review_rounds"]
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            errors.append("Plan field 'max_review_rounds' must be an integer >= 1.")
    if "min_decisions" in plan:
        floor = plan["min_decisions"]
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
            errors.append("Plan field 'min_decisions' must be a non-negative integer.")
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
        if "review_rounds" in f and (
            isinstance(f["review_rounds"], bool) or not isinstance(f["review_rounds"], int)
            or f["review_rounds"] < 0
        ):
            errors.append(f"Feature {label} field 'review_rounds' must be a non-negative integer.")
        if "base_commit" in f and not isinstance(f["base_commit"], str):
            errors.append(f"Feature {label} field 'base_commit' must be a string.")
        for text_field in STRING_RUNTIME_FIELDS:
            if text_field in f and not isinstance(f[text_field], str):
                errors.append(f"Feature {label} field '{text_field}' must be a string.")
        if "metrics" in f and not isinstance(f["metrics"], dict):
            errors.append(f"Feature {label} field 'metrics' must be an object.")
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
            else:
                if interview:
                    errors.extend(feature_section_errors(md, min_decisions(plan)))
                if _line_count(md) > MD_LINES_WARN:
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
    if interview:
        errors.extend(decision_errors(plan, plan_dir))
    return errors, warnings


# --- interview record and feature sections -------------------------------------

def min_decisions(plan):
    """Minimum settled decisions the plan needs (plan.json `min_decisions`)."""
    floor = plan.get("min_decisions") if isinstance(plan, dict) else None
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
        return DEFAULT_MIN_DECISIONS
    return floor


def normalize_answer(text):
    """Lowercased answer without wrapping/decoration: `(TBD!)` -> `tbd`."""
    text = re.sub(r"[^\w\s]", " ", text.lower().replace("n/a", "na"))
    return " ".join(text.split())


def is_placeholder(text, min_alnum=MIN_ANSWER_ALNUM):
    """True for an answer that settles nothing: empty, TBD-like, or too short."""
    norm = normalize_answer(text)
    if norm in PLACEHOLDER_ANSWERS:
        return True
    return sum(c.isalnum() for c in norm) < min_alnum


class _Fence:
    """Tracks fenced code (``` or ~~~): closes only with the same character and
    at least the opening length, per CommonMark."""

    def __init__(self):
        self.opener = None

    def marker(self, line):
        """True when `line` opens or closes a fence (it is then not content)."""
        m = FENCE_RE.match(line)
        if m is None:
            return False
        run, rest = m.group(1), m.group(2)
        if self.opener is None:
            if run[0] == "`" and "`" in rest:
                return False  # inline code, not a fence
            self.opener = (run[0], len(run))
            return True
        if run[0] == self.opener[0] and len(run) >= self.opener[1] and not rest.strip():
            self.opener = None
            return True
        return False

    @property
    def inside(self):
        return self.opener is not None


def scan_decisions(text):
    """[[number, question, answer, reason]] for every `## D<n>: ...` heading.
    `reason` is '' when settled, else 'no answer' or 'placeholder answer'.

    The answer is the same-line text after `Decision:`, or, when that is empty,
    the next non-empty line (a bullet marker is dropped)."""
    entries, current, awaiting = [], None, False
    fence = _Fence()
    for line in text.splitlines():
        if fence.marker(line) or fence.inside:
            continue
        heading = DECISION_HEADING_RE.match(line)
        if heading:
            current = [int(heading.group(1)), heading.group(2), "", "no answer"]
            entries.append(current)
            awaiting = False
            continue
        if line.startswith("#"):
            current, awaiting = None, False  # any other heading ends the section
            continue
        if current is None or current[3] == "":
            continue
        if awaiting:
            if not line.strip():
                continue
            awaiting = False
            if WHY_LINE_RE.match(line):
                continue
            answer = re.sub(r"^[ \t]*[-*][ \t]+", "", line).strip().strip("*").strip()
        else:
            m = DECISION_LINE_RE.match(line)
            if not m:
                continue
            answer = m.group(1).strip().strip("*").strip()
            if not answer:
                awaiting = True
                continue
        if current[2]:
            continue
        current[2] = answer
        current[3] = "placeholder answer" if is_placeholder(answer) else ""
    return entries


def parse_decisions(text):
    """[(number, question, decision)] for every section with a real answer."""
    return [(n, q, a) for n, q, a, reason in scan_decisions(text) if not reason]


def _read_text(path):
    return Path(path).read_text(encoding="utf-8-sig")  # tolerate a BOM


def settled_decisions(plan_dir):
    """Settled decisions in decisions.md, or None when the file is unreadable."""
    try:
        return parse_decisions(_read_text(Path(plan_dir) / DECISIONS_FILE))
    except (OSError, ValueError):
        return None


def decision_errors(plan, plan_dir):
    """The interview gate: decisions.md must record enough settled questions."""
    needed = min_decisions(plan)
    if needed == 0:
        return []
    path = Path(plan_dir) / DECISIONS_FILE
    how = (
        "Record each question settled with the user as a section `## D<n>: <question>` "
        "followed by a non-empty `Decision: <answer>` line (the answer may also start on the "
        "next line; optional `Why: <reason>`). Finish the interview with the user before "
        "writing the plan. " + RECOVERY.format(needed=needed)
    )
    try:
        entries = scan_decisions(_read_text(path))
    except (OSError, ValueError):
        return [f"Interview record is missing: {path}. The plan needs at least {needed} settled decisions. {how}"]
    errors = []
    ids = [e[0] for e in entries]
    duplicates = sorted({n for n in ids if ids.count(n) > 1})
    if duplicates:
        errors.append(
            f"{DECISIONS_FILE} repeats decision id(s): " + ", ".join(f"D{n}" for n in duplicates)
            + ". Each `## D<n>:` heading must be unique.")
    settled = {n for n, _q, _a, reason in entries if not reason}
    if len(settled) < needed:
        rejected = [f"D{n} ({q[:40]}): {reason}" for n, q, _a, reason in entries if reason]
        detail = f" Rejected: {'; '.join(rejected)}." if rejected else ""
        errors.append(
            f"{DECISIONS_FILE} has {len(settled)} distinct settled decision(s); the plan needs at least "
            f"{needed} (plan.json 'min_decisions').{detail} {how}")
    return errors


def _strip_comments(line, in_comment):
    """(line without HTML comments, still inside a multi-line comment)."""
    out, i = [], 0
    while i < len(line):
        if in_comment:
            j = line.find("-->", i)
            if j < 0:
                return "".join(out), True
            i, in_comment = j + 3, False
        else:
            j = line.find("<!--", i)
            if j < 0:
                out.append(line[i:])
                break
            out.append(line[i:j])
            i, in_comment = j + 4, True
    return "".join(out), in_comment


def feature_sections(text):
    """{lowercased `## ` heading: meaningful content lines} of a feature file.
    Fenced code lines are content (the markers are not), headings inside fences
    never count, HTML comments are dropped but text beside them is kept, and
    placeholder-only lines (TBD, TODO...) are not content."""
    sections, current, in_comment = {}, None, False
    fence = _Fence()
    for line in text.splitlines():
        if fence.marker(line):
            continue
        if fence.inside:
            if current is not None and line.strip():
                sections[current].append(line.strip())
            continue
        line, in_comment = _strip_comments(line, in_comment)
        heading = re.match(r"^##[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if re.match(r"^#[ \t]", line):
            current = None  # a new top-level heading ends the section
            continue
        stripped = line.strip()
        if current is not None and stripped and not is_placeholder(stripped, 1):
            sections[current].append(stripped)
    return sections


def feature_section_errors(md_path, needed=DEFAULT_MIN_DECISIONS):
    """Errors for a feature file lacking a required `## ` section or holding an empty one."""
    try:
        sections = feature_sections(_read_text(md_path))
    except (OSError, ValueError):
        return []  # existence is reported separately
    missing = [s for s in REQUIRED_FEATURE_SECTIONS if s.lower() not in sections]
    empty = [s for s in REQUIRED_FEATURE_SECTIONS if s.lower() in sections and not sections[s.lower()]]
    errors = []
    name = Path(md_path).name
    if missing:
        errors.append(
            f"Feature file {name} is missing required section(s): "
            + ", ".join(f"'## {s}'" for s in missing)
            + ". Add each as a level-2 heading with real content. " + RECOVERY.format(needed=needed)
        )
    if empty:
        errors.append(
            f"Feature file {name} has empty section(s): "
            + ", ".join(f"'## {s}'" for s in empty)
            + ". Fill them in with real content (a bare TBD/TODO/none counts as empty; "
            "for 'Out of scope' say what is excluded, or 'Nothing beyond <the behavior above>')."
        )
    return errors


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
    """sha256 of the plan content minus statuses/approved_at/delivery settings
    and per-feature runtime fields, plus the bytes of general.md, decisions.md
    and features/*.md."""
    plan = load(plan_dir)
    ignored = ("status", "approved_at") + DELIVERY_PLAN_FIELDS
    canon = {k: v for k, v in plan.items() if k not in ignored}
    raw_features = plan.get("features") if isinstance(plan.get("features"), list) else []
    canon["features"] = [
        {k: v for k, v in f.items() if k not in RUNTIME_FEATURE_FIELDS} if isinstance(f, dict) else f
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
    # decisions.md joins the hash only when present, so a plan approved before
    # the interview record existed keeps its approval until it is edited.
    decisions = Path(plan_dir) / DECISIONS_FILE
    if decisions.is_file():
        files.append(("decisions", decisions))
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
        base = load(plan_dir).get("base_branch")
        base = base.strip() if isinstance(base, str) and valid_base_branch(base.strip()) else ""
        tmp.write_text(json.dumps({
            "sha256": content_hash(plan_dir), "approved_at": now_iso,
            "plan_dir": str(Path(plan_dir).resolve()),
            # Delivery targets pinned at approval: the flow refuses to push or
            # open PRs if plan.json or the remote no longer match them.
            "base_branch": base, "origin_url": git_origin_url(_project_root(plan_dir)),
        }, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        raise PlanError(f"Could not record the approval at {path}: {e}")


def approval_pins(plan_dir):
    """{'base_branch', 'origin_url'} pinned at approval, or None when the
    approval record is missing or predates pinning."""
    try:
        data = json.loads(approval_file(plan_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "base_branch" not in data or "origin_url" not in data:
        return None
    return {"base_branch": data.get("base_branch") or "", "origin_url": data.get("origin_url") or ""}


def pin_current_targets(plan_dir):
    """One-time upgrade path: an approval recorded before pinning existed gets
    the CURRENT base branch and origin URL pinned, but only while the plan
    content still matches the approval (so nothing unapproved is blessed)."""
    if approval_pins(plan_dir) is not None or approval_errors(plan_dir):
        return False
    path = approval_file(plan_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        base = load(plan_dir).get("base_branch")
        base = base.strip() if isinstance(base, str) and valid_base_branch(base.strip()) else ""
        data["base_branch"] = base
        data["origin_url"] = git_origin_url(_project_root(plan_dir))
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, ValueError, PlanError):
        return False
    return True


def migrate_flow(plan):
    """One-time migration to flow version 1 (in place; True when changed).

    Features merged before the delivery flow existed must never trigger a
    sync, worktree reset or dispatch: they are marked synced up front."""
    if plan.get("flow_version", 0) >= FLOW_VERSION:
        return False
    for f in _features(plan):
        if f.get("status") == "merged" and not f.get("synced_at"):
            f["synced_at"] = "migrated"
    plan["flow_version"] = FLOW_VERSION
    return True


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

def _check_transition(plan, feature, target):
    current = feature.get("status")
    if target not in TRANSITIONS.get(current, frozenset()):
        raise TransitionError(feature.get("id"), current, target, "not an allowed move")
    if target in IN_PROGRESS:
        busy = [f.get("id") for f in in_progress_features(plan) if f is not feature]
        if busy:
            raise TransitionError(
                feature.get("id"), current, target,
                f"feature '{busy[0]}' is already in progress; features are sequential",
            )


def reset_runtime_counters(feature):
    """Clear the per-cycle review state of a feature."""
    feature["review_rounds"] = 0
    feature["escalated"] = False
    feature.pop("metrics", None)
    # A reopened (or freshly reserved) feature has no PR, final commit or sync yet.
    for name in STRING_RUNTIME_FIELDS:
        feature.pop(name, None)


def _set_status_unlocked(plan_dir, feature_id, status, force=False):
    if status not in FEATURE_STATUSES:
        raise PlanError(f"Invalid status '{status}'; allowed: {', '.join(FEATURE_STATUSES)}.")
    plan = load(plan_dir)
    feature = feature_by_id(plan, feature_id)
    if feature is None:
        raise PlanError(f"Unknown feature '{feature_id}'.")
    if not force:
        _check_transition(plan, feature, status)
    if feature.get("status") == "awaiting_merge" and status == "coding":
        # The human reopened a finished feature: a fresh review cycle starts.
        # (arch -> coding is architect rework and keeps the lifetime counter.)
        reset_runtime_counters(feature)
    feature["status"] = status
    save(plan_dir, plan)
    return plan


def set_feature_status(plan_dir, feature_id, status, force=False):
    """Human status change. Illegal jumps raise TransitionError unless `force`."""
    with plan_lock(plan_dir):
        return _set_status_unlocked(plan_dir, feature_id, status, force=force)


def mark_coding(plan_dir, feature_id):
    """The single place where a feature enters `coding`."""
    return set_feature_status(plan_dir, feature_id, "coding")


def snapshot_feature(plan_dir, feature_id):
    """Deep copy of a feature object, used to roll a transition back."""
    feature = feature_by_id(load(plan_dir), feature_id)
    return copy.deepcopy(feature) if feature is not None else None


def restore_feature(plan_dir, feature_id, snapshot):
    """Put a feature back exactly as `snapshot` (rollback after a failed outbox write)."""
    if snapshot is None:
        return
    with plan_lock(plan_dir):
        plan = load(plan_dir)
        for i, f in enumerate(plan.get("features", [])):
            if isinstance(f, dict) and f.get("id") == feature_id:
                plan["features"][i] = snapshot
                save(plan_dir, plan)
                return
        raise PlanError(f"Unknown feature '{feature_id}'.")


def reserve_feature(plan_dir, task, base_commit=None):
    """Under one lock: re-run the specifier -> coder gate, then mark the
    feature `coding` and record the commit the work starts from. Returns
    (errors, snapshot); empty errors means reserved and `snapshot` is the
    feature as it was, for rollback."""
    with plan_lock(plan_dir):
        errors = check_specifier_to_coder(plan_dir, task)
        if errors:
            return errors, None
        snapshot = snapshot_feature(plan_dir, task)
        plan = _set_status_unlocked(plan_dir, task, "coding")
        # A new feature starts with a clean review state and no stale base.
        feature = feature_by_id(plan, task)
        reset_runtime_counters(feature)
        feature.pop("base_commit", None)
        if base_commit:
            feature["base_commit"] = base_commit
        save(plan_dir, plan)
    return [], snapshot


def planned_transition(sender, recipients, verdict=None):
    """(from_status, to_status) a validated handoff implies, or None."""
    if len(recipients) != 1:
        return None
    to = recipients[0]
    if sender == "coder" and to == "reviewer":
        return "coding", "review"
    if sender == "reviewer" and to == "coder" and verdict == "fail":
        return "review", "coding"
    if sender == "reviewer" and to == "architect" and verdict in ("pass", "fail"):
        return "review", "arch"
    if sender == "architect" and to == "specifier":
        return "arch", "awaiting_merge"
    if sender == "architect" and to == "coder":
        return "arch", "coding"
    return None


class HandoffTransition:
    """Result of applying a handoff's status move (see apply_handoff_transition)."""

    def __init__(self, snapshot=None, escalated=False, skipped=""):
        self.snapshot = snapshot
        self.escalated = escalated
        self.skipped = skipped


def apply_handoff_transition(plan_dir, sender, recipients, task, verdict=None, metrics=None):
    """Move the feature named by `task` for a validated handoff, under the plan
    lock. Returns None when the task is not a feature of the active plan
    (legacy work: no transition, no rejection). A feature whose status is not
    the expected source is left alone and `skipped` explains why: a drifted
    status must not deadlock the swarm; the human fixes it with set-status.
    """
    move = planned_transition(sender, recipients, verdict)
    if move is None or not task or not exists(plan_dir):
        return None
    with plan_lock(plan_dir):
        plan = load(plan_dir)
        feature = feature_by_id(plan, task)
        if feature is None:
            return None
        src, dst = move
        if feature.get("status") != src:
            return HandoffTransition(skipped=(
                f"feature '{task}' is '{feature.get('status')}', expected '{src}'; "
                f"status left unchanged"
            ))
        snapshot = copy.deepcopy(feature)
        escalated = False
        if sender == "reviewer" and verdict == "fail":
            if dst == "arch":
                escalated = True
                feature["escalated"] = True
            else:
                feature["review_rounds"] = _safe_rounds(feature) + 1
        if sender == "coder" and dst == "review":
            # Stale metrics must not outlive a skipped computation.
            if metrics is not None:
                feature["metrics"] = metrics
            else:
                feature.pop("metrics", None)
        feature["status"] = dst
        save(plan_dir, plan)
    return HandoffTransition(snapshot=snapshot, escalated=escalated)


def _safe_rounds(feature):
    rounds = feature.get("review_rounds", 0)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
        return 0
    return rounds


def max_review_rounds(plan):
    cap = plan.get("max_review_rounds") if isinstance(plan, dict) else None
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        return DEFAULT_MAX_REVIEW_ROUNDS
    return cap


def review_cap_reached(plan_dir, task):
    """True when `task` is a feature whose review_rounds reached the plan cap.
    Anything unreadable or non-feature answers False."""
    try:
        if not task or not exists(plan_dir):
            return False
        plan = load(plan_dir)
        feature = feature_by_id(plan, task)
        if feature is None:
            return False
        return _safe_rounds(feature) >= max_review_rounds(plan)
    except PlanError:
        return False


def note_to_coder_errors(plan_dir):
    """A specifier -> coder `note` is only legitimate while a feature is in
    progress; otherwise it would start work outside the approved plan."""
    if not exists(plan_dir):
        return []
    try:
        plan = load(plan_dir)
    except PlanError as e:
        return [str(e)]
    if any(f.get("status") in ("coding", "review", "arch") for f in _features(plan)):
        return []
    return [
        "A 'note' from the specifier to the coder is only accepted while a plan feature is "
        "in progress (coding, review or arch). Work starts only through an approved plan "
        "feature handoff: send a git_handoff whose task is the next ready feature id."
    ]


# --- advisory metrics ---------------------------------------------------------

LOCKFILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
    "Pipfile.lock", "Cargo.lock", "Gemfile.lock", "composer.lock", "go.sum",
})


def is_non_production(path):
    """Tests, lockfiles and docs markdown do not count as production lines."""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    if any(p in ("tests", "__tests__") for p in parts[:-1]):
        return True
    if (name.startswith("test_") or fnmatch.fnmatch(name, "*_test.*")
            or fnmatch.fnmatch(name, "*.spec.*") or fnmatch.fnmatch(name, "*.test.*")):
        return True
    if name in LOCKFILES or name.endswith(".lock"):
        return True
    return parts[0] == "docs" and name.endswith(".md")


def _glob_regex(pattern):
    pattern = pattern.replace("\\", "/")
    if pattern.startswith("./"):
        pattern = pattern[2:]
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    # A bare directory (`src/feature`) also covers everything below it.
    return re.compile("".join(out) + "(?:/.*)?")


def outside_allowed(paths, allowed_globs):
    """Files in `paths` matched by none of `allowed_globs` (empty = unrestricted)."""
    if not allowed_globs:
        return []
    regexes = [_glob_regex(g) for g in allowed_globs]
    return [p for p in paths if not any(r.fullmatch(p) for r in regexes)]


def compute_metrics(base, commit, allowed_globs, cwd=None):
    """Advisory size/scope numbers for base..commit, or None when git fails.
    Never raises; callers must treat the result as a warning only."""
    try:
        r = subprocess.run(
            ["git", "diff", "--numstat", "--no-renames", "-z", base, commit],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(cwd) if cwd else None, timeout=30,
        )
    except (subprocess.SubprocessError, OSError):  # includes TimeoutExpired
        return None
    if r.returncode != 0:
        return None
    prod_lines, files = 0, []
    for entry in r.stdout.split("\0"):
        parts = entry.strip("\n").split("\t", 2)
        if len(parts) != 3:
            continue
        added, removed, path = parts
        files.append(path)
        if is_non_production(path):
            continue
        if added.isdigit() and removed.isdigit():  # binary files show "-"
            prod_lines += int(added) + int(removed)
    scoped = [p for p in files if not is_non_production(p)]
    return {
        "prod_lines": prod_lines,
        "files_total": len(files),
        "outside_allowed": outside_allowed(scoped, allowed_globs or []),
    }


def plan_check_text(metrics):
    """One short advisory block for the reviewer (and the coder's stderr)."""
    lines = metrics["prod_lines"]
    head = f"~{lines} prod lines (target ~{LINE_GUIDE}"
    head += ", over target)" if lines > LINE_GUIDE else ")"
    outside = metrics.get("outside_allowed") or []
    if outside:
        shown = ", ".join(outside[:10]) + (f" (+{len(outside) - 10} more)" if len(outside) > 10 else "")
        scope = f"{len(outside)} file(s) outside allowed_paths: {shown}"
    else:
        scope = "all production files inside allowed_paths"
    return f"Plan check: {head}; {scope}. Advisory only."


def update_feature(plan_dir, feature_id, **fields):
    """Set runtime fields on one feature under the plan lock (a None value
    removes the field). Returns the updated plan."""
    with plan_lock(plan_dir):
        plan = load(plan_dir)
        feature = feature_by_id(plan, feature_id)
        if feature is None:
            raise PlanError(f"Unknown feature '{feature_id}'.")
        for key, value in fields.items():
            if value is None:
                feature.pop(key, None)
            else:
                feature[key] = value
        save(plan_dir, plan)
    return plan


def mark_merged(plan_dir, feature_id, merge_commit=""):
    """Code-owned edge `awaiting_merge -> merged` (merge detected). The human
    `set-status` reaches the same status through set_feature_status."""
    with plan_lock(plan_dir):
        plan = _set_status_unlocked(plan_dir, feature_id, "merged")
        feature = feature_by_id(plan, feature_id)
        if merge_commit:
            feature["merge_commit"] = merge_commit
        save(plan_dir, plan)
    return plan


def set_plan_status(plan_dir, status):
    if status not in PLAN_STATUSES:
        raise PlanError(f"Invalid plan status '{status}'.")
    with plan_lock(plan_dir):
        plan = load(plan_dir)
        plan["status"] = status
        save(plan_dir, plan)
    return plan


def detect_base_branch(root):
    """Branch checked out in the master checkout `root`, else the remote's
    default branch, else None. Never raises."""
    def run(*args):
        try:
            r = subprocess.run(
                ["git", *args], capture_output=True, text=True, cwd=str(root),
                timeout=15, stdin=subprocess.DEVNULL,
            )
        except (subprocess.SubprocessError, OSError):
            return ""
        return r.stdout.strip() if r.returncode == 0 else ""

    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    if branch and branch != "HEAD":
        return branch
    remote_head = run("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if remote_head:
        return remote_head[len("origin/"):] if remote_head.startswith("origin/") else remote_head
    return None


def approve(plan_dir, now_iso):
    with plan_lock(plan_dir):
        plan = load(plan_dir)
        plan["status"] = "approved"
        plan["approved_at"] = now_iso
        # Where the finished features' PRs will point: the branch the human is
        # working on right now. A value the human wrote into plan.json wins.
        existing = plan.get("base_branch")
        if not (isinstance(existing, str) and existing.strip()):
            detected = detect_base_branch(_project_root(plan_dir))
            if detected:
                plan["base_branch"] = detected
        chosen = plan.get("base_branch")
        if isinstance(chosen, str) and chosen.strip() and not valid_base_branch(chosen.strip()):
            raise PlanError(
                f"base_branch '{chosen}' is not a safe branch name "
                "(no 'swarm/' prefix, '..', whitespace or leading '-').")
        migrate_flow(plan)
        for f in _features(plan):
            # Runtime state is written only by the swarm; never let a
            # pre-seeded value ride through approval.
            reset_runtime_counters(f)
            f.pop("base_commit", None)
        save(plan_dir, plan)
        record_approval(plan_dir, now_iso)
    return plan


# --- handoff gate ---------------------------------------------------------

def check_specifier_to_coder(plan_dir, task):
    """Agent-facing errors for a specifier -> coder git_handoff (empty = ok)."""
    if not exists(plan_dir):
        return [
            f"No feature plan found at {plan_path(plan_dir)}. Create the plan "
            "(plan.json, general.md, decisions.md, features/<id>.md), run `swarm plan validate`, "
            "and ask the user to run `swarm plan approve` before handing work to the coder."
        ]
    try:
        plan = load(plan_dir)
    except PlanError as e:
        return [str(e)]
    if plan.get("status") == "done":
        return ["The feature plan is complete: every feature is merged. There is nothing left to hand off."]
    if plan.get("status") != "approved":
        return [
            "The feature plan is not approved. Ask the user to run `swarm plan approve`; "
            "you cannot approve it yourself."
        ]
    # An approved plan may have been edited into an invalid state since.
    # interview=False: plans approved before the interview checks existed must
    # keep working; approval-hash integrity below still guards their content.
    invalid, _warnings = validate(plan, plan_dir, interview=False)
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
