import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { parseRoles } from "./roles.ts";
import { readHandoffSnapshot } from "./handoffs.ts";
import { appendLogEntry, logPathForRoot } from "./log.ts";
import { child } from "./logger.ts";
import { queryHerdrAgents as queryHerdrAgentsImpl } from "./herdr.ts";
import type { TuiIO } from "./app.ts";
import type { AttachResult, HerdrAgent, HandoffSnapshot, Role, TerminalSize } from "./types.ts";

const log = child("io");

/**
 * Hands the terminal to and from a foreground child process.
 *
 * Attaching to a herdr workspace means giving the terminal away completely:
 * the child inherits stdio and drives the screen until it exits. Whoever
 * owns the terminal (the OpenTUI renderer in the running TUI, plain stdio
 * in tests) supplies this pair so `attach` does not need to know which.
 */
export interface TerminalControl {
  /** Give the terminal to the child process. */
  release(): void;
  /** Take the terminal back after the child exits. */
  reclaim(): void;
}

/**
 * Fallback control used when nothing else is installed. It only restores
 * sane modes, which is all a non-rendering caller needs.
 */
const bareTerminalControl: TerminalControl = {
  release(): void {
    try {
      if (process.stdin.isTTY && process.stdin.isRaw) process.stdin.setRawMode(false);
      process.stdout.write("\x1b[?25h");
      process.stdout.write("\x1b[0m");
    } catch (err) {
      log.warn({ event: "tty_release_failed", err: errMessage(err) }, "tty release failed");
    }
  },
  reclaim(): void {
    try {
      process.stdin.resume();
    } catch (err) {
      log.warn({ event: "tty_reclaim_failed", err: errMessage(err) }, "tty reclaim failed");
    }
  },
};

let terminalControl: TerminalControl = bareTerminalControl;

/**
 * How the TUI leaves. The renderer must be torn down before the process
 * exits, otherwise the terminal is left in raw mode on the alternate
 * screen, so the owner installs its own handler.
 */
let exitHandler: (code: number) => void = (code) => process.exit(code);

/** Install the shutdown path used by `quit`. */
export function setExitHandler(handler: (code: number) => void): void {
  exitHandler = handler;
}

/** Install the terminal owner's release/reclaim pair. */
export function setTerminalControl(control: TerminalControl): void {
  terminalControl = control;
}

function releaseTty(): void {
  terminalControl.release();
}

function reclaimTty(): void {
  terminalControl.reclaim();
}

