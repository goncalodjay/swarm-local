import { test } from "node:test";
import assert from "node:assert/strict";
import { App, type TuiIO } from "../src/app.ts";
import type { HandoffSnapshot, Role, TerminalSize } from "../src/types.ts";

function makeRole(role: string, index: number): Role {
  return {
    role,
    worktreeName: role,
    worktreePath: `/p/.worktrees/${role}`,
    session: `swarmforge-${role}`,
    displayName: role[0].toUpperCase() + role.slice(1),
    agent: "opencode",
    receiveMode: "task",
  };
}

const ROLES: Role[] = ["specifier", "coder", "refactorer", "architect"].map(makeRole);

function emptySnapshot(): HandoffSnapshot {
  return { queued: [], inProcess: [], completed: [], pendingUserNote: false };
}

class FakeIO implements TuiIO {
  roles: Role[] = ROLES;
  snapshots = new Map<string, HandoffSnapshot>();
  socket = "/tmp/swarmforge/test.sock";
  socketOk = true;
  size: TerminalSize = { cols: 120, rows: 40 };
  attaches: Array<{ session: string; socket: string }> = [];
  restored = false;
  quitCalled = false;
  private attachResolvers: Array<() => void> = [];

  readRoles(): Role[] {
    return this.roles;
  }

  readSnapshot(role: Role): HandoffSnapshot {
    return this.snapshots.get(role.role) ?? emptySnapshot();
  }

  socketPath(): string {
    return this.socket;
  }

  socketAvailable(): boolean {
    return this.socketOk;
  }

  terminalSize(): TerminalSize {
    return this.size;
  }

  attach(session: string, socket: string): Promise<void> {
    this.attaches.push({ session, socket });
    return new Promise((resolve) => {
      this.attachResolvers.push(resolve);
    });
  }

  detach(): void {
    for (const resolve of this.attachResolvers) resolve();
    this.attachResolvers = [];
  }

  restore(): void {
    this.restored = true;
  }

  quit(): void {
    this.quitCalled = true;
  }
}

test("start lists configured roles in order", () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  assert.deepEqual(app.roles.map((r) => r.role), ["specifier", "coder", "refactorer", "architect"]);
});

test("start with no socket shows error view", () => {
  const io = new FakeIO();
  io.socketOk = false;
  const app = new App(io);
  app.start();
  assert.equal(app.view, "error");
});

test("start with small terminal shows too-small view", () => {
  const io = new FakeIO();
  io.size = { cols: 80, rows: 24 };
  const app = new App(io);
  app.start();
  assert.equal(app.view, "too-small");
});

test("down/up move selection and clamp at bounds", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  await app.press("down");
  assert.equal(app.selection, 1);
  await app.press("down");
  await app.press("down");
  assert.equal(app.selection, 3);
  await app.press("down");
  assert.equal(app.selection, 3);
  await app.press("up");
  assert.equal(app.selection, 2);
  await app.press("up");
  await app.press("up");
  await app.press("up");
  assert.equal(app.selection, 0);
});

test("enter attaches to the selected agent session on the socket", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.selection = 1;
  const attaching = app.press("enter");
  assert.equal(app.view, "attached");
  io.detach();
  await attaching;
  assert.deepEqual(io.attaches, [{ session: "swarmforge-coder", socket: "/tmp/swarmforge/test.sock" }]);
  assert.equal(app.view, "dashboard");
});

test("poll refreshes a newly landed in-process handoff", () => {
  const io = new FakeIO();
  io.snapshots.set("coder", {
    queued: [],
    inProcess: [{ task: "fix-login", type: "git_handoff", created_at: "x", dequeued_at: "y", completed_at: null }],
    completed: [],
    pendingUserNote: false,
  });
  const app = new App(io);
  app.start();
  assert.equal(app.agents[1].status, "working");
  assert.equal(app.agents[1].task, "fix-login");
});

test("poll detects lost socket while running", () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  assert.equal(app.view, "dashboard");
  io.socketOk = false;
  app.poll();
  assert.equal(app.view, "error");
});

test("poll recovers to dashboard when size becomes valid", () => {
  const io = new FakeIO();
  io.size = { cols: 80, rows: 24 };
  const app = new App(io);
  app.start();
  assert.equal(app.view, "too-small");
  io.size = { cols: 120, rows: 40 };
  app.poll();
  assert.equal(app.view, "dashboard");
});

test("quit restores the terminal and quits", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  await app.press("quit");
  assert.equal(io.restored, true);
  assert.equal(io.quitCalled, true);
});
