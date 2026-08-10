import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync, mkdtempSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { App, type TuiIO } from "../tui/src/app.ts";
import { parseRoles } from "../tui/src/roles.ts";
import { readHandoffSnapshot } from "../tui/src/handoffs.ts";
import { renderFrame, type FrameModel } from "../tui/src/render.ts";
import type { HandoffSnapshot, Role, TerminalSize } from "../tui/src/types.ts";

const ROLES = [
  { role: "specifier", worktreeName: "master", session: "swarmforge-specifier", displayName: "Specifier", agent: "opencode", mode: "task" },
  { role: "coder", worktreeName: "coder", session: "swarmforge-coder", displayName: "Coder", agent: "opencode", mode: "task" },
  { role: "refactorer", worktreeName: "refactorer", session: "swarmforge-refactorer", displayName: "Refactorer", agent: "codex", mode: "task" },
  { role: "architect", worktreeName: "architect", session: "swarmforge-architect", displayName: "Architect", agent: "opencode", mode: "batch" },
] as const;

export interface World {
  root: string;
  socket: string;
  socketOk: boolean;
  size: TerminalSize;
  autoDetach: boolean;
  attachCalls: Array<{ session: string; socket: string }>;
  restoreCalls: number;
  quitCalls: number;
  app: App;
  io: TestIO;
  frame: string[];
  rendered: string;
  pendingAttach: Promise<void> | null;
  detach: () => void;
}

export class TestIO implements TuiIO {
  world: World;

  constructor(world: World) {
    this.world = world;
  }

  readRoles(): Role[] {
    return parseRoles(readFileSync(path.join(this.world.root, ".swarmforge", "roles.tsv"), "utf8"));
  }

  readSnapshot(role: Role): HandoffSnapshot {
    return readHandoffSnapshot(path.join(role.worktreePath, ".swarmforge", "handoffs"));
  }

  socketPath(): string {
    return this.world.socket;
  }

  socketAvailable(): boolean {
    return this.world.socketOk && existsSync(this.world.socket);
  }

  terminalSize(): TerminalSize {
    return this.world.size;
  }

  attach(session: string, socket: string): Promise<void> {
    this.world.attachCalls.push({ session, socket });
    if (this.world.autoDetach) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      this.world.detach = (): void => {
        this.world.pendingAttach = null;
        resolve();
      };
      this.world.pendingAttach = Promise.resolve();
    });
  }

  restore(): void {
    this.world.restoreCalls++;
  }

  quit(): void {
    this.world.quitCalls++;
  }
}

function roleWorktree(root: string, worktreeName: string): string {
  return path.join(root, ".worktrees", worktreeName);
}

function rolesTsv(root: string): string {
  return ROLES.map((r) => {
    const worktreePath = r.worktreeName === "master" ? root : roleWorktree(root, r.worktreeName);
    return [r.role, r.worktreeName, worktreePath, r.session, r.displayName, r.agent, r.mode].join("\t");
  }).join("\n") + "\n";
}

function ensureInbox(worktreePath: string): void {
  for (const sub of ["new", "in_process", "completed"]) {
    mkdirSync(path.join(worktreePath, ".swarmforge", "handoffs", "inbox", sub), { recursive: true });
  }
}

export function createWorld(): World {
  const root = mkdtempSync(path.join(os.tmpdir(), "swarm-tui-acceptance-"));
  mkdirSync(path.join(root, ".swarmforge"), { recursive: true });
  writeFileSync(path.join(root, ".swarmforge", "roles.tsv"), rolesTsv(root));
  for (const r of ROLES) {
    const worktree = r.worktreeName === "master" ? root : roleWorktree(root, r.worktreeName);
    ensureInbox(worktree);
  }
  const socket = path.join(root, ".swarmforge", "swarm.sock");
  const world: World = {
    root,
    socket,
    socketOk: false,
    size: { cols: 120, rows: 40 },
    autoDetach: true,
    attachCalls: [],
    restoreCalls: 0,
    quitCalls: 0,
    app: undefined as unknown as App,
    io: undefined as unknown as TestIO,
    frame: [],
    rendered: "",
    pendingAttach: null,
    detach: (): void => {},
  };
  world.io = new TestIO(world);
  world.app = new App(world.io);
  return world;
}

