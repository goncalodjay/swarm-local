import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync, mkdtempSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { App, type TuiIO } from "../tui/src/app.ts";
import { parseRoles } from "../tui/src/roles.ts";
import { readHandoffSnapshot } from "../tui/src/handoffs.ts";
import { appendLogEntry, logPathForRoot } from "../tui/src/log.ts";
import { frameModel, helpLines } from "../tui/src/render.ts";
import { createTestRenderer } from "@opentui/core/testing";
import { FrameMount } from "../tui/src/ui/dashboard.ts";
import { selectTheme } from "../tui/src/theme.ts";
import type { AttachResult, HandoffSnapshot, LogFields, Role, TerminalSize } from "../tui/src/types.ts";
import { installTuiBundle, type InstallOutcome } from "../tui/src/install.ts";
import { launchInstalledTui } from "../tui/test/helpers/launch-installed-tui.ts";

const ROLES = [
  { role: "specifier", worktreeName: "master", session: "swarmforge-specifier", displayName: "Specifier", agent: "opencode", mode: "task" },
  { role: "coder", worktreeName: "coder", session: "swarmforge-coder", displayName: "Coder", agent: "opencode", mode: "task" },
  { role: "reviewer", worktreeName: "reviewer", session: "swarmforge-reviewer", displayName: "Reviewer", agent: "codex", mode: "task" },
  { role: "architect", worktreeName: "architect", session: "swarmforge-architect", displayName: "Architect", agent: "opencode", mode: "batch" },
] as const;

export interface World {
  root: string;
  socket: string;
  socketOk: boolean;
  sessionAlive: boolean;
  size: TerminalSize;
  autoDetach: boolean;
  attachCalls: Array<{ session: string; socket: string }>;
  restoreCalls: number;
  quitCalls: number;
  app: App;
  io: TestIO;
  frame: string[];
  rendered: string;
  helpOverlay: string;
  pendingAttach: Promise<AttachResult> | null;
  resolveAttach: (result: AttachResult) => void;
  detach: () => void;
  endSession: (message: string) => void;
  projectDir: string;
  swarmLocalDir: string;
  installOutcome: InstallOutcome | null;
  launchExitCode: number | null;
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

  attach(session: string, socket: string): Promise<AttachResult> {
    this.world.attachCalls.push({ session, socket });
    if (this.world.autoDetach) {
      return Promise.resolve({ code: 0, reason: "" });
    }
    const pending = new Promise<AttachResult>((resolve) => {
      this.world.resolveAttach = (result: AttachResult): void => {
        resolve(result);
      };
      this.world.detach = (): void => {
        resolve({ code: 0, reason: "" });
      };
      this.world.endSession = (message: string): void => {
        resolve({ code: 1, reason: message });
      };
    });
    this.world.pendingAttach = pending;
    return pending;
  }

  sessionExists(session: string): Promise<boolean> {
    return Promise.resolve(this.world.sessionAlive);
  }

