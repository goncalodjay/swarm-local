# Migration Spec: Babashka → Python

> Status: draft. Reviewed by maintainer before implementation.
> Scope: replace every `*.bb` script under `swarmforge/scripts/` with an
> equivalent Python implementation. Keep the same external surface (CLI args,
> exit codes, on-disk file formats, env vars) so the rest of the project does
> not need to change.

## 1. Why migrate

The Babashka scripts work but the dialect is a real obstacle for anyone who
doesn't already read Clojure: paren soup, threading macros, `defn` / `let` /
`recur` chains. The business logic is not complex — it is fs walks, git
subprocesses, tmux `send-keys`, header parsing — all of which Python expresses
linearly with `pathlib` and `subprocess`. Keeping Bash for the parts that are
genuinely shell-shaped (terminal-adapter sourcing, AppleScript bridges) and
moving the rest to Python is the right cut.

## 2. Inventory of scripts to migrate

All paths are relative to `swarmforge/scripts/`.

| File                       | Lines | Role                                                |
| -------------------------- | ----: | --------------------------------------------------- |
| `swarmforge.bb`            |   611 | Main CLI orchestrator (`./swarm`, `./swarm tui`)    |
| `swarm_handoff.bb`         |   338 | Validate a draft, write `.handoff` to outbox        |
| `handoffd.bb`              |   201 | Long-running delivery daemon                        |
| `handoff_lib.bb`           |   190 | Library of helpers (parses, roles, sequence)        |
| `ready_for_next.bb`        |    66 | Dispatcher: pick `task` or `batch` variant          |
| `ready_for_next_task.bb`   |   120 | Move `new/` → `in_process/` and print task          |
| `ready_for_next_batch.bb`  |   148 | Group `new/` by priority into a `batch_*` dir       |
| `done_with_current.bb`     |    66 | Dispatcher: pick `task` or `batch` variant          |
| `done_with_current_task.bb`|    95 | Stamp `completed_at`, move to `completed/`          |
| `done_with_current_batch.bb`|  106 | Same, for batch dirs                                |
| `swarm-window-watchdog.bb` |   119 | Reopen terminal windows when tmux dies              |
| `stop_handoff_daemon.bb`   |    45 | Graceful TERM → KILL of the handoff daemon          |

**Heavy duplication** exists between the four `ready_*` / `done_*` files
(inbox helpers, header parser, set-header, print-task, print-batch,
new-batch-dir). Babashka forced the copy-paste because each `.bb` is its own
process; Python modules fix this for free.

## 3. Scripts that STAY in Bash

These are genuine shell code, not wrappers. Migrating them would be a
downgrade:

- `swarm-cleanup.sh` — runs at agent exit, sources the terminal adapter,
  kills sessions, closes terminal windows. The Bash here is correct.
- `swarm-terminal-adapter.sh` — sources the per-backend zsh adapter.
- `terminal-adapters/*.sh` — `osascript` / `wt.exe` bridges; do not touch.
- The `*.sh` wrappers that today are `exec bb <same>.bb "$@"`. Rewrite each
  one to `exec python3 ... <new>.py "$@"`. Same external shape.

## 4. Target layout

A single Python package at `swarmforge/scripts/swarm_python/`. One
executable per current `.bb`, **with duplication-bearing trios merged into
single entrypoints**. Shared code lives in modules, not copy-pasted in each
script.

The `ready_for_next_*` trio (`.bb` + `_task.bb` + `_batch.bb`) collapses into
one `ready_for_next.py` that branches internally. Same for the
`done_with_current_*` trio. Net: **7 entrypoints instead of 11**. Every
external `.sh` wrapper keeps the same name and only re-points its `exec`.

