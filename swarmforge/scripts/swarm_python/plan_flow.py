"""Publish, merge detection, sync and next-feature dispatch for plan features.

Features are strictly sequential. After the architect reports a feature
complete, CODE (never an LLM) does the delivery:

  1. publish      push the architect's commit to `swarm/<plan>/<feature>` and
                  open a PR into the base branch (`awaiting_merge` = PR open)
  2. poll_merges  detect that the human merged the PR (gh, or the commit being
                  reachable from the base branch)
  3. sync         fast-forward the master checkout and reset the idle role
                  worktrees to the new base, never touching uncommitted work
  4. dispatch     reserve the next ready feature through the normal gate and
                  queue the specifier -> coder handoff

Safety rules that hold everywhere in this module:
  * the swarm never merges a PR and never pushes to the base branch;
  * pushes are never forced and only target `refs/heads/swarm/...`;
  * a worktree is reset only when it is provably idle and clean (no unfinished
    git operation, right branch, empty inbox/outbox, nothing untracked or
    colliding); local commits that would be orphaned get a backup ref first;
  * delivery targets (base branch, origin URL) are pinned at approval and a
    mismatch refuses to push; gh answers are cross-checked before being trusted;
  * the master checkout is never switched, committed to or pushed from;
  * nothing here may raise into the handoff daemon: public hooks catch and log.

In the daemon all of the above runs on ONE background worker thread fed by a
queue, so handoff delivery and the scheduler tick are never blocked by git or
gh. With `synchronous=True` (tests and the `swarm plan` CLI) the same work runs
inline on the calling thread.

Git and gh are reached through injectable runners, so tests need no network.
"""
import datetime
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import uuid
from collections import namedtuple
from pathlib import Path

from . import plan as planlib
from .env import env_long
from .handoff.timefmt import now_id_ts, now_iso
from .scheduler import pending_work

Result = namedtuple("Result", "rc out err")
PublishResult = namedtuple("PublishResult", "ok message pr_url")

GIT_TIMEOUT = 60
PUSH_TIMEOUT = 120
GH_TIMEOUT = 60
FETCH_TIMEOUT = 120
# Daemon path: short bounds so a hung network never parks the worker for long.
DAEMON_TIMEOUTS = {"git_timeout": 20, "gh_timeout": 20, "push_timeout": 90, "fetch_timeout": 30}
DEFAULT_POLL_S = 30
BACKOFF_CAP_S = 300
FLOW_LOCK_STALE_S = 600
DEFER_NOTE_AFTER = 5
MAX_REPORT_CHARS = 4000
NO_WORKTREE = ("none", "master")
PR_FIELDS = "state,baseRefName,headRefName,headRefOid,mergeCommit"
GUARDED_STATE_PATHS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")

_REDACTIONS = (
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "[redacted-token]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[redacted-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[redacted-token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-token]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [redacted]"),
    (re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key)(\s*[:=]\s*)[^\s`'\"]+"), r"\1\2[redacted]"),
    (re.compile(r"[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+[^\\/\s`'\"]+"), "<user-dir>"),
    (re.compile(r"/(?:home|Users)/[^/\s`'\"]+"), "<user-dir>"),
)


