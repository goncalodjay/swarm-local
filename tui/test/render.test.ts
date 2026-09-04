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
  frameModel,
  type FrameModel,
} from "../src/render.ts";
import type { App, TuiIO } from "../src/app.ts";
import type { AgentState, Role, TerminalSize } from "../src/types.ts";
import { agent, roles } from "./helpers/model.ts";

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
    herdrStatus: null,
    terminalTitle: null,
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

