# Swarm TUI — Attach Diagnostics (F2.1)

## Goal and context

The F2 navigation polish fixed the startup focus and added a non-transient error
banner for unexpected tmux session termination. Users still occasionally see
agent sessions disconnect with no visible error, or the captured reason is not
enough to diagnose the root cause. F2.1 adds lightweight, append-only
diagnostics so the user (and future maintainers) can see exactly what happened
around each attach/detach.

## Decisions settled with the user

- **Both a log file and a dashboard banner**: write events to a persistent log
  file, and keep the most recent attach error visible on the dashboard until it
  is explicitly dismissed or a new attach succeeds.
- **Log location**: `.swarmforge/logs/tui.log` next to other SwarmForge state.
  The file is created automatically if it does not exist.
- **Log format**: one line per event, RFC3339 UTC timestamp, event name, and
  key/value pairs. No structured JSON, no external logging library.
- **Events to log**:
  - `tui_start` — TUI process starts.
  - `attach_start` — user pressed Enter to attach; includes role, session name,
    and socket path.
  - `attach_end` — tmux attach process exited; includes role, exit code, and
    reason/stderr text (empty string for clean detach).
  - `socket_check` — result of the poll-time socket availability check.
  - `session_check` — result of an explicit session-existence check before or
    after attach.
- **Banner behavior**: the dashboard shows the last `attach_end` reason in red
  until the user presses `Esc` or a new `attach_start` occurs. A clean detach
  clears the banner.
- **Privacy / size**: the log only records events and errors, not tmux pane
  content or keystrokes. No automatic rotation in F2.1; the file grows until the
  user truncates it.

## Scope

- Add an event logger to the TUI that appends to `.swarmforge/logs/tui.log`.
- Emit the events listed above at the appropriate points in the attach lifecycle
  and poll loop.
- Make the dashboard error banner dismissible with `Esc` and clear it on a
  successful attach start.
- Update unit tests to assert log entries are written and the banner can be
  dismissed.

## Edge cases and error handling

- Log directory or file cannot be created → log a single warning to stderr on
  startup and continue without logging; the TUI must not crash.
- Disk full or append fails → silently skip subsequent log writes; do not
  surface repeated I/O errors in the UI.
- Empty attach reason → still log `attach_end` with an empty reason field so
  the user can distinguish "clean detach" from "no error captured".
- Concurrent TUI instances → all instances append to the same file; lines are
  not interleaved mid-line because each write is a single line.

## Non-functional constraints

- Zero runtime dependencies; use Node's built-in `fs` module.
- Tests with the built-in `node:test` runner.
- Do not capture or log tmux pane content, handoff payloads, or user keystrokes.
- Keep log writes synchronous and small so they do not block the render loop.

## Non-goals

- Log rotation, compression, or remote log shipping.
- Query/filter UI for logs.
- Logging agent-internal output (that belongs in the agent's own tmux session).
- Metrics or performance counters.

## Open questions

- None.