export function projectRoot(cwd: string): string {
  let dir = path.resolve(cwd);
  for (;;) {
    if (existsSync(path.join(dir, ".swarmforge", "roles.tsv"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) throw new Error("SwarmForge project root not found");
    dir = parent;
  }
}

export function readHerdrSession(root: string): string {
  const file = path.join(root, ".swarmforge", "herdr-session");
  if (!existsSync(file)) return "";
  try {
    return readFileSync(file, "utf8").trim();
  } catch (err) {
    log.warn({ event: "herdr_session_read_failed", file, err }, "cannot read herdr-session file");
    return "";
  }
}

export function readRolesText(root: string): string {
  const file = path.join(root, ".swarmforge", "roles.tsv");
  try {
    return readFileSync(file, "utf8");
  } catch (err) {
    log.error({ event: "roles_read_failed", file, err }, "cannot read roles.tsv");
    throw err;
  }
}

export function readSnapshotForRole(role: Role): {
  snapshot: HandoffSnapshot;
  error: string | null;
} {
  const root = path.join(role.worktreePath, ".swarmforge", "handoffs");
  try {
    return { snapshot: readHandoffSnapshot(root), error: null };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    log.warn(
      { event: "snapshot_read_failed", role: role.role, root, err: message },
      "cannot read handoff snapshot",
    );
    return {
      snapshot: { queued: [], inProcess: [], completed: [], pendingUserNote: false },
      error: message,
    };
  }
}

export class FileSystemTuiIO implements TuiIO {
  root: string;

  constructor(root: string) {
    this.root = root;
  }

  readRoles(): Role[] {
    try {
      return parseRoles(readRolesText(this.root));
    } catch (err) {
      log.error({ event: "roles_parse_failed", err }, "cannot parse roles.tsv");
      return [];
    }
  }

  readSnapshot(role: Role): HandoffSnapshot {
    return readSnapshotForRole(role).snapshot;
  }

  /** Returns the herdr session name (from .swarmforge/herdr-session), not a filesystem path. */
  socketPath(): string {
    return readHerdrSession(this.root);
  }

  socketAvailable(): boolean {
    const herdrSession = this.socketPath();
    if (herdrSession === "") return false;
    try {
      const result = spawnSync("herdr", ["--session", herdrSession, "workspace", "list"], {
        stdio: ["ignore", "ignore", "ignore"],
      });
      return result.status === 0;
    } catch (err) {
      log.warn({ event: "herdr_reachability_check_failed", herdrSession, err }, "cannot reach herdr session");
      return false;
    }
  }

  terminalSize(): TerminalSize {
    return { cols: process.stdout.columns || 0, rows: process.stdout.rows || 0 };
  }

  attach(workspaceId: string, herdrSession: string): Promise<AttachResult> {
    return new Promise((resolve) => {
      releaseTty();
      log.info(
        { event: "tty_released", workspaceId, herdrSession },
        "tty released for herdr attach",
      );

      try {
        spawnSync("herdr", ["--session", herdrSession, "workspace", "focus", workspaceId], {
          stdio: ["ignore", "ignore", "ignore"],
        });
      } catch (err) {
        log.warn(
          { event: "workspace_focus_failed", workspaceId, herdrSession, err },
          "cannot focus herdr workspace before attach",
        );
      }

      let proc: ChildProcess;
      try {
        proc = spawn("herdr", ["--session", herdrSession], {
          stdio: "inherit",
        });
      } catch (err) {
        log.error(
          { event: "attach_spawn_failed", workspaceId, herdrSession, err },
          "cannot spawn herdr attach",
        );
        reclaimTty();
        resolve({ code: -1, reason: errMessage(err) });
        return;
      }
      proc.on("error", (err) => {
        log.error(
          { event: "attach_runtime_error", workspaceId, herdrSession, err },
          "herdr attach runtime error",
        );
      });
      proc.on("exit", (code) => {
        log.info({ event: "herdr_attach_exit", code }, "herdr attach exited");
        log.info({ event: "tty_reclaim_start" }, "reclaiming tty from herdr");
        reclaimTty();
        resolve({ code, reason: "" });
      });
    });
  }

  sessionExists(workspaceId: string): Promise<boolean> {
    const herdrSession = this.socketPath();
    if (herdrSession === "") return Promise.resolve(false);
    return new Promise((resolve) => {
      let proc: ChildProcess;
      try {
        proc = spawn("herdr", ["--session", herdrSession, "workspace", "get", workspaceId], {
          stdio: ["ignore", "ignore", "ignore"],
        });
      } catch (err) {
        log.warn(
          { event: "session_exists_spawn_failed", workspaceId, herdrSession, err: errMessage(err) },
          "cannot spawn herdr workspace get",
        );
        resolve(false);
        return;
      }
      proc.on("error", (err) => {
        log.warn(
          { event: "session_exists_error", workspaceId, herdrSession, err: errMessage(err) },
          "herdr workspace get error",
        );
        resolve(false);
      });
      proc.on("exit", (code) => resolve(code === 0));
    });
  }

  queryHerdrAgents(): Promise<HerdrAgent[]> {
    return queryHerdrAgentsImpl();
  }

  log(event: string, fields: Record<string, string | number | boolean | null>): void {
    appendLogEntry(logPathForRoot(this.root), event, fields);
  }

  restore(): void {
    try {
      process.stdout.write("\x1b[?25h");
      process.stdout.write("\x1b[0m");
      if (process.stdin.isTTY && process.stdin.isRaw) process.stdin.setRawMode(false);
    } catch (err) {
      log.warn({ event: "restore_failed", err: errMessage(err) }, "cannot restore terminal");
    }
  }

  quit(): void {
    exitHandler(0);
  }
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}