```
swarmforge/scripts/
├── swarm_python/                  # the package
│   ├── __init__.py
│   ├── ansi.py                    # color consts (was red/green/yellow/cyan/bold/reset)
│   ├── env.py                     # shlex.quote wrap, env-long, sq helper
│   ├── paths.py                   # context() — build all .swarmforge paths
│   ├── git_ops.py                 # init repo, worktrees, info/exclude, copy scripts to worktree
│   ├── tmux_ops.py                # base-index detection, new-session, send-keys, kill
│   ├── terminal.py                # backend detection + open-session / close / exists calls
│   ├── sleep_inhibit.py           # caffeinate / systemd-inhibit wrapper
│   ├── deps.py                    # check-dependency!, command-exists?
│   ├── config.py                  # parse swarmforge.conf → list of role rows
│   ├── tsv.py                     # read/write the .tsv state files (sessions, roles, windows)
│   ├── handoff/
│   │   ├── __init__.py
│   │   ├── format.py              # header-field, body, set-header!, parse-draft
│   │   ├── inbox.py               # inbox/new|in_process|completed helpers, handoff-files, batch-dirs
│   │   ├── roles.py               # roles.tsv reader, role-known?, role-row
│   │   ├── project.py             # project-root resolution (git-root / git-common-dir)
│   │   ├── sequence.py            # next-sequence with fcntl advisory lock
│   │   ├── timefmt.py             # ISO_INSTANT + id-timestamp
│   │   └── validate.py             # validate-recipients, canonical-commit, validate, error-report
│   └── entrypoints/               # 7 scripts, one per external wrapper
│       ├── swarm_cli.py           # === swarmforge.bb
│       ├── swarm_handoff.py       # === swarm_handoff.bb
│       ├── handoffd.py            # === handoffd.bb
│       ├── ready_for_next.py      # merges ready_for_next{,_task,_batch}.bb
│       ├── done_with_current.py   # merges done_with_current{,_task,_batch}.bb
│       ├── window_watchdog.py     # === swarm-window-watchdog.bb
│       └── stop_handoff_daemon.py # === stop_handoff_daemon.bb
├── swarm                          # bash: exec python3 swarm_python/entrypoints/swarm_cli.py "$@"
├── swarm_handoff.sh               # rewritten to point at .py
├── ready_for_next.sh              # rewritten
├── done_with_current.sh           # rewritten
├── swarm-window-watchdog.sh       # rewritten
├── stop_handoff_daemon.sh         # rewritten
├── swarm-cleanup.sh               # unchanged
├── swarm-terminal-adapter.sh      # unchanged
└── terminal-adapters/             # unchanged
```

### Why a package per current script

Two reasons. First, **external surface stability**: the `.sh` wrappers,
`swarm-init`, the launch commands inside `swarm_cli.py` (which `exec` other
scripts), and the agent prompts all call into these scripts by name. Keeping
one executable per script means zero churn outside `swarmforge/scripts/`.
Second, **process boundary**: `handoffd.py` is a long-lived daemon that
others `exec` into; `ready_for_next.py` re-execs the next task; the
window watchdog is its own process. Collapsing them into a single
multi-command binary would force changes to all of those call sites.

If we want a single binary later we can add a thin `swarm` CLI on top of the
package, but **not as part of this migration** — keep blast radius small.

## 5. Module responsibilities

### `ansi.py`
Six named color constants and one `RESET`. Used by `swarm_cli.py` and
`handoffd.py` for the boot banner and error lines.

### `env.py`
- `shell_quote(value)` — thin wrapper over `shlex.quote()`. This replaces
  the `sq` helper that escapes single quotes for `bash -c` (we keep escaping
  for parity; `shlex.quote` is correct for the `zsh -c` usage in
  `adapter-script`).
- `env_long(name, default)` — read env var, parse as `int`, fallback.

### `paths.py`
`build_context(working_dir) -> Context` returns a frozen dataclass with every
path the orchestrator uses (`config_file`, `roles_dir`, `state_dir`,
`tmux_socket_dir`, `tmux_socket`, `daemon_dir`, `handoff_daemon_log`, etc.).
The tmux socket name is computed by `zlib.crc32(working_dir.encode())` — same
algorithm as `swarmforge.bb:473`.

### `git_ops.py`
- `init_repo_if_missing(ctx)`
- `ensure_runtime_git_excludes(ctx)`
- `prepare_worktrees(ctx, roles)`
- `sync_worktree_scripts(ctx, roles)` — copies the helpers into each
  non-master worktree, plus `sessions.tsv`, `roles.tsv`, `tmux-socket`,
  `tmux-env`. Identical semantics to `sync-worktree-scripts!` at
  `swarmforge.bb:270`.

