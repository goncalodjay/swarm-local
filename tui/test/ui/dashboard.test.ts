import { test } from "node:test";
import assert from "node:assert/strict";
import { createTestRenderer } from "@opentui/core/testing";
import { FrameMount } from "../../src/ui/dashboard.ts";
import { darkTheme, monochromeTheme, selectTheme, statusColor } from "../../src/theme.ts";
import type { FrameModel } from "../../src/render.ts";
import { agent, agentsForRoles, model, roles } from "../helpers/model.ts";

const WIDTH = 120;
const HEIGHT = 40;

/** Render a model through the real OpenTUI pipeline and return the frame. */
async function frameOf(m: FrameModel, theme = darkTheme): Promise<string> {
  const { renderer, renderOnce, captureCharFrame } = await createTestRenderer({
    width: WIDTH,
    height: HEIGHT,
  });
  const mount = new FrameMount(renderer, theme);
  try {
    mount.update(m);
    await renderOnce();
    return captureCharFrame();
  } finally {
    mount.destroy();
    renderer.destroy();
  }
}

test("dashboard frame draws both panels", async () => {
  const frame = await frameOf(model("dashboard", agentsForRoles(1, "fix-login"), 1));
  assert.ok(frame.includes("Agents"), "agents panel missing");
  assert.ok(frame.includes("Detail — coder"), "detail panel missing");
  assert.ok(frame.includes("┌"), "no box drawing");
  assert.ok(frame.includes("└"), "no box drawing");
});

test("dashboard frame lists menu entries", async () => {
  const frame = await frameOf(model("dashboard", agentsForRoles(1), 1));
  for (const entry of ["[dashboard]", "[specifier]", "[coder]", "[reviewer]", "[architect]", "(logs)", "(costs)"]) {
    assert.ok(frame.includes(entry), `menu bar missing ${entry}`);
  }
});

test("dashboard frame shows the selected agent row and its task", async () => {
  const frame = await frameOf(model("dashboard", agentsForRoles(1, "fix-login"), 1));
  assert.ok(frame.includes("> ◐ coder fix-login"), "selected row missing");
});

test("dashboard frame shows header and footer", async () => {
  const frame = await frameOf(model("dashboard", agentsForRoles(1), 1));
  assert.ok(frame.includes("SwarmForge TUI"));
  assert.ok(frame.includes("/p/.swarmforge/swarm.sock"));
  assert.ok(frame.includes("↑/↓ select"));
});

test("dashboard frame shows a non-transient attach error banner", async () => {
  const frame = await frameOf(
    model("dashboard", agentsForRoles(1), 1, { attachError: "server disconnected unexpectedly" }),
  );
  assert.ok(frame.includes("server disconnected unexpectedly"));
});

test("help overlay lists the keybindings and current focus", async () => {
  const frame = await frameOf(model("dashboard", agentsForRoles(1), 1, { helpOpen: true }));
  for (const key of ["Ctrl+k", "Tab", "j/k", "g/G"]) {
    assert.ok(frame.includes(key), `help overlay missing ${key}`);
  }
  assert.ok(frame.includes("Focus: menu"));
});

test("help overlay stays inside the terminal", async () => {
  const frame = await frameOf(model("dashboard", agentsForRoles(1), 1, { helpOpen: true }));
  for (const line of frame.split("\n")) {
    assert.ok(line.length <= WIDTH, `line overflows terminal width: ${line.length}`);
  }
});

test("error view reports the swarm as unavailable", async () => {
  const frame = await frameOf(model("error", [], 0, { errorMessage: "The swarm socket is unavailable." }));
  assert.ok(frame.includes("Swarm unavailable"));
  assert.ok(frame.includes("socket"));
});

test("too-small view reports current and required sizes", async () => {
  const frame = await frameOf(
    model("too-small", [], 0, { terminalSize: { cols: 80, rows: 24 } }),
  );
  assert.ok(frame.includes("80x24"));
  assert.ok(frame.includes("100x30"));
});