  log(event: string, fields: LogFields): void {
    appendLogEntry(logPathForRoot(this.world.root), event, fields);
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

function roleDefinition(role: string) {
  const definition = ROLES.find((candidate) => candidate.role === role);
  if (!definition) throw new Error(`Unknown role ${role}`);
  return definition;
}

function worktreePath(root: string, worktreeName: string): string {
  return worktreeName === "master" ? root : roleWorktree(root, worktreeName);
}

function rolesTsv(root: string): string {
  return ROLES.map((r) => {
    const rolePath = worktreePath(root, r.worktreeName);
    return [r.role, r.worktreeName, rolePath, r.session, r.displayName, r.agent, r.mode].join("\t");
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
    const worktree = worktreePath(root, r.worktreeName);
    ensureInbox(worktree);
  }
  const socket = path.join(root, ".swarmforge", "swarm.sock");
  const world: World = {
    root,
    socket,
    socketOk: false,
    sessionAlive: true,
    size: { cols: 120, rows: 40 },
    autoDetach: true,
    attachCalls: [],
    restoreCalls: 0,
    quitCalls: 0,
    app: undefined as unknown as App,
    io: undefined as unknown as TestIO,
    frame: [],
    rendered: "",
    helpOverlay: "",
    pendingAttach: null,
    resolveAttach: (): void => {},
    detach: (): void => {},
    endSession: (): void => {},
    projectDir: mkdtempSync(path.join(os.tmpdir(), "swarm-init-project-")),
    swarmLocalDir: mkdtempSync(path.join(os.tmpdir(), "swarm-init-local-")),
    installOutcome: null,
    launchExitCode: null,
  };
  world.io = new TestIO(world);
  world.app = new App(world.io);
  return world;
}

export function cleanupWorld(world: World): void {
  rmSync(world.root, { recursive: true, force: true });
  rmSync(world.projectDir, { recursive: true, force: true });
  rmSync(world.swarmLocalDir, { recursive: true, force: true });
}

export function startSwarm(world: World): void {
  mkdirSync(path.dirname(world.socket), { recursive: true });
  writeFileSync(path.join(world.root, ".swarmforge", "tmux-socket"), world.socket + "\n");
  writeFileSync(world.socket, "");
  world.socketOk = true;
}

function handoffPath(world: World, role: string, sub: string, name: string): string {
  const worktree = worktreePath(world.root, roleDefinition(role).worktreeName);
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

/**
 * Render the current application state through the real OpenTUI pipeline
 * and store the resulting frame on the world.
 *
 * Acceptance assertions run against the same renderer the shipped binary
 * uses, so a layout regression fails here rather than in a terminal.
 */
export async function captureFrame(world: World): Promise<void> {
  const { renderer, renderOnce, captureCharFrame } = await createTestRenderer({
    width: world.size.cols,
    height: world.size.rows,
  });
  const mount = new FrameMount(renderer, selectTheme({}));
  try {
    mount.update(frameModel(world.app));
    await renderOnce();
    world.rendered = captureCharFrame();
    world.frame = world.rendered.split("\n");
  } finally {
    mount.destroy();
    renderer.destroy();
  }
  world.helpOverlay = world.app.helpOpen ? helpLines(world.app.focus).join("\n") : "";
}

export interface ParsedRow {
  selected: boolean;
  marker: string;
  role: string;
  task: string | null;
}

/**
 * Parse an agent row out of a rendered frame line.
 *
 * A frame line is a series of panel cells separated by vertical borders,
 * for example `" | >   specifier      | | Task: ..."`. Each cell carries
 * one column of padding, so the row content starts at the second
 * character of its cell.
 */
export function parseAgentRow(line: string): ParsedRow | null {
  for (const cell of line.split("│")) {
    const match = /^ ([ >]) (.) (\S+)( .*)?$/.exec(cell.trimEnd());
    if (!match) continue;
    return {
      selected: match[1] === ">",
      marker: match[2]!,
      role: match[3]!,
      task: match[4] ? match[4].trim() : null,
    };
  }
  return null;
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

export function readLog(world: World): string {
  const file = logPathForRoot(world.root);
  return existsSync(file) ? readFileSync(file, "utf8") : "";
}

export function logLines(world: World): string[] {
  return readLog(world).split("\n").filter((line) => line !== "");
}

export function swarmLocalBundlePath(world: World): string {
  return path.join(world.swarmLocalDir, "tui", "dist", "swarm-tui");
}

export function installedBundlePath(world: World): string {
  return path.join(world.projectDir, ".swarmforge", "tui", "swarm-tui");
}

export function writeSwarmLocalBundle(world: World, content: string): void {
  const bundle = swarmLocalBundlePath(world);
  mkdirSync(path.dirname(bundle), { recursive: true });
  writeFileSync(bundle, content);
}

export function removeSwarmLocalBundle(world: World): void {
  rmSync(swarmLocalBundlePath(world), { force: true });
}

export function runSwarmInitInstall(world: World): InstallOutcome {
  world.installOutcome = installTuiBundle(swarmLocalBundlePath(world), world.projectDir);
  return world.installOutcome;
}

export function createInstalledBundle(world: World, content: string): void {
  const target = installedBundlePath(world);
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, content);
}

export function launchMarkerPath(world: World): string {
  return path.join(world.projectDir, "tui-executed.marker");
}

export function markerWritingBundle(world: World): string {
  return `const fs = require("node:fs");\nfs.writeFileSync(${JSON.stringify(launchMarkerPath(world))}, "executed");\n`;
}

export async function runInstalledTui(world: World): Promise<number> {
  world.launchExitCode = await launchInstalledTui(world.projectDir);
  return world.launchExitCode;
}