### `tmux_ops.py`
- `detect_base_indexes(ctx)` — creates a probe session if none, reads
  `base-index` and `pane-base-index` with `tmux show-options -gqv`, kills
  the probe, returns a `(window_base, pane_base)` tuple.
- `create_role_session(ctx, session, title)`
- `send_keys(ctx, target, command)`
- `has_session(ctx, session)` → bool
- `kill_session(ctx, session)`
- `attach(ctx, session)` — only used by the `none` backend fallback.
- `build_launch_command(ctx, row, index)` — the per-agent command string
  for `claude`, `codex`, `copilot`, `grok`, `opencode`, `pi`. Same table as
  `launch-command` at `swarmforge.bb:326`. The first-row cleanup suffix is
  appended here too.

### `terminal.py`
- `normalize_backend(name)` — same case-map as
  `swarmforge.bb:44`.
- `detect_backend()` — env first, then `osascript`+`TERM_PROGRAM`,
  then `wt.exe`, then `"none"`.
- `open_surfaces(ctx, roles)` — drives `terminal_open_session`,
  `terminal_backend_label`, `terminal_backend_tracks_windows`. Mirrors
  `open-terminal-surfaces!` at `swarmforge.bb:432`.

### `sleep_inhibit.py`
`prefix()` returns a list (possibly empty) to prepend to the daemon command.
Same Darwin `caffeinate -dims` / Linux `systemd-inhibit --what=sleep:idle`
logic. The probe-before-use behavior at `swarmforge.bb:396` is preserved:
run `systemd-inhibit ... true` once to confirm logind accepts the wrapper.

### `deps.py`
- `command_exists(name)` — `shutil.which` wrapped to return `bool` (mirrors
  `sh-ok? sh -c "command -v ..."`).
- `require(command)` — exits with the red `Error: '<cmd>' is required...`
  message if missing.
- `check_backends(roles)` — iterates configured agent names.

### `config.py`
- `parse(ctx)` — reads `swarmforge.conf`, returns a list of
  `RoleRow(role, agent, session, display_name, worktree_name,
  worktree_path, receive_mode, extra_args)`. Every validation rule at
  `swarmforge.bb:129` is preserved with the same error message (the messages
  are read by agents, do not reword).
- `write_sessions_tsv(ctx, roles)`, `write_roles_tsv(ctx, roles)` — byte
  format identical to `write-sessions-file!` / `write-roles-file!`.

### `tsv.py`
Tiny utility to read/write TSV with `\n` line endings. We keep the TSV
format exactly — the `roles.tsv` file is also parsed by `handoffd.bb`,
`handoff_lib.bb`, `ready_for_next*.bb`, `done_with_current*.bb`. Changing
the on-disk format would break every consumer at once.

### `handoff/format.py`
- `parse_draft(path) -> {headers, ordered, errors}` — same state machine as
  `swarm_handoff.bb:92`. The empty-line-as-body-separator and
  reserved/allowed field sets stay verbatim.
- `header_field(path, field)`, `body(path)`, `header_value(...)` — used by
  the inbox scripts.
- `set_header(path, field, value)` — atomic temp-file write + rename. Same
  "insert at first blank line or replace existing" logic.
- `print_task(path)`, `print_batch(batch_dir)` — stdout format unchanged.
  The agents parse these.

### `handoff/inbox.py`
- `handoff_files(dir)` — sorted list of `*.handoff` regular files.
- `batch_dirs(dir)` — sorted list of dirs whose name starts with `batch_`.
- `new_batch_dir(in_process_dir)` — find the next `batch_<id_ts>_<n>`.

### `handoff/roles.py`
- `project_root()` — same three-fallback logic as `handoff_lib.bb:18`
  (cwd → git-root → git-common-dir parent).
- `role_rows()` → list of lists (TSV rows).
- `role_known(name)`, `role_row(name)`, `role_worktree_name(name)`,
  `role_receive_mode(name)`.
- `roles_file_path()`.

### `handoff/project.py`
- `state_dir()` — `cwd/.swarmforge/handoffs`. Note: in the bb code some
  scripts use `cwd/.swarmforge/handoffs` and others use `cwd/.swarmforge`.
  The Python version makes the distinction explicit per call site (see §7).