export function cleanupWorld(world: World): void {
  rmSync(world.root, { recursive: true, force: true });
}

export function startSwarm(world: World): void {
  mkdirSync(path.dirname(world.socket), { recursive: true });
  writeFileSync(path.join(world.root, ".swarmforge", "tmux-socket"), world.socket + "\n");
  writeFileSync(world.socket, "");
  world.socketOk = true;
}

function agentIndex(world: World, role: string): number {
  const index = world.app.agents.findIndex((a) => a.role === role);
  if (index === -1) throw new Error(`No agent row for role: ${role}`);
  return index;
}

function handoffPath(world: World, role: string, sub: string, name: string): string {
  const row = ROLES.find((r) => r.role === role);
  if (!row) throw new Error(`Unknown role ${role}`);
  const worktree = row.worktreeName === "master" ? world.root : roleWorktree(world.root, row.worktreeName);
  return path.join(worktree, ".swarmforge", "handoffs", "inbox", sub, name);
}

export function writeInProcessHandoff(world: World, role: string, task: string): void {
  const file = handoffPath(world, role, "in_process", `${task}.handoff`);
  writeFileSync(file, `id: test_${role}\nfrom: specifier\nto: ${role}\npriority: 50\ntype: git_handoff\ntask: ${task}\ncreated_at: 2026-08-10T22:41:31Z\ndequeued_at: 2026-08-10T22:41:35Z\n\npayload\n`);
}

export function writeCompletedHandoff(world: World, role: string, task: string): void {
  const file = handoffPath(world, role, "completed", `${task}.handoff`);
  writeFileSync(file, `id: done_${role}\nfrom: specifier\nto: ${role}\npriority: 50\ntype: git_handoff\ntask: ${task}\ncreated_at: 2026-08-10T22:40:00Z\ndequeued_at: 2026-08-10T22:40:10Z\ncompleted_at: 2026-08-10T22:41:00Z\n\npayload\n`);
}

export function writeUserNote(world: World, role: string): void {
  const file = handoffPath(world, role, "new", "note.handoff");
  writeFileSync(file, `id: note_${role}\nfrom: ${role}\nto: user\npriority: 50\ntype: note\nmessage: human help requested\ncreated_at: 2026-08-10T22:41:00Z\n\nRe-read your role and constitution.\n`);
}

export function captureFrame(world: World): void {
  const model: FrameModel = {
    view: world.app.view === "attached" ? "dashboard" : world.app.view,
    roles: world.app.roles,
    agents: world.app.agents,
    selection: world.app.selection,
    errorMessage: world.app.errorMessage,
    terminalSize: world.io.terminalSize(),
    requiredSize: { cols: 100, rows: 30 },
  };
  world.frame = renderFrame(model);
  world.rendered = world.frame.join("\n");
}

export interface ParsedRow {
  selected: boolean;
  marker: string;
  role: string;
  task: string | null;
}

export function parseAgentRow(line: string): ParsedRow | null {
  const match = /^([ >]) (.) (\S+)( .*)?$/.exec(line);
  if (!match) return null;
  return {
    selected: match[1] === ">",
    marker: match[2],
    role: match[3],
    task: match[4] ? match[4].trim() : null,
  };
}

export function findAgentRow(world: World, role: string): ParsedRow {
  for (const line of world.frame) {
    const row = parseAgentRow(line);
    if (row && row.role === role) return row;
  }
  throw new Error(`Agent row not found in frame for role: ${role}`);
}

export function markerForName(name: string): string {
  switch (name) {
    case "spinner":
      return "◐";
    case "dot":
      return "●";
    case "!":
      return "!";
    default:
      return " ";
  }
}
