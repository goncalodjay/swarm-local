import { test } from "node:test";
import assert from "node:assert/strict";
import { App, type TuiIO } from "../src/app.ts";
import { menuItemIndex, menuItems } from "../src/menu.ts";
import type { AttachResult, HandoffSnapshot, LogFields, Role, TerminalSize } from "../src/types.ts";

function makeRole(role: string, index: number): Role {
  return {
    role,
    worktreeName: role,
    worktreePath: `/p/.worktrees/${role}`,
    session: `swarmforge-${role}`,
    workspaceId: `w${index + 1}`,
    displayName: role[0].toUpperCase() + role.slice(1),
    agent: "opencode",
    receiveMode: "task",
  };
}

const ROLES: Role[] = ["specifier", "coder", "reviewer", "architect"].map(makeRole);

function emptySnapshot(): HandoffSnapshot {
  return { queued: [], inProcess: [], completed: [], pendingUserNote: false };
}

class FakeIO implements TuiIO {
  roles: Role[] = ROLES;
  snapshots = new Map<string, HandoffSnapshot>();
  socket = "/tmp/swarmforge/test.sock";
  socketOk = true;
  sessionAlive = true;
  size: TerminalSize = { cols: 120, rows: 40 };
  attaches: Array<{ session: string; socket: string }> = [];
  restored = false;
  quitCalled = false;
  logCalls: Array<{ event: string; fields: LogFields }> = [];
  private attachResolvers: Array<(result: AttachResult) => void> = [];

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

  log(event: string, fields: LogFields): void {
    this.logCalls.push({ event, fields });
  }

  attach(session: string, socket: string): Promise<AttachResult> {
    this.attaches.push({ session, socket });
    return new Promise((resolve) => {
      this.attachResolvers.push(resolve);
    });
  }

  sessionExists(_session: string): Promise<boolean> {
    return Promise.resolve(this.sessionAlive);
  }

  detach(): void {
    for (const resolve of this.attachResolvers) resolve({ code: 0, reason: "" });
    this.attachResolvers = [];
  }

  endSession(message: string): void {
    for (const resolve of this.attachResolvers) resolve({ code: 1, reason: message });
    this.attachResolvers = [];
  }

  endGeneric(code: number): void {
    for (const resolve of this.attachResolvers) resolve({ code, reason: "" });
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
  assert.deepEqual(app.roles.map((r) => r.role), ["specifier", "coder", "reviewer", "architect"]);
});

test("startup focus is on the agents panel", () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  assert.equal(app.focus, "agents");
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
  assert.deepEqual(io.attaches, [{ session: "w2", socket: "/tmp/swarmforge/test.sock" }]);
  assert.equal(app.view, "dashboard");
});

test("an unexpected session end returns to the dashboard with a non-transient error", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.selection = 1;
  app.focus = "agents";
  const attaching = app.press("enter");
  assert.equal(app.view, "attached");
  io.endSession("server disconnected unexpectedly");
  await attaching;
  assert.equal(app.view, "dashboard");
  assert.equal(app.attachError, "server disconnected unexpectedly");
});

test("a clean detach clears a previous attach error", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.selection = 1;
  app.focus = "agents";
  let attaching = app.press("enter");
  io.endSession("server disconnected unexpectedly");
  await attaching;
  assert.equal(app.attachError, "server disconnected unexpectedly");
  attaching = app.press("enter");
  io.detach();
  await attaching;
  assert.equal(app.attachError, null);
});

test("start logs a tui_start event", () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  assert.ok(io.logCalls.some((c) => c.event === "tui_start"));
});