### `handoff/sequence.py`
`next_sequence()` uses `fcntl.flock(LOCK_EX)` on a sidecar `sequence.lock`
file. The bb version used `fs/create-dir` on a lock dir; both rely on POSIX
advisory locking semantics. We choose `flock` because it is simpler and
auto-released on process death.

### `handoff/timefmt.py`
- `now_iso()` → ISO_INSTANT, microsecond-free.
- `now_id_ts()` → `yyyyMMdd'T'HHmmss'Z'` in UTC. Same as bb.

### `handoff/validate.py`
- `validate_recipients(to)` → `(recipients, errors)`. Same per-recipient
  rules.
- `canonical_commit(commit)` → `(short_sha, error)`. Runs
  `git rev-parse --disambiguate=<c>` and `git cat-file -t`, returns the
  10-char short SHA only if the object is a commit. Same as
  `swarm_handoff.bb:157`.
- `validate(headers, ordered)` → the full error list. The error messages are
  the contract; keep them byte-identical.
- `error_report(draft, errors)` — emits the `HANDOFF INVALID:` block plus
  the `usage-text` to stderr.

## 6. Entry points (7 total)

Each file is a thin `if __name__ == "__main__": ...` that imports from the
package and runs the corresponding `-main` function. They are invoked by the
rewritten `.sh` wrappers and by the orchestrator's `subprocess.Popen` /
`subprocess.run` calls.

| New entrypoint                | Old .bb                            | What it does                              |
| ----------------------------- | ---------------------------------- | ----------------------------------------- |
| `entrypoints/swarm_cli.py`    | `swarmforge.bb`                    | Orchestrator: parse config, init git, prepare worktrees, create tmux sessions, launch agents, start daemon, open terminals. Preserves `--test-*` flags and the `tui` subcommand. |
| `entrypoints/swarm_handoff.py`| `swarm_handoff.bb`                 | Validate a draft and write the outbox file. |
| `entrypoints/handoffd.py`     | `handoffd.bb`                      | Poll every 1s, deliver handoffs, notify via tmux. PID file, stop file, shutdown hook. |
| `entrypoints/ready_for_next.py` | `ready_for_next.bb` + `_task.bb` + `_batch.bb` | Branches on the role's `receive_mode` and runs the single-task or batch-grouping flow. |
| `entrypoints/done_with_current.py`   | `done_with_current.bb` + `_task.bb` + `_batch.bb` | Same shape: branches on `receive_mode`. |
| `entrypoints/window_watchdog.py` | `swarm-window-watchdog.bb`         | Reopen or kill all on terminal loss. |
| `entrypoints/stop_handoff_daemon.py` | `stop_handoff_daemon.bb`       | Graceful TERM → KILL of daemon. |

## 7. Behavioral contract — what MUST NOT change

The migration is byte-for-byte on the outside, refactored on the inside.

1. **Exit codes.** Every script returns the same codes as today. The agents
   and the orchestrator branch on `2` (AMBIGUOUS_TASK_STATE) and `1`
   (NO_CURRENT_TASK / NO_CURRENT_BATCH / unknown role, etc.). Keep these.
2. **Stdout/stderr text.** The `TASK:` / `FROM:` / `BATCH:` / `COMPLETED:`
   / `NO_TASK` / `HANDOFF QUEUED:` / `HANDOFF INVALID:` blocks are parsed
   by both humans and the LLM agents. Do not change wording, ordering, or
   blank lines.
3. **TSV formats.** `roles.tsv`, `sessions.tsv`, `windows.tsv` must remain
   tab-separated with `\n` line endings and the same column order.
4. **Handoff file format.** `.handoff` files keep the
   `key: value\n\nbody` shape. The reserved fields and allowed fields are
   the same sets.
5. **Env vars consumed.** `SWARMFORGE_ROLE`, `SWARMFORGE_TERMINAL`,
   `SWARMFORGE_TERMINAL_BACKEND`, `SWARMFORGE_PREVENT_SLEEP`,
   `SWARMFORGE_AGENT_START_DELAY_MS`. Same names, same semantics.
6. **Tmux socket naming.** CRC32 of the working-dir path → same
   `/tmp/swarmforge-<uid>/<crc>.sock` pattern.
