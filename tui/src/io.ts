import { spawn, type ChildProcess } from "node:child_process";
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

let stdinDataListener: ((data: Buffer) => void) | null = null;
let stdinWasRaw = false;

export function setStdinDataListener(listener: (data: Buffer) => void): void {
  stdinDataListener = listener;
  process.stdin.on("data", listener);
}

function releaseTty(): void {
  stdinWasRaw = !!process.stdin.isRaw;
  try {
    if (process.stdin.isTTY) {
      if (stdinWasRaw) process.stdin.setRawMode(false);
      process.stdin.pause();
      if (stdinDataListener) {
        process.stdin.removeListener("data", stdinDataListener);
      }
    }
    process.stdout.write("\x1b[?25h");
    process.stdout.write("\x1b[0m");
  } catch (err) {
    log.warn({ event: "tty_release_failed", err: errMessage(err) }, "tty release failed");
  }
}

function reclaimTty(): void {
  try {
    if (process.stdin.isTTY) {
      if (stdinWasRaw) process.stdin.setRawMode(true);
      if (stdinDataListener) process.stdin.on("data", stdinDataListener);
      process.stdin.resume();
      process.stdin.setEncoding("utf8");
    }
    process.stdout.write("\x1b[?5l");
    process.stdout.write("\x1b[2J\x1b[H");
  } catch (err) {
    log.warn({ event: "tty_reclaim_failed", err: errMessage(err) }, "tty reclaim failed");
  }
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

export function readSocketPath(root: string): string {
  const file = path.join(root, ".swarmforge", "tmux-socket");
  if (!existsSync(file)) return "";
  try {
    return readFileSync(file, "utf8").trim();
  } catch (err) {
    log.warn({ event: "socket_read_failed", file, err }, "cannot read tmux socket file");
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

  socketPath(): string {
    return readSocketPath(this.root);
  }

  socketAvailable(): boolean {
    const socket = this.socketPath();
    if (socket === "") return false;
    try {
      return existsSync(socket);
    } catch (err) {
      log.warn({ event: "socket_stat_failed", socket, err }, "cannot stat tmux socket");
      return false;
    }
  }

  terminalSize(): TerminalSize {
    return { cols: process.stdout.columns || 0, rows: process.stdout.rows || 0 };
  }

  attach(session: string, socket: string): Promise<AttachResult> {
    return new Promise((resolve) => {
      releaseTty();
      log.info(
        { event: "tty_released", session, socket, raw: stdinWasRaw },
        "tty released for tmux attach",
      );

      let proc: ChildProcess;
      try {
        proc = spawn("tmux", ["-S", socket, "attach", "-t", session], {
          stdio: "inherit",
        });
      } catch (err) {
        log.error(
          { event: "attach_spawn_failed", session, socket, err },
          "cannot spawn tmux attach",
        );
        reclaimTty();
        resolve({ code: -1, reason: errMessage(err) });
        return;
      }
      proc.on("error", (err) => {
        log.error(
          { event: "attach_runtime_error", session, socket, err },
          "tmux attach runtime error",
        );
      });
      proc.on("exit", (code) => {
        log.info({ event: "tmux_attach_exit", code }, "tmux attach exited");
        log.info({ event: "tty_reclaim_start" }, "reclaiming tty from tmux");
        reclaimTty();
        resolve({ code, reason: "" });
      });
    });
  }

  sessionExists(session: string): Promise<boolean> {
    const socket = this.socketPath();
    if (socket === "") return Promise.resolve(false);
    return new Promise((resolve) => {
      let proc: ChildProcess;
      try {
        proc = spawn("tmux", ["-S", socket, "has-session", "-t", session], {
          stdio: ["ignore", "ignore", "ignore"],
        });
      } catch (err) {
        log.warn(
          { event: "session_exists_spawn_failed", session, socket, err: errMessage(err) },
          "cannot spawn tmux has-session",
        );
        resolve(false);
        return;
      }
      proc.on("error", (err) => {
        log.warn(
          { event: "session_exists_error", session, socket, err: errMessage(err) },
          "tmux has-session error",
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
    process.exit(0);
  }
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}