import { test } from "node:test";
import assert from "node:assert/strict";
import {
  markerGlyph,
  statusLabel,
  renderHeader,
  renderLegend,
  renderMenuBar,
  renderAgentRow,
  renderAgentsPanel,
  renderDetailPane,
  renderFooter,
  renderError,
  renderTooSmall,
  renderFrame,
  frameModel,
  type FrameModel,
} from "../src/render.ts";
import type { App, TuiIO } from "../src/app.ts";
import type { AgentState, Role, TerminalSize } from "../src/types.ts";

const roles: Role[] = [
  { role: "specifier", worktreeName: "master", worktreePath: "/p", session: "swarmforge-specifier", displayName: "Specifier", agent: "opencode", receiveMode: "task" },
  { role: "coder", worktreeName: "coder", worktreePath: "/p", session: "swarmforge-coder", displayName: "Coder", agent: "opencode", receiveMode: "task" },
  { role: "reviewer", worktreeName: "reviewer", worktreePath: "/p", session: "swarmforge-reviewer", displayName: "Reviewer", agent: "codex", receiveMode: "task" },
  { role: "architect", worktreeName: "architect", worktreePath: "/p", session: "swarmforge-architect", displayName: "Architect", agent: "opencode", receiveMode: "batch" },
];

function agent(role: Role, marker: AgentState["marker"], task: string | null): AgentState {
  return {
    role: role.role,
    displayName: role.displayName,
    session: role.session,
    status: marker === "spinner" ? "working" : marker === "bang" ? "needs-human" : marker === "dot" ? "finished-idle" : "idle",
    marker,
    task,
    handoffs: { queued: [], inProcess: [], completed: [], pendingUserNote: marker === "bang" },
  };
}

function model(view: FrameModel["view"], agents: AgentState[], selection: number, overrides: Partial<FrameModel> = {}): FrameModel {
  return {
    view,
    roles,
    agents,
    selection,
    errorMessage: "boom",
    attachError: null,
    terminalSize: { cols: 120, rows: 40 },
    requiredSize: { cols: 100, rows: 30 },
    socket: "/p/.swarmforge/swarm.sock",
    mode: "normal",
    focus: "menu",
    menuFocus: 0,
    hint: null,
    helpOpen: false,
    ...overrides,
  };
}

test("markerGlyph renders distinct glyphs", () => {
  assert.equal(markerGlyph("spinner"), "◐");
  assert.equal(markerGlyph("dot"), "●");
  assert.equal(markerGlyph("bang"), "!");
  assert.equal(markerGlyph("blank"), " ");
});

test("statusLabel renders labels", () => {
  assert.equal(statusLabel("working"), "working");
  assert.equal(statusLabel("needs-human"), "needs human");
  assert.equal(statusLabel("finished-idle"), "finished");
  assert.equal(statusLabel("idle"), "idle");
});

test("renderMenuBar lists dashboard, roles, logs and costs", () => {
  const bar = renderMenuBar(roles);
  for (const entry of ["[dashboard]", "[specifier]", "[coder]", "[reviewer]", "[architect]", "(logs)", "(costs)"]) {
    assert.ok(bar.includes(entry), `menu bar missing ${entry}`);
  }
});

test("renderAgentRow shows marker and task", () => {
  const row = renderAgentRow(agent(roles[1], "spinner", "fix-login"), false);
  assert.ok(row.includes("◐"));
  assert.ok(row.includes("coder"));
  assert.ok(row.includes("fix-login"));
});

test("renderAgentRow marks selection", () => {
  const selected = renderAgentRow(agent(roles[0], "blank", null), true);
  const unselected = renderAgentRow(agent(roles[1], "blank", null), false);
  assert.ok(selected.startsWith(">"));
  assert.ok(unselected.startsWith(" "));
});

test("renderAgentsPanel lists roles in order", () => {
  const agents = roles.map((r, i) => agent(r, i === 0 ? "spinner" : "blank", null));
  const lines = renderAgentsPanel(agents, 0);
  const text = lines.join("\n");
  const positions = roles.map((r) => text.indexOf(r.role));
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
});