7. **CLI surface of `./swarm`.** `swarm_cli.py` keeps the
   `--test-parse`, `--test-terminal-bridge`, `--test-launch-command`,
   `--test-agent-start-delay`, `--test-sleep-inhibitor-prefix`,
   `--test-tmux-base-indexes`, and the `tui` subcommand.

## 8. Dependencies

**Stdlib only.** Specifically:

- `pathlib`, `shutil`, `os`, `sys`, `subprocess`
- `argparse` (only for the `--test-*` flags in `swarm_cli.py`; not really
  needed since they are positional checks, but it documents the surface)
- `datetime`, `time`
- `fcntl` (sequence lock)
- `shlex` (shell quoting)
- `zlib` (CRC32 for tmux socket)
- `signal`, `atexit` (daemon shutdown)

Rationale: every script boots in tens of milliseconds; adding a dep
(`click`, `psutil`, `watchdog`) costs us install friction in
`swarm-init` for no real win. The handoff daemon polls at 1s so we don't
need inotify. If something genuinely needs a dep later, add it then.

## 9. Migration order

Smallest independent unit first, so each step is verifiable in isolation.

1. **Foundation modules** — `ansi`, `env`, `paths`, `tsv`, `handoff/timefmt`,
   `handoff/project`, `handoff/roles`, `handoff/format`,
   `handoff/inbox`, `handoff/sequence`, `handoff/validate`. No scripts
   move yet; we just have the library in place.
2. **`stop_handoff_daemon.py`** — simplest daemon-adjacent script, 45
   lines. Smoke test: kill a fake daemon process and confirm TERM, then
   KILL fallback.
3. **`handoffd.py`** — the daemon itself. Replace `swarm_handoff.sh`
   callsite in `swarm_cli.py` once it is migrated.
4. **`ready_for_next.py` + `ready_for_next_task.py` +
   `ready_for_next_batch.py`** — small, no daemon coupling. Easiest to
   test against fixtures.
5. **`done_with_current_*.py`** — same shape as the ready trio.
6. **`swarm_handoff.py`** — pure logic, no subprocess orchestration.
   Reuses `handoff/format` + `handoff/validate` + `handoff/sequence`.
7. **`swarm_cli.py`** — biggest piece. Migrate after the helpers it
   delegates to are stable. The `--test-*` flags double as our
   verification harness.
8. **`window_watchdog.py`** — last; it depends on `terminal.py` and
   `tmux_ops.py` which only `swarm_cli.py` exercised.

Each step ends with: (a) delete the corresponding `.bb`, (b) rewrite the
`.sh` wrapper to point at the new entrypoint, (c) run the relevant
`--test-*` flag (or a hand-rolled fixture), (d) confirm exit codes and
stdout match.

## 10. Acceptance criteria

The migration is "done" when, on a freshly initialized project:

- `./swarm` brings the swarm up exactly as before. All four agent tmux
  sessions are created with the same names, the handoff daemon is running
  with the same PID-file location, the terminal windows open (or the
  `none` fallback attaches).
- `./swarm tui` runs the Node TUI unchanged.
- A draft file created by an agent, validated by
  `./swarm_handoff.sh`, produces a byte-identical `.handoff` in `outbox/`.
- `ready_for_next.sh` in `task` mode moves a single `*.handoff` from
  `inbox/new/` to `inbox/in_process/` and prints the same `TASK:` block.
- `ready_for_next.sh` in `batch` mode groups by priority and creates the
  same `batch_<ts>_<n>/` directory layout.
- `done_with_current.sh` in both modes stamps `completed_at` and moves
  files to `completed/` with the same exit codes.
- Stopping the daemon via `stop_handoff_daemon.sh` produces the same
  TERM-then-KILL behavior.
- `./swarm --test-parse`, `--test-launch-command coder opencode`,
  `--test-tmux-base-indexes`, `--test-agent-start-delay`,
  `--test-sleep-inhibitor-prefix`, and `--test-terminal-bridge` all
  produce the same output as today.

## 11. Out of scope

- The TypeScript TUI under `tui/` is not touched.
- The Gherkin features under `features/` are TUI-only; no `.feature`
  describes the `.bb` scripts today, so we add no new features either.
- The Bash files listed in §3 are not migrated.
- The `swarm-init` flow under `template/swarm/` is not migrated.