test("frame never scrolls past the terminal height", async () => {
  const frame = await frameOf(model("dashboard", agentsForRoles(1, "fix-login"), 1));
  assert.ok(frame.split("\n").length <= HEIGHT + 1, "frame taller than the terminal");
});

test("frame renders at the minimum supported terminal size", async () => {
  const { renderer, renderOnce, captureCharFrame } = await createTestRenderer({ width: 100, height: 30 });
  const mount = new FrameMount(renderer, darkTheme);
  try {
    mount.update(model("dashboard", agentsForRoles(1, "fix-login"), 1, {
      terminalSize: { cols: 100, rows: 30 },
    }));
    await renderOnce();
    const frame = captureCharFrame();
    assert.ok(frame.includes("Agents"));
    assert.ok(frame.includes("Detail — coder"));
    for (const line of frame.split("\n")) {
      assert.ok(line.length <= 100, `line overflows 100 columns: ${line.length}`);
    }
  } finally {
    mount.destroy();
    renderer.destroy();
  }
});

test("remounting a frame replaces the previous one", async () => {
  const { renderer, renderOnce, captureCharFrame } = await createTestRenderer({ width: WIDTH, height: HEIGHT });
  const mount = new FrameMount(renderer, darkTheme);
  try {
    mount.update(model("dashboard", agentsForRoles(1, "fix-login"), 1));
    await renderOnce();
    mount.update(model("dashboard", agentsForRoles(3, "ship-it"), 3));
    await renderOnce();
    const frame = captureCharFrame();
    assert.ok(frame.includes("Detail — architect"), "detail did not follow the selection");
    assert.ok(!frame.includes("fix-login"), "stale frame content survived the remount");
  } finally {
    mount.destroy();
    renderer.destroy();
  }
});

test("the monochrome theme still renders a usable frame", async () => {
  const frame = await frameOf(model("dashboard", agentsForRoles(1, "fix-login"), 1), monochromeTheme);
  assert.ok(frame.includes("> ◐ coder fix-login"), "status must survive without color");
  assert.ok(frame.includes("[dashboard]"));
});

test("needs-human agents are legible without color", async () => {
  const blocked = [agent(roles[2]!, "bang", "auth")];
  const frame = await frameOf(model("dashboard", blocked, 0), monochromeTheme);
  assert.ok(frame.includes("!"), "needs-human marker must not rely on color");
  assert.ok(frame.includes("needs human"), "status label must be spelled out");
});

test("the accent is a light violet", () => {
  assert.equal(darkTheme.accentPrimary, "#b9a3f5");
  assert.equal(darkTheme.borderFocus, darkTheme.accentPrimary);
});

test("NO_COLOR selects the monochrome theme", () => {
  assert.equal(selectTheme({}), darkTheme);
  assert.equal(selectTheme({ NO_COLOR: "" }), monochromeTheme);
  assert.equal(selectTheme({ NO_COLOR: "1" }), monochromeTheme);
});

test("statusColor maps every status to a theme token", () => {
  assert.equal(statusColor(darkTheme, "working"), darkTheme.statusWarning);
  assert.equal(statusColor(darkTheme, "needs-human"), darkTheme.statusError);
  assert.equal(statusColor(darkTheme, "finished-idle"), darkTheme.statusSuccess);
  assert.equal(statusColor(darkTheme, "idle"), darkTheme.fgMuted);
});

test("a long socket path never eats the header separators", async () => {
  const frame = await frameOf(
    model("dashboard", agentsForRoles(1), 1, {
      socket: "/tmp/a-very-long-swarmforge-socket-path-that-overflows-the-header/swarm.sock",
    }),
  );
  const header = frame.split("\n")[0] ?? "";
  assert.ok(header.includes("SwarmForge TUI · socket:"), `separator lost: ${header}`);
  assert.ok(header.includes(" · poll 1s"), `separator lost: ${header}`);
  assert.ok(header.includes(" · herdr:"), `separator lost: ${header}`);
});