def _run(argv, cwd, timeout, extra_env):
    env = dict(os.environ)
    env.update(extra_env)
    try:
        r = subprocess.run(
            argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return Result(127, "", f"{argv[0]} not found")
    except subprocess.TimeoutExpired:
        return Result(124, "", f"{argv[0]} timed out after {timeout}s")
    except (subprocess.SubprocessError, OSError) as e:
        return Result(126, "", f"{argv[0]} failed to run: {e}")
    return Result(r.returncode, r.stdout.strip(), r.stderr.strip())


def run_git(args, cwd=None, timeout=GIT_TIMEOUT):
    return _run(["git", *args], cwd, timeout, {"GIT_TERMINAL_PROMPT": "0"})


def run_gh(args, cwd=None, timeout=GH_TIMEOUT):
    return _run(["gh", *args], cwd, timeout, {"GH_PROMPT_DISABLED": "1"})


def poll_interval_s():
    return env_long("SWARMFORGE_PLAN_POLL_S", DEFAULT_POLL_S)


# --- pure helpers -------------------------------------------------------------

def _slug(text):
    """A string safe to embed in a git ref component."""
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-.")
    out = re.sub(r"\.{2,}", ".", out)
    if out.endswith(".lock"):
        out = out[: -len(".lock")] + "-lock"
    return out or "plan"


def branch_name(plan, feature_id):
    return f"swarm/{_slug(plan.get('id', 'plan'))}/{_slug(feature_id)}"


def next_revision_branch(branch):
    """`swarm/p/f` -> `swarm/p/f-r2` -> `swarm/p/f-r3` ..."""
    m = re.search(r"-r(\d+)$", branch)
    if m:
        return f"{branch[:m.start()]}-r{int(m.group(1)) + 1}"
    return f"{branch}-r2"


def redact(text, extra_paths=()):
    """Strip absolute user paths and common credential shapes before text is
    published on a PR."""
    text = str(text or "")
    for p in extra_paths:
        for variant in {str(p), str(p).replace("\\", "/")}:
            if len(variant) > 3:
                text = text.replace(variant, "<repo>")
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def _position(feats, feature):
    """1-based position of `feature` in the plan, matched by id."""
    for i, f in enumerate(feats):
        if f.get("id") == feature.get("id"):
            return i + 1
    return 1


def pr_title(plan, feature):
    feats = planlib._features(plan)
    index = _position(feats, feature)
    return f"{feature.get('title', feature.get('id', ''))} ({plan.get('title', plan.get('id', ''))}, {index}/{len(feats)})"


def build_pr_body(plan, feature, metrics=None, previous_url=""):
    """PR description (English). Advisory numbers only: ~400 lines never blocks.

    Documentation intent: the plan, specs and briefs live in the ignored
    `.swarmforge/plan/` directory, so they are never part of the pushed commit.
    The commit is pushed exactly as the architect handed it over."""
    feats = planlib._features(plan)
    index = _position(feats, feature)
    lines = ["## Summary", "", str(feature.get("summary", "")).strip() or "(no summary)", ""]
    report = str(feature.get("report", "")).strip()[:MAX_REPORT_CHARS]
    if report:
        lines += ["## Architect report", "", report, ""]
    lines += ["## Acceptance criteria", ""]
    lines += [f"- [ ] {a}" for a in feature.get("acceptance", [])]
    lines += ["", "## Plan metrics (advisory)", ""]
    if metrics:
        prod = metrics.get("prod_lines", 0)
        target = planlib.LINE_GUIDE
        lines.append(
            f"- Production lines changed: ~{prod} (target ~{target}, "
            f"{'over target' if prod > target else 'within target'})"
        )
        outside = metrics.get("outside_allowed") or []
        if outside:
            shown = ", ".join(f"`{p}`" for p in outside[:15])
            more = f" (+{len(outside) - 15} more)" if len(outside) > 15 else ""
            lines.append(f"- Files outside allowed_paths: {len(outside)}: {shown}{more}")
        else:
            lines.append("- Files outside allowed_paths: none")
    else:
        lines.append("- Size and scope metrics are not available for this feature")
    rounds = feature.get("review_rounds", 0)
    lines.append(
        f"- Review rounds: {rounds if isinstance(rounds, int) and not isinstance(rounds, bool) else 0}; "
        f"escalated to the architect: {'yes' if feature.get('escalated') else 'no'}"
    )
    lines += ["", f"Previous feature PR: {previous_url}" if previous_url else "Previous feature PR: none (first PR of the plan)"]
    lines += [
        "",
        "---",
        f"Opened automatically by SwarmForge (plan `{plan.get('id', '')}`, feature {index} of {len(feats)}). "
        "The swarm never merges PRs: review and merge this one, and it will detect the merge "
        "and continue with the next feature.",
    ]
    return "\n".join(lines) + "\n"


def extract_report(body):
    """The architect's free-form report inside a delivered handoff body."""
    kept = []
    for line in str(body or "").splitlines():
        if line.startswith("Re-read your role and constitution.") or line.startswith("merge_and_process "):
            continue
        kept.append(line)
    return "\n".join(kept).strip()[:MAX_REPORT_CHARS]


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def roles_from_tsv(path):
    """role -> {worktree-name, worktree-path} from roles.tsv (same file the daemon reads)."""
    out = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        fields = line.split("\t")
        if fields and fields[0]:
            out[fields[0]] = {
                "role": fields[0],
                "worktree-name": fields[1] if len(fields) > 1 else "",
                "worktree-path": fields[2] if len(fields) > 2 else "",
            }
    return out


# --- the flow -----------------------------------------------------------------

class PlanFlow:
    def __init__(self, project_root, git=run_git, gh=run_gh, log=None, clock=time.monotonic,
                 poll_interval=None, synchronous=True, git_timeout=GIT_TIMEOUT,
                 gh_timeout=GH_TIMEOUT, push_timeout=PUSH_TIMEOUT, fetch_timeout=FETCH_TIMEOUT):
        self.root = Path(project_root)
        self.plan_dir = self.root / ".swarmforge" / "plan"
        self.git = git
        self.gh = gh
        self.extra_log = log
        self.clock = clock
        self.poll_interval = poll_interval_s() if poll_interval is None else poll_interval
        self.synchronous = synchronous
        self.t_git, self.t_gh = git_timeout, gh_timeout
        self.t_push, self.t_fetch = push_timeout, fetch_timeout
        self._next_poll = None        # earliest clock() of the next poll; set AFTER a poll ends
        self._failures = 0            # consecutive failed polls (drives the backoff)
        self._poll_pending = False    # a poll job is queued or running (async mode)
        self._deferrals = {}          # feature id -> consecutive sync deferrals
        self._defer_until = 0.0       # sync retry is skipped until this clock() value
        self._last_deferral = None
        self._noted = set()           # one-time note keys (per process)
        self._queue = None
        self._worker = None
        self._worker_lock = threading.Lock()

    # -- logging --

    def _log(self, *parts):
        line = f"{now_iso()} {' '.join(str(p) for p in parts)}\n"
        try:
            logs = self.root / ".swarmforge" / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            with (logs / "plan-flow.log").open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
        if self.extra_log is not None:
            try:
                self.extra_log("plan-flow", *parts)
            except Exception:  # noqa: BLE001 - logging must never break the flow
                pass

    # -- worker thread --

    def _ensure_worker(self):
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._queue = queue.Queue()
            self._worker = threading.Thread(
                target=self._worker_loop, args=(self._queue,), name="plan-flow", daemon=True)
            self._worker.start()

    def _worker_loop(self, jobs):
        while True:
            item = jobs.get()
            if item is None:
                return
            kind, args = item
            try:
                if kind == "delivered":
                    self._on_delivered(*args)
                elif kind == "poll":
                    self._poll(*args)
            except Exception as e:  # noqa: BLE001 - the worker must survive any job
                self._log("worker-error", kind, repr(e))
            finally:
                if kind == "poll":
                    self._poll_pending = False

    def _submit(self, kind, *args):
        self._ensure_worker()
        self._queue.put((kind, args))

    def close(self, timeout=5):
        """Stop the worker after the queued jobs; never blocks longer than `timeout`."""
        with self._worker_lock:
            worker, jobs = self._worker, self._queue
            self._worker = None
        if worker is not None and jobs is not None:
            jobs.put(None)
            worker.join(timeout)

    # -- git helpers --

    def _g(self, *args, cwd=None, timeout=None):
        return self.git(list(args), cwd=cwd or self.root, timeout=timeout or self.t_git)

    def _has_origin(self):
        return self._g("remote", "get-url", "origin").rc == 0

    def _origin_url(self):
        r = self._g("remote", "get-url", "origin")
        return r.out if r.rc == 0 else ""

    def _resolve(self, rev, cwd=None):
        r = self._g("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", cwd=cwd)
        return r.out if r.rc == 0 and r.out else ""

    def _is_ancestor(self, older, newer, cwd=None):
        return self._g("merge-base", "--is-ancestor", older, newer, cwd=cwd).rc == 0

    def _feature_branch(self, plan, feature):
        return feature.get("pub_branch") or branch_name(plan, feature["id"])

    def _targets(self, plan):
        """(base branch, error). The base must be a safe branch name and, together
        with the origin URL, must equal what was pinned when the human approved."""
        base = plan.get("base_branch")
        base = base.strip() if isinstance(base, str) and base.strip() else (
            planlib.detect_base_branch(self.root) or "")
        if not planlib.valid_base_branch(base):
            return "", f"the base branch {base!r} is empty or not a safe branch name"
        planlib.pin_current_targets(self.plan_dir)  # only acts on pre-pinning approvals
        pins = planlib.approval_pins(self.plan_dir)
        if pins is None:
            return "", ("no approval record with pinned delivery targets exists; "
                        "ask the user to run `swarm plan approve` again")
        if pins["base_branch"] != base:
            return "", (f"base_branch is '{base}' but '{pins['base_branch']}' was approved; "
                        "ask the user to run `swarm plan approve` again if the change is intended")
        url = self._origin_url()
        if url and url != pins["origin_url"]:
            return "", ("the origin remote URL changed since the plan was approved; "
                        "ask the user to check it and run `swarm plan approve` again")
        return base, ""

    def _base_ref(self, base):
        """Ref that holds the merged state: `origin/<base>` when a remote exists."""
        return f"origin/{base}" if self._has_origin() else base

    # -- 1. publish --------------------------------------------------------------

    def on_delivered(self, roles, sender, headers, recipients, body=""):
        """Daemon hook, called after a handoff was delivered. Acts only on the
        architect -> specifier completion handoff of a plan feature. Never raises
        and, in the daemon, returns immediately (the work runs on the worker)."""
        try:
            if sender != "architect" or list(recipients) != ["specifier"]:
                return
            if headers.get("type") != "git_handoff" or not headers.get("task"):
                return
            if self.synchronous:
                self._on_delivered(roles, headers, body)
            else:
                self._submit("delivered", roles, dict(headers), body)
        except Exception as e:  # noqa: BLE001
            self._log("publish-error", repr(e))

    def _on_delivered(self, roles, headers, body):
        task = headers["task"]
        try:
            if not planlib.exists(self.plan_dir):
                return
            plan = planlib.load(self.plan_dir)
            feature = planlib.feature_by_id(plan, task)
            if feature is None:
                return
            if feature.get("status") != "awaiting_merge":
                self._log("publish-skipped", task, f"status is '{feature.get('status')}', not awaiting_merge")
                return
            final = self._resolve_handoff_commit(roles, headers.get("commit", ""))
            if not final:
                self._log("publish-skipped", task, "handoff commit does not resolve")
                return
            planlib.update_feature(self.plan_dir, task, final_commit=final, report=extract_report(body) or None)
            if plan.get("auto_publish", True) is False:
                self._log("publish-skipped", task, "auto_publish is false")
                return
            result = self.publish(task)
            if not result.ok:
                self._notify_publish_failure(roles, task, result.message)
        except Exception as e:  # noqa: BLE001
            self._log("publish-error", repr(e))
            self._notify_publish_failure(roles, task, f"unexpected error: {e!r}")

    def _resolve_handoff_commit(self, roles, short):
        """Full hash of the handoff's commit. An abbreviation that git reports as
        ambiguous falls back to the architect branch tip when that tip matches."""
        arch = (roles.get("architect") or {}).get("worktree-path") or None
        if not short:
            return ""
        full = self._resolve(short, cwd=arch) or self._resolve(short)
        if full:
            return full
        tip = self._resolve("refs/heads/swarmforge-architect")
        return tip if tip and tip.startswith(short) else ""

    def publish(self, feature_id):
        """Push the feature's final commit to its own branch and open a PR.
        Returns PublishResult; never raises for git/gh problems."""
        try:
            return self._publish(feature_id)
        except planlib.PlanError as e:
            return PublishResult(False, str(e), "")

    def _publish(self, feature_id):
        plan = planlib.load(self.plan_dir)
        feature = planlib.feature_by_id(plan, feature_id)
        if feature is None:
            return PublishResult(False, f"Unknown feature '{feature_id}'.", "")
        if feature.get("status") != "awaiting_merge":
            return PublishResult(
                False, f"Feature '{feature_id}' is '{feature.get('status')}', not 'awaiting_merge'.", "")
        republish = bool(feature.get("pr_closed_at"))
        if feature.get("pr_url") and not republish:
            return PublishResult(True, f"Already published: {feature['pr_url']}", feature["pr_url"])
        final = self._resolve(feature.get("final_commit") or "")
        if not final:
            # The completion handoff was never seen: fall back to the architect's branch tip.
            final = self._resolve("refs/heads/swarmforge-architect")
            if not final:
                return PublishResult(False, "The feature has no usable final commit recorded.", "")
            planlib.update_feature(self.plan_dir, feature_id, final_commit=final)
            self._log("publish-fallback", feature_id, f"using the swarmforge-architect tip {final[:10]}")
        base, err = self._targets(plan)
        if err:
            return PublishResult(False, f"Refusing to publish: {err}.", "")
        if not self._has_origin():
            return PublishResult(False, "This repository has no 'origin' remote.", "")
        recorded_base = feature.get("base_commit")
        if recorded_base and not self._is_ancestor(recorded_base, final):
            return PublishResult(
                False, f"The final commit {final[:10]} does not descend from the recorded base "
                       f"commit {str(recorded_base)[:10]}; refusing to push it.", "")
        branch = self._feature_branch(plan, feature)
        if republish:
            branch = next_revision_branch(branch)
        for attempt in (1, 2):
            if branch == base or self._g("check-ref-format", f"refs/heads/{branch}").rc != 0:
                return PublishResult(False, f"Refusing to push to unsafe branch name '{branch}'.", "")
            # The exact commit goes to its own branch; the master checkout is not
            # touched and there is no --force, so an existing branch is never rewritten.
            pushed = self._g("push", "origin", f"{final}:refs/heads/{branch}", timeout=self.t_push)
            if pushed.rc == 0:
                break
            if attempt == 1 and re.search(r"rejected|non-fast-forward|fetch first", pushed.err, re.I):
                branch = next_revision_branch(branch)  # stale branch from an older cycle
                continue
            return PublishResult(False, f"git push failed: {pushed.err or pushed.out}", "")

        feats = planlib._features(plan)
        index = _position(feats, feature) - 1
        previous_url = next((f["pr_url"] for f in reversed(feats[:index]) if f.get("pr_url")), "")
        metrics = None
        if recorded_base:
            metrics = planlib.compute_metrics(
                recorded_base, final, feature.get("allowed_paths") or [], cwd=self.root)
        metrics = metrics or feature.get("metrics")
        body = redact(build_pr_body(plan, feature, metrics, previous_url), [self.root])
        url = self._open_pr(plan, feature, base, branch, final, body)
        if not url:
            return PublishResult(
                False, f"The commit was pushed to '{branch}' but gh pr create failed: "
                       f"{self._last_gh_error or 'no PR url returned'}", "")
        planlib.update_feature(self.plan_dir, feature_id, pr_url=url, published_at=now_iso(),
                               pub_branch=branch, pr_closed_at=None)
        self._log("published", feature_id, url)
        return PublishResult(True, f"Published {feature_id}: {url}", url)

    _last_gh_error = ""

    def _open_pr(self, plan, feature, base, branch, final, body):
        """Create the PR (body through a temp file) or reuse an OPEN one that
        points at exactly this commit. Returns the URL or ''."""
        fd, tmp_name = tempfile.mkstemp(prefix="swarm-pr-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
            created = self.gh(
                ["pr", "create", "--base", base, "--head", branch,
                 "--title", pr_title(plan, feature), "--body-file", tmp_name],
                cwd=self.root, timeout=self.t_gh)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        self._last_gh_error = created.err or created.out
        if created.rc == 0:
            url = next((ln.strip() for ln in reversed(created.out.splitlines())
                        if ln.strip().startswith("http")), "")
            if url:
                return url
        # The PR may already exist for this branch (a retry). Reuse it only when
        # it is open and is exactly this commit on this base; an old merged or
        # closed PR of the same branch name must never be adopted.
        viewed = self.gh(["pr", "view", branch, "--json", f"url,{PR_FIELDS}"],
                         cwd=self.root, timeout=self.t_gh)
        if viewed.rc == 0:
            data = self._json(viewed.out)
            if (isinstance(data, dict) and data.get("state") == "OPEN"
                    and self._pr_matches(data, base, branch, final)):
                return str(data.get("url") or "")
        return ""

    @staticmethod
    def _json(text):
        try:
            return json.loads(text)
        except ValueError:
            return None

    @staticmethod
    def _pr_matches(data, base, branch, final):
        return (data.get("baseRefName") == base and data.get("headRefName") == branch
                and bool(final) and data.get("headRefOid") == final)

    def _notify_publish_failure(self, roles, task, why):
        self._log("publish-failed", task, why)
        try:
            self._send_note(
                roles, f"PR for '{task}' not opened; run: swarm plan publish {task}"[:80],
                f"SwarmForge could not publish feature '{task}' automatically.\n\nReason: {why}\n\n"
                f"The feature stays 'awaiting_merge'. Tell the user, who can fix the cause "
                f"(remote, gh login) and run `swarm plan publish {task}`, or push the "
                f"`swarmforge-architect` branch and open the PR by hand. Merge detection keeps "
                f"working once the commit lands on the base branch. Without gh and a login, "
                f"squash or rebase merges cannot be detected: the user then runs "
                f"`swarm plan set-status {task} merged`.")
        except Exception as e:  # noqa: BLE001
            self._log("notify-error", repr(e))

    # -- 2. merge detection --------------------------------------------------------

    def poll_merges(self, roles=None, force=False):
        """Rate-limited. Detect merged PRs, then sync and advance. Returns a list
        of short action descriptions. Never raises. In the daemon it only queues
        the poll for the worker and returns [] at once."""
        try:
            if self.synchronous:
                if not force and self._next_poll is not None and self.clock() < self._next_poll:
                    return []
                return self._poll(roles or {}, force)
            if self._poll_pending:
                return []
            if not force and self._next_poll is not None and self.clock() < self._next_poll:
                return []
            self._poll_pending = True
            try:
                self._submit("poll", roles or {}, force)
            except Exception:
                self._poll_pending = False
                raise
        except Exception as e:  # noqa: BLE001
            self._log("poll-error", repr(e))
        return []

    def _poll_delay(self):
        if self._failures <= 0:
            return self.poll_interval
        return min(BACKOFF_CAP_S, max(self.poll_interval, 1) * (2 ** min(self._failures, 10)))

    def _poll(self, roles, force):
        actions = []
        failed = False
        try:
            if not planlib.exists(self.plan_dir):
                return actions
            plan = self._load_migrated()
            feats = planlib._features(plan)
            awaiting = [f for f in feats if f.get("status") == "awaiting_merge" and not f.get("pr_closed_at")]
            unsynced = [f for f in feats if f.get("status") == "merged" and not f.get("synced_at")]
            if not awaiting and not unsynced:
                return actions
            base, err = self._targets(plan)
            if err:
                self._refuse(roles, err)
                return actions
            fetch_state = None
            if self._has_origin():
                fetched = self._g("fetch", "origin", timeout=self.t_fetch)  # once per poll
                fetch_state = fetched.rc == 0
                if not fetch_state:
                    self._log("fetch-failed", fetched.err)
                    failed = True
            for f in awaiting:
                try:
                    kind, detail = self._detect(plan, f, base, roles)
                    if kind == "merged":
                        planlib.mark_merged(self.plan_dir, f["id"], detail)
                        planlib.update_feature(self.plan_dir, f["id"], merge_source="detected")
                        self._log("merged", f["id"], detail or "(reachable from base)")
                        actions.append(f"feature '{f['id']}' merged")
                    elif kind == "closed":
                        self._on_closed(roles, f)
                except planlib.PlanError as e:
                    self._log("poll-error", f["id"], str(e))
            if force or self.clock() >= self._defer_until:
                actions += self._sync_pending(roles, fetch_state)
        except Exception as e:  # noqa: BLE001
            self._log("poll-error", repr(e))
            failed = True
        finally:
            self._failures = self._failures + 1 if failed else 0
            self._next_poll = self.clock() + self._poll_delay()
        return actions

    def _load_migrated(self):
        plan = planlib.load(self.plan_dir)
        if plan.get("flow_version", 0) < planlib.FLOW_VERSION:
            with planlib.plan_lock(self.plan_dir):
                plan = planlib.load(self.plan_dir)
                if planlib.migrate_flow(plan):
                    planlib.save(self.plan_dir, plan)
                    self._log("migrated", "plan marked as flow version", planlib.FLOW_VERSION)
        return plan

    def _detect(self, plan, feature, base, roles):
        """('merged', merge commit or '') | ('closed', '') | ('none', '').

        gh answers are trusted only when they describe exactly this feature:
        base == the pinned base, head == its swarm branch, head oid == the
        recorded final commit. Anything else is ignored (and reported once)."""
        final = feature.get("final_commit") or ""
        branch = self._feature_branch(plan, feature)
        closed = False
        view = self.gh(["pr", "view", feature.get("pr_url") or branch, "--json", PR_FIELDS],
                       cwd=self.root, timeout=self.t_gh)
        if view.rc == 0:
            data = self._json(view.out)
            if isinstance(data, dict) and self._pr_matches(data, base, branch, final):
                if data.get("state") == "MERGED":
                    return "merged", (data.get("mergeCommit") or {}).get("oid") or ""
                closed = data.get("state") == "CLOSED"
            else:
                self._mismatch(roles, feature, data)
        if final:
            # Squash and rebase merges leave the commit unreachable; gh sees them.
            listed = self.gh(["pr", "list", "--state", "merged", "--head", branch, "--base", base,
                              "--json", PR_FIELDS], cwd=self.root, timeout=self.t_gh)
            if listed.rc == 0:
                items = self._json(listed.out)
                for item in items if isinstance(items, list) else []:
                    if (isinstance(item, dict) and item.get("state", "MERGED") == "MERGED"
                            and self._pr_matches(item, base, branch, final)):
                        return "merged", (item.get("mergeCommit") or {}).get("oid") or ""
            if self._is_ancestor(final, self._base_ref(base)):
                return "merged", ""
        if closed:
            return "closed", ""
        return "none", ""

    def _mismatch(self, roles, feature, data):
        fid = feature["id"]
        self._log("pr-mismatch", fid, "ignoring a gh answer that does not match this feature",
                  json.dumps(data)[:300] if data is not None else "unparseable")
        self._note_once(
            roles, f"mismatch:{fid}", f"PR data for '{fid}' does not match; ignored."[:80],
            f"gh returned a pull request for feature '{fid}' whose base branch, head branch or head "
            f"commit do not match what SwarmForge published, so it was ignored and the feature is "
            f"not treated as merged. Tell the user to check the PR "
            f"({feature.get('pr_url') or 'no url recorded'}) and, if it really was merged, run "
            f"`swarm plan set-status {fid} merged`.")

    def _on_closed(self, roles, feature):
        fid = feature["id"]
        planlib.update_feature(self.plan_dir, fid, pr_closed_at=now_iso())
        self._log("pr-closed", fid, feature.get("pr_url", ""))
        self._note_once(
            roles, f"closed:{fid}:{feature.get('pr_url', '')}",
            f"PR for '{fid}' was closed without merging."[:80],
            f"The PR of feature '{fid}' ({feature.get('pr_url', 'no url')}) was closed without being "
            f"merged. SwarmForge stopped polling this feature. Tell the user: to publish it again "
            f"they run `swarm plan publish {fid}` (a fresh branch is used), or they send the feature "
            f"back with `swarm plan set-status {fid} coding`.")

    # -- 3. sync -------------------------------------------------------------------

    def _sync_pending(self, roles, fetch_state=None):
        try:
            with planlib.plan_lock(self.plan_dir, "plan-flow.lock", stale=FLOW_LOCK_STALE_S):
                plan = planlib.load(self.plan_dir)  # re-read under the lock
                if planlib.in_progress_features(plan):
                    return []  # a feature is running: resetting worktrees now would hurt it
                unsynced = [f for f in planlib._features(plan)
                            if f.get("status") == "merged" and not f.get("synced_at")]
                if not unsynced:
                    return []
                for older in unsynced[:-1]:
                    planlib.update_feature(self.plan_dir, older["id"], synced_at=now_iso())
                return self._sync_locked(unsynced[-1]["id"], roles, fetch_state)
        except planlib.PlanError as e:
            self._log("sync-error", str(e))
            return []

    def sync_and_advance(self, feature_id, roles, fetch_state=None):
        """Bring the checkout and idle worktrees up to the merged base, then
        dispatch the next feature. Returns action descriptions; an empty list
        means it was deferred and will be retried on a later poll. Decide and
        act happen under one cross-process lock (daemon vs `swarm plan sync`)."""
        try:
            with planlib.plan_lock(self.plan_dir, "plan-flow.lock", stale=FLOW_LOCK_STALE_S):
                return self._sync_locked(feature_id, roles, fetch_state)
        except planlib.PlanError as e:
            self._log("sync-error", str(e))
            return []

    def _sync_locked(self, feature_id, roles, fetch_state):
        plan = planlib.load(self.plan_dir)
        feature = planlib.feature_by_id(plan, feature_id)
        if (feature is None or feature.get("status") != "merged" or feature.get("synced_at")
                or planlib.in_progress_features(plan)):
            return []  # another process got here first, or the state moved on
        base, err = self._targets(plan)
        if err:
            self._refuse(roles, err)
            return []
        has_origin = self._has_origin()
        if has_origin:
            if fetch_state is None:
                fetched = self._g("fetch", "origin", timeout=self.t_fetch)
                fetch_state, fetch_err = fetched.rc == 0, fetched.err
            else:
                fetch_err = "see the previous log line"
            if not fetch_state:
                self._defer(roles, feature, f"git fetch failed: {fetch_err}")
                return []
        target = f"origin/{base}" if has_origin else base
        head = self._resolve(target)
        if not head:
            self._defer(roles, feature, f"{target} does not resolve")
            return []

        warnings = []
        skipped = self._fast_forward_master(base, target, has_origin)
        if skipped:
            warnings.append(skipped)

        # Merged by hand with no PR, merge commit or detection behind it: we do
        # not know what the human merged, so the worktrees are NOT reset.
        manual = not (feature.get("merge_commit") or feature.get("pr_url") or feature.get("merge_source"))
        if manual:
            self._log("sync-manual", feature_id,
                      "marked merged by hand without a PR or merge commit: master fast-forward "
                      "attempted, worktrees left untouched, next feature not auto-dispatched")
            planlib.update_feature(self.plan_dir, feature_id, synced_at=now_iso())
            return [self._advance_manual(feature_id, roles, warnings)]

        final = feature.get("final_commit") or ""
        plans, deferred = [], []
        for role, info in roles.items():
            name, path = info.get("worktree-name", ""), info.get("worktree-path", "")
            if not path or name in NO_WORKTREE or _same_path(path, self.root) or not Path(path).is_dir():
                continue
            why = self._worktree_blocker(role, name, path, head, final)
            if why:
                deferred.append(f"{role} ({path}): {why}")
            else:
                plans.append((role, name, path))
        if deferred:
            self._defer(roles, feature, "; ".join(deferred))
            return []
        failed = []
        for role, name, path in plans:  # every worktree was checked first: all-or-nothing intent
            moved = self._g("checkout", "-q", "-B", f"swarmforge-{name}", head, cwd=path)
            if moved.rc != 0:
                failed.append(f"{role} ({path}): checkout failed ({moved.err})")
        if failed:
            self._defer(roles, feature, "; ".join(failed))
            return []

        self._last_deferral = None
        self._deferrals.pop(feature_id, None)
        self._defer_until = 0.0
        actions = [f"synced worktrees to {head[:10]}"]
        actions.append(self._advance(feature_id, roles, head, warnings))
        planlib.update_feature(self.plan_dir, feature_id, synced_at=now_iso())
        return actions

    def _worktree_blocker(self, role, name, path, head, final):
        """Why this worktree must not be reset now ('' = safe). May create a
        backup ref for local commits the reset would orphan."""
        wt = Path(path)
        if pending_work(wt):
            return "has pending inbox work"
        outbox = wt / ".swarmforge" / "handoffs" / "outbox"
        if outbox.is_dir() and any(p.is_file() for p in outbox.rglob("*")):
            return "has unsent outbox handoffs"
        for state_path in GUARDED_STATE_PATHS:
            r = self._g("rev-parse", "--git-path", state_path, cwd=path)
            if r.rc != 0:
                return f"git state could not be inspected ({r.err})"
            p = Path(r.out)
            if not p.is_absolute():
                p = wt / p
            if p.exists():
                return f"has an unfinished git operation ({state_path})"
        head_ref = self._g("symbolic-ref", "-q", "HEAD", cwd=path)
        if head_ref.rc != 0:
            return "HEAD is detached"
        if head_ref.out != f"refs/heads/swarmforge-{name}":
            return f"is on '{head_ref.out}', not swarmforge-{name}"
        status = self._g("status", "--porcelain", "--ignored", cwd=path)
        if status.rc != 0:
            return f"git status failed ({status.err})"
        for line in status.out.splitlines():
            if not line.startswith("!! "):
                return "has uncommitted or untracked changes"
        for line in status.out.splitlines():
            ignored = line[3:].strip().strip('"')
            tracked = self._g("ls-tree", "-r", "--name-only", head, "--", ignored, cwd=path)
            if tracked.rc != 0 or tracked.out:
                return f"ignored path '{ignored}' collides with files in the new base"
        old = self._g("rev-parse", "--verify", "--quiet", "HEAD", cwd=path)
        if old.rc == 0 and old.out:
            tip = old.out
            if not self._is_ancestor(tip, head, cwd=path) and not (
                    final and self._is_ancestor(tip, final, cwd=path)):
                stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                ref = f"refs/swarmforge/backup/{role}/{stamp}"
                made = self._g("update-ref", ref, tip, cwd=path)
                if made.rc != 0:
                    return f"has local commits that cannot be backed up ({made.err})"
                self._log("backup-ref", role, ref, tip)
        return ""

    def _defer(self, roles, feature, why):
        fid = feature["id"]
        count = self._deferrals[fid] = self._deferrals.get(fid, 0) + 1
        self._defer_until = self.clock() + min(BACKOFF_CAP_S, max(self.poll_interval, 1) * (2 ** (count - 1)))
        message = f"sync of '{fid}' deferred: {why}"
        if message != self._last_deferral:  # log changes only, not every poll
            self._last_deferral = message
            self._log("sync-deferred", fid, why)
        if count >= DEFER_NOTE_AFTER and not feature.get("defer_noted_at"):
            try:
                self._send_note(
                    roles, f"Sync of '{fid}' keeps being deferred."[:80],
                    f"Feature '{fid}' was merged but the worktrees could not be synced after {count} "
                    f"attempts. What blocks it: {why}. Tell the user to finish or discard that work "
                    f"(commit, stash or abort the git operation, empty the outbox); SwarmForge retries "
                    f"on its own, or the user runs `swarm plan sync`.")
                planlib.update_feature(self.plan_dir, fid, defer_noted_at=now_iso())
            except Exception as e:  # noqa: BLE001
                self._log("notify-error", repr(e))

    def _refuse(self, roles, err):
        self._log("refused", err)
        self._note_once(
            roles, f"refused:{err}", "SwarmForge refused to deliver the plan."[:80],
            f"SwarmForge will not push, open PRs or sync worktrees for this plan: {err}. "
            f"Tell the user; nothing was changed.")

    def _fast_forward_master(self, base, target, has_origin):
        """Returns a reason string when the master checkout was NOT updated."""
        current = self._g("rev-parse", "--abbrev-ref", "HEAD")
        if current.rc != 0 or current.out != base:
            return f"the master checkout is on '{current.out or '?'}', not '{base}'; it was not updated"
        dirty = self._g("status", "--porcelain", "--untracked-files=no")
        if dirty.rc != 0 or dirty.out:
            return "the master checkout has uncommitted changes; it was not updated"
        if not has_origin:
            return ""
        merged = self._g("merge", "--ff-only", "-q", target)
        if merged.rc != 0:
            return f"the master checkout could not be fast-forwarded ({merged.err}); update it by hand"
        return ""

    # -- 4. dispatch ---------------------------------------------------------------

    def _advance_manual(self, merged_id, roles, warnings):
        plan = planlib.load(self.plan_dir)
        extra = (" Warnings: " + "; ".join(warnings) + ".") if warnings else ""
        feats = planlib._features(plan)
        if feats and all(f.get("status") == "merged" for f in feats):
            if plan.get("status") != "done":
                planlib.set_plan_status(self.plan_dir, "done")
            self._send_note(roles, "Plan complete: every feature is merged.",
                            f"Feature '{merged_id}' was marked merged by hand and it was the last one. "
                            f"Tell the user the plan is complete.{extra}")
            return "plan complete"
        self._send_note(roles, f"Feature {merged_id} marked merged by hand."[:80],
                        f"Feature '{merged_id}' was marked merged by hand, so SwarmForge did not reset "
                        f"the role worktrees and did not dispatch the next feature. Tell the user to run "
                        f"`swarm plan set-status {merged_id} merged` only after real PR merges "
                        f"(the swarm detects those itself), and hand off the next ready feature "
                        f"yourself once the worktrees are on the new base.{extra}")
        return f"'{merged_id}' marked merged by hand; worktrees untouched, next feature not dispatched"

    def _advance(self, merged_id, roles, head, warnings):
        plan = planlib.load(self.plan_dir)
        extra = (" Warnings: " + "; ".join(warnings) + ".") if warnings else ""
        feats = planlib._features(plan)
        if feats and all(f.get("status") == "merged" for f in feats):
            if plan.get("status") != "done":
                planlib.set_plan_status(self.plan_dir, "done")
            self._send_note(roles, "Plan complete: every feature is merged.",
                            f"Feature '{merged_id}' was merged and it was the last one. "
                            f"Tell the user the plan is complete.{extra}")
            return "plan complete"
        if planlib.in_progress_features(plan):
            return f"'{merged_id}' merged; a feature is already in progress"
        if plan.get("auto_next", True) is False:
            self._send_note(roles, f"Feature {merged_id} merged; auto_next is off."[:80],
                            f"Feature '{merged_id}' was merged. Automatic dispatch is disabled "
                            f"(auto_next=false): hand off the next ready feature yourself.{extra}")
            return f"'{merged_id}' merged; auto_next is off"
        nxt = planlib.next_ready_feature(plan)
        if nxt is None:
            self._send_note(roles, f"Feature {merged_id} merged; no feature is ready."[:80],
                            f"Feature '{merged_id}' was merged but no pending feature has all its "
                            f"dependencies merged. Check `swarm plan status`.{extra}")
            return f"'{merged_id}' merged; no feature ready"
        task = nxt["id"]
        # Same gate as a hand-written specifier -> coder handoff (approval, hash,
        # dependencies, one-in-progress), then the same `coding` reservation.
        errors, snapshot = planlib.reserve_feature(self.plan_dir, task, head)
        if errors:
            self._send_note(roles, f"Feature {merged_id} merged; {task} not dispatched."[:80],
                            "The next feature was rejected by the plan gate:\n- "
                            + "\n- ".join(errors) + extra)
            return f"'{merged_id}' merged; dispatch of '{task}' rejected"
        try:
            short = self._g("rev-parse", "--short=10", head).out or head[:10]
            self._write_outbox(
                roles, "specifier", sender="specifier", to="coder", type_="git_handoff",
                headers=[("role", "specifier"), ("task", task), ("commit", short)],
                body=f"Re-read your role and constitution.\n\nmerge_and_process specifier {short}\n\n"
                     f"Dispatched automatically by SwarmForge: feature '{merged_id}' was merged.")
        except Exception:
            planlib.restore_feature(self.plan_dir, task, snapshot)
            raise
        try:
            self._send_note(roles, f"Feature {merged_id} merged; dispatched {task}."[:80],
                            f"Feature '{merged_id}' was merged and '{task}' was handed to the "
                            f"coder automatically. Nothing for you to do.{extra}")
        except Exception as e:  # noqa: BLE001 - the dispatch itself succeeded
            self._log("notify-error", repr(e))
        return f"'{merged_id}' merged; dispatched '{task}'"

    # -- outbox --------------------------------------------------------------------

    def _note_once(self, roles, key, message, body):
        """Send a specifier note at most once per process for `key`."""
        if key in self._noted:
            return
        self._noted.add(key)
        try:
            self._send_note(roles, message, body)
        except Exception as e:  # noqa: BLE001
            self._log("notify-error", repr(e))

    def _send_note(self, roles, message, body):
        """Note for the specifier (`from: swarmforge`). It is queued in the
        architect's outbox when there is one: the daemon attributes every handoff
        leaving a role's outbox to that role and calls scheduler.on_sent for it,
        which only marks the ARCHITECT as "forwarded work" (sent_since_launch,
        empty task). That must not make the specifier (the role that talks to
        the human) parkable, which is what queueing it in its own outbox would do."""
        via = "architect" if "architect" in roles else "specifier"
        self._write_outbox(
            roles, via, sender="swarmforge", to="specifier", type_="note",
            headers=[("message", message[:80])],
            body=f"Re-read your role and constitution.\n\n{body}")

    def _write_outbox(self, roles, via, sender, to, type_, headers, body):
        info = roles.get(via)
        if not info or not info.get("worktree-path"):
            raise planlib.PlanError(f"Role '{via}' has no worktree to queue a handoff in.")
        outbox = Path(info["worktree-path"]) / ".swarmforge" / "handoffs" / "outbox"
        tmp_dir = outbox / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_id_ts()
        seq = f"pf{uuid.uuid4().hex[:6]}"
        name = f"50_{stamp}_{seq}_from_{sender}_to_{to}.handoff"
        lines = [f"id: {stamp}_{seq}_from_{sender}", f"from: {sender}", f"to: {to}",
                 "priority: 50", f"type: {type_}"]
        lines += [f"{k}: {v}" for k, v in headers]
        lines += [f"created_at: {now_iso()}", "", body]
        tmp = tmp_dir / f"{name}.tmp"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        final = outbox / name
        tmp.rename(final)
        return final
