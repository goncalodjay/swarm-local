import { test } from "node:test";
import assert from "node:assert/strict";
import { App, type TuiIO } from "../src/app.ts";
import { menuItemIndex, menuItems } from "../src/menu.ts";
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

test("checkSocket returns to the dashboard when the socket recovers", () => {
  const io = new FakeIO();
  io.socketOk = false;
  const app = new App(io);
  app.start();
  io.socketOk = true;
  app.checkSocket();
  assert.equal(app.view, "dashboard");
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
  app.focus = "agents";
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

test("ctrl+k enters prefix mode and esc cancels it", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  await app.press("ctrl+k");
  assert.equal(app.mode, "prefix");
  await app.press("esc");
  assert.equal(app.mode, "normal");
});

test("unmapped key cancels prefix mode silently", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  await app.press("ctrl+k");
  await app.press("j");
  assert.equal(app.mode, "normal");
  assert.equal(app.selection, 0);
});

test("tab in prefix mode cycles focus through the panels", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  assert.equal(app.focus, "menu");
  await app.press("ctrl+k");
  await app.press("tab");
  assert.equal(app.focus, "agents");
  await app.press("ctrl+k");
  await app.press("tab");
  assert.equal(app.focus, "detail");
  await app.press("ctrl+k");
  await app.press("tab");
  assert.equal(app.focus, "menu");
});

test("vim motions move selection regardless of focus", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.focus = "menu";
  await app.press("j");
  assert.equal(app.selection, 1);
  await app.press("k");
  assert.equal(app.selection, 0);
});

test("home and end jump to the first and last agent", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  await app.press("end");
  assert.equal(app.selection, 3);
  await app.press("g");
  assert.equal(app.selection, 0);
  await app.press("G");
  assert.equal(app.selection, 3);
  await app.press("home");
  assert.equal(app.selection, 0);
});

test("left/right navigate the menu and wrap", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.focus = "menu";
  await app.press("right");
  assert.equal(app.menuFocus, 1);
  await app.press("left");
  assert.equal(app.menuFocus, 0);
  await app.press("left");
  assert.equal(app.menuFocus, menuItems(app.roles).length - 1);
});

test("left/right are ignored outside the menu focus", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.focus = "agents";
  await app.press("right");
  assert.equal(app.menuFocus, 0);
});

test("enter on a disabled menu item shows the not-implemented hint", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.focus = "menu";
  app.menuFocus = menuItemIndex(menuItems(app.roles), "logs");
  await app.press("enter");
  assert.equal(app.hint, "logs: not implemented");
});

test("enter on a role menu item moves focus to agents and selects it", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.focus = "menu";
  app.menuFocus = menuItemIndex(menuItems(app.roles), "coder");
  await app.press("enter");
  assert.equal(app.focus, "agents");
  assert.equal(app.selection, 1);
});

test("the footer hint is transient and clears on the next key", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.focus = "menu";
  app.menuFocus = menuItemIndex(menuItems(app.roles), "costs");
  await app.press("enter");
  assert.equal(app.hint, "costs: not implemented");
  await app.press("down");
  assert.equal(app.hint, null);
});

test("ctrl+k then ? opens the help overlay and q closes it", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  await app.press("ctrl+k");
  await app.press("?");
  assert.equal(app.helpOpen, true);
  await app.press("quit");
  assert.equal(app.helpOpen, false);
});

test("a key that is not a help key leaves the overlay open", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  await app.press("ctrl+k");
  await app.press("?");
  await app.press("j");
  assert.equal(app.helpOpen, true);
  assert.equal(app.selection, 0);
});