test("renderDetailPane shows task, state, timestamps and events", () => {
  const coder: AgentState = {
    role: "coder",
    displayName: "Coder",
    session: "swarmforge-coder",
    status: "working",
    marker: "spinner",
    task: "fix-login",
    handoffs: {
      queued: [],
      inProcess: [{ task: "fix-login", type: "git_handoff", created_at: "2026-01-01T00:00:00Z", dequeued_at: "2026-01-01T00:01:00Z", completed_at: null }],
      completed: [],
      pendingUserNote: false,
    },
  };
  const lines = renderDetailPane(coder).join("\n");
  assert.ok(lines.includes("fix-login"));
  assert.ok(lines.includes("working"));
  assert.ok(lines.includes("created:"));
  assert.ok(lines.includes("dequeued:"));
  assert.ok(lines.includes("Recent events:"));
});

test("renderDetailPane handles null agent", () => {
  assert.ok(renderDetailPane(null).join("\n").includes("No agent selected"));
});

test("renderFooter lists keybindings", () => {
  const footer = renderFooter("normal", null, false);
  assert.ok(footer.includes("Enter"));
  assert.ok(footer.includes("q"));
  assert.ok(footer.includes("Ctrl+k"));
});

test("renderError shows unavailable message", () => {
  const lines = renderError("The swarm socket is unavailable.");
  assert.ok(lines.join("\n").includes("Swarm unavailable"));
  assert.ok(lines.join("\n").includes("socket"));
});

test("renderTooSmall shows current and required sizes", () => {
  const lines = renderTooSmall({ cols: 80, rows: 24 }, { cols: 100, rows: 30 });
  const text = lines.join("\n");
  assert.ok(text.includes("80x24"));
  assert.ok(text.includes("100x30"));
});

test("renderFrame dashboard includes panel, menu and footer", () => {
  const agents = roles.map((r, i) => agent(r, i === 1 ? "spinner" : "blank", i === 1 ? "fix-login" : null));
  const frame = renderFrame(model("dashboard", agents, 1)).join("\n");
  assert.ok(frame.includes("[dashboard]"));
  assert.ok(frame.includes("◐ coder fix-login"));
  assert.ok(frame.includes("↑/↓ select"));
});

test("renderFrame error view reports unavailable", () => {
  const frame = renderFrame(model("error", [], 0)).join("\n");
  assert.ok(frame.includes("Swarm unavailable"));
});

test("renderFrame too-small view reports size", () => {
  const frame = renderFrame(model("too-small", [], 0, { terminalSize: { cols: 80, rows: 24 } })).join("\n");
  assert.ok(frame.includes("80x24"));
  assert.ok(frame.includes("100x30"));
});

function stubApp(overrides: Partial<App> = {}, size: TerminalSize = { cols: 120, rows: 40 }): App {
  const stub: Partial<App> = {
    view: "dashboard",
    roles,
    agents: [],
    selection: 0,
    errorMessage: "",
    mode: "normal",
    focus: "menu",
    menuFocus: 0,
    hint: null,
    helpOpen: false,
    io: { terminalSize: () => size, socketPath: () => "/p/.swarmforge/swarm.sock" } as unknown as TuiIO,
    ...overrides,
  };
  return stub as App;
}

test("frameModel maps the attached view to the dashboard", () => {
  const m = frameModel(stubApp({ view: "attached" }));
  assert.equal(m.view, "dashboard");
});

test("renderFrame dashboard shows a non-transient attach error banner", () => {
  const agents = roles.map((r, i) => agent(r, i === 1 ? "spinner" : "blank", null));
  const frame = renderFrame(model("dashboard", agents, 1, { attachError: "server disconnected unexpectedly" })).join("\n");
  assert.ok(frame.includes("server disconnected unexpectedly"));
});

test("frameModel passes through roles, agents, selection and error message", () => {
  const m = frameModel(stubApp({ selection: 2, errorMessage: "boom" }));
  assert.deepEqual(m.roles, roles);
  assert.deepEqual(m.agents, []);
  assert.equal(m.selection, 2);
  assert.equal(m.errorMessage, "boom");
});

test("frameModel reads terminal size from the IO adapter", () => {
  const m = frameModel(stubApp({}, { cols: 80, rows: 24 }));
  assert.deepEqual(m.terminalSize, { cols: 80, rows: 24 });
});

test("frameModel always reports the required size", () => {
  const m = frameModel(stubApp({}, { cols: 200, rows: 60 }));
  assert.deepEqual(m.requiredSize, { cols: 100, rows: 30 });
});

