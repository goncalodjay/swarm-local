import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { parseRoles } from "./roles.ts";
import { readHandoffSnapshot } from "./handoffs.ts";
import type { TuiIO } from "./app.ts";
import type { HandoffSnapshot, Role, TerminalSize } from "./types.ts";

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
  return readFileSync(file, "utf8").trim();
}

export class FileSystemTuiIO implements TuiIO {
  root: string;

  constructor(root: string) {
    this.root = root;
  }

  readRoles(): Role[] {
    return parseRoles(readFileSync(path.join(this.root, ".swarmforge", "roles.tsv"), "utf8"));
  }

  readSnapshot(role: Role): HandoffSnapshot {
    return readHandoffSnapshot(path.join(role.worktreePath, ".swarmforge", "handoffs"));
  }

  socketPath(): string {
    return readSocketPath(this.root);
  }

  socketAvailable(): boolean {
    const socket = this.socketPath();
    if (socket === "") return false;
    return existsSync(socket);
  }

  terminalSize(): TerminalSize {
    return { cols: process.stdout.columns || 0, rows: process.stdout.rows || 0 };
  }

  attach(session: string, socket: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const child: ChildProcess = spawn("tmux", ["-S", socket, "attach", "-t", session], {
        stdio: "inherit",
      });
      child.on("error", reject);
      child.on("exit", () => resolve());
    });
  }

  restore(): void {
    process.stdout.write("\x1b[?25h");
    process.stdout.write("\x1b[0m");
    if (process.stdin.isTTY && process.stdin.isRaw) process.stdin.setRawMode(false);
  }

  quit(): void {
    process.exit(0);
  }
}