test("attach logs attach_start and attach_end events", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  io.logCalls = [];
  app.selection = 1;
  app.focus = "agents";
  const attaching = app.press("enter");
  io.endSession("server disconnected unexpectedly");
  await attaching;
  const attachStart = io.logCalls.find((c) => c.event === "attach_start");
  const attachEnd = io.logCalls.find((c) => c.event === "attach_end");
  assert.ok(attachStart, "missing attach_start");
  assert.equal(attachStart.fields.workspaceId, "w2");
  assert.equal(attachStart.fields.socket, "/tmp/swarmforge/test.sock");
  assert.ok(attachEnd, "missing attach_end");
  assert.equal(attachEnd.fields.reason, "server disconnected unexpectedly");
  assert.equal(attachEnd.fields.code, 1);
});

test("attach_end with a clean detach logs an empty reason", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  io.logCalls = [];
  app.selection = 1;
  app.focus = "agents";
  const attaching = app.press("enter");
  io.detach();
  await attaching;
  const attachEnd = io.logCalls.find((c) => c.event === "attach_end");
  assert.ok(attachEnd, "missing attach_end");
  assert.equal(attachEnd.fields.reason, "");
});

test("generic non-zero exit with missing session logs the session root cause", async () => {
  const io = new FakeIO();
  io.sessionAlive = false;
  const app = new App(io);
  app.start();
  io.logCalls = [];
  app.selection = 1;
  app.focus = "agents";
  const attaching = app.press("enter");
  io.endGeneric(1);
  await attaching;
  const attachEnd = io.logCalls.find((c) => c.event === "attach_end");
  assert.ok(attachEnd, "missing attach_end");
  assert.equal(attachEnd.fields.reason, "herdr workspace w2 no longer exists");
  assert.equal(attachEnd.fields.socket_available, true);
  assert.equal(attachEnd.fields.session_alive, false);
});

test("generic non-zero exit with unavailable socket logs the socket root cause", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  io.socketOk = false;
  io.logCalls = [];
  app.selection = 1;
  app.focus = "agents";
  const attaching = app.press("enter");
  io.endGeneric(1);
  await attaching;
  const attachEnd = io.logCalls.find((c) => c.event === "attach_end");
  assert.ok(attachEnd, "missing attach_end");
  assert.equal(attachEnd.fields.reason, "herdr session /tmp/swarmforge/test.sock is unavailable");
  assert.equal(attachEnd.fields.socket_available, false);
  assert.equal(attachEnd.fields.session_alive, false);
});

test("generic non-zero exit with alive session logs the client exit status", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  io.logCalls = [];
  app.selection = 1;
  app.focus = "agents";
  const attaching = app.press("enter");
  io.endGeneric(1);
  await attaching;
  const attachEnd = io.logCalls.find((c) => c.event === "attach_end");
  assert.ok(attachEnd, "missing attach_end");
  assert.equal(attachEnd.fields.reason, "herdr client exited with status 1");
  assert.equal(attachEnd.fields.socket_available, true);
  assert.equal(attachEnd.fields.session_alive, true);
});

test("esc clears a persistent attach error", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.attachError = "server disconnected unexpectedly";
  await app.press("esc");
  assert.equal(app.attachError, null);
});

test("attach start clears a previous attach error", async () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  app.selection = 1;
  app.focus = "agents";
  app.attachError = "server disconnected unexpectedly";
  const attaching = app.attachSelected();
  assert.equal(app.attachError, null);
  io.detach();
  await attaching;
  assert.equal(app.attachError, null);
});

test("poll logs a socket_check event on the transition to unavailable", () => {
  const io = new FakeIO();
  const app = new App(io);
  app.start();
  io.logCalls = [];
  io.socketOk = false;
  app.poll();
  assert.ok(
    io.logCalls.some((c) => c.event === "socket_check" && c.fields.status === "unavailable"),
    "missing socket_check unavailable",
  );
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
  assert.equal(app.focus, "agents");
  await app.press("ctrl+k");
  await app.press("tab");
  assert.equal(app.focus, "detail");
  await app.press("ctrl+k");
  await app.press("tab");
  assert.equal(app.focus, "menu");
  await app.press("ctrl+k");
  await app.press("tab");
  assert.equal(app.focus, "agents");
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