test("frameModel carries the socket path", () => {
  const m = frameModel(stubApp());
  assert.equal(m.socket, "/p/.swarmforge/swarm.sock");
});

test("renderHeader shows title, socket and terminal size", () => {
  const m = frameModel(stubApp({}, { cols: 120, rows: 40 }));
  const header = renderHeader(m).replace(/\x1b\[[0-9;]*m/g, "");
  assert.ok(header.includes("SwarmForge TUI"));
  assert.ok(header.includes("/p/.swarmforge/swarm.sock"));
  assert.ok(header.includes("120x40"));
});

test("renderHeader reports a missing socket", () => {
  const m = frameModel(stubApp({}, { cols: 120, rows: 40 }));
  const m2: FrameModel = { ...m, socket: "" };
  const header = renderHeader(m2).replace(/\x1b\[[0-9;]*m/g, "");
  assert.ok(header.includes("socket: —"));
});

test("renderLegend covers every status marker", () => {
  const legend = renderLegend();
  for (const glyph of [markerGlyph("spinner"), markerGlyph("dot"), markerGlyph("bang")]) {
    assert.ok(legend.includes(glyph), `legend missing ${glyph}`);
  }
});

test("renderFrame dashboard draws a boxed frame", () => {
  const agents = roles.map((r, i) => agent(r, i === 1 ? "spinner" : "blank", i === 1 ? "fix-login" : null));
  const frame = renderFrame(model("dashboard", agents, 1)).join("\n");
  assert.ok(frame.includes("┌"));
  assert.ok(frame.includes("└"));
  assert.ok(frame.includes("Agents"));
  assert.ok(frame.includes("Detail — coder"));
  assert.ok(frame.includes("◐ coder fix-login"));
  assert.ok(frame.includes("↑/↓ select"));
});

test("renderFrame error view is boxed", () => {
  const frame = renderFrame(model("error", [], 0)).join("\n");
  assert.ok(frame.includes("Swarm unavailable"));
  assert.ok(frame.includes("└"));
});

test("renderFrame too-small view is boxed", () => {
  const frame = renderFrame(model("too-small", [], 0, { terminalSize: { cols: 80, rows: 24 } })).join("\n");
  assert.ok(frame.includes("80x24"));
  assert.ok(frame.includes("100x30"));
  assert.ok(frame.includes("└"));
});

test("renderFooter switches to the prefix footer in prefix mode", () => {
  const footer = renderFooter("prefix", null, false);
  assert.ok(footer.includes("Tab cycle focus"));
  assert.ok(footer.includes("? help"));
  assert.ok(footer.includes("q quit"));
  assert.ok(footer.includes("Esc cancel"));
});

test("renderFooter shows a transient hint when present", () => {
  assert.equal(renderFooter("normal", "logs: not implemented", false), "logs: not implemented");
});

test("renderFooter shows the close hint while the help overlay is open", () => {
  assert.ok(renderFooter("normal", null, true).includes("close help"));
});

test("renderFrame with an open help overlay includes the keybinding list", () => {
  const agents = roles.map((r, i) => agent(r, i === 1 ? "spinner" : "blank", i === 1 ? "fix-login" : null));
  const frame = renderFrame(model("dashboard", agents, 1, { helpOpen: true })).join("\n");
  for (const key of ["Ctrl+k", "Tab", "?", "j/k", "g/G"]) {
    assert.ok(frame.includes(key), `help overlay missing ${key}`);
  }
  assert.ok(frame.includes("Focus: menu"));
});

test("renderFrame highlights the focused menu item", () => {
  const frame = renderFrame(model("dashboard", [], 0, { focus: "menu", menuFocus: 5 })).join("\n");
  assert.ok(frame.includes("(logs)"));
  const focused = renderFrame(model("dashboard", [], 0, { focus: "menu", menuFocus: 0 })).join("\n");
  assert.ok(focused.includes("[dashboard]"));
});

test("renderFrame highlights the focused panel header", () => {
  const agents = roles.map((r, i) => agent(r, i === 1 ? "spinner" : "blank", null));
  const focusedAgents = renderFrame(model("dashboard", agents, 1, { focus: "agents" })).join("\n");
  const focusedDetail = renderFrame(model("dashboard", agents, 1, { focus: "detail" })).join("\n");
  assert.ok(focusedAgents.includes("Agents"));
  assert.ok(focusedDetail.includes("Detail — coder"));
});
