import assert from "node:assert/strict";
import { rmSync } from "node:fs";
import path from "node:path";
import {
  cleanupWorld,
  createWorld,
  startSwarm,
  writeInProcessHandoff,
  writeCompletedHandoff,
  writeUserNote,
  captureFrame,
  findAgentRow,
  parseAgentRow,
  markerForName,
  type World,
} from "./world.ts";
import type { StepHandlerDef } from "./runtime.ts";
import { menuItemIndex, menuItems } from "../tui/src/menu.ts";
import type { Key } from "../tui/src/types.ts";

type Handler = StepHandlerDef<World>;

function agentsPanelRoles(world: World): string[] {
  const configured = new Set(world.app.roles.map((r) => r.role));
  const roles: string[] = [];
  for (const line of world.frame) {
    const row = parseAgentRow(line);
    if (row && configured.has(row.role)) roles.push(row.role);
  }
  return roles;
}

function refreshAndCapture(world: World): void {
  world.app.refreshAgents();
  captureFrame(world);
}

function roleRowText(world: World, role: string): string {
  const row = findAgentRow(world, role);
  return `${row.selected ? ">" : " "} ${row.marker} ${row.role}${row.task ? " " + row.task : ""}`;
}

export function createHandlers(): Handler[] {
  const handlers: Handler[] = [];

  handlers.push({
    pattern: /^a running swarm with the configured roles$/,
    run: (world) => {
      startSwarm(world);
      world.app.start();
    },
  });

  handlers.push({
    pattern: /^the TUI starts$/,
    run: async (world) => {
      world.app.start();
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the TUI renders the agents panel$/,
    run: refreshAndCapture,
  });

  handlers.push({
    pattern: /^the TUI renders the detail pane$/,
    run: refreshAndCapture,
  });

  handlers.push({
    pattern: /^the TUI renders$/,
    run: refreshAndCapture,
  });

  handlers.push({
    pattern: /^the TUI is showing the dashboard$/,
    run: async (world) => {
      world.app.start();
      assert.equal(world.app.view, "dashboard");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the TUI renders the dashboard$/,
    run: (world) => {
      assert.equal(world.app.view, "dashboard");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the TUI shows the terminal-too-small screen$/,
    run: (world) => {
      assert.equal(world.app.view, "too-small");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the agents panel lists the configured roles in order$/,
    run: (world) => {
      const expected = world.app.roles.map((r) => r.role);
      assert.deepEqual(agentsPanelRoles(world), expected);
    },
  });

  handlers.push({
    pattern: /^the menu bar shows entries for dashboard, each role, logs and costs$/,
    run: (world) => {
      for (const entry of ["[dashboard]", "[specifier]", "[coder]", "[refactorer]", "[architect]", "(logs)", "(costs)"]) {
        assert.ok(world.rendered.includes(entry), `menu bar missing ${entry}`);
      }
    },
  });

  handlers.push({
    pattern: /^the logs and costs entries are shown as disabled$/,
    run: (world) => {
      assert.ok(world.rendered.includes("(logs)"));
      assert.ok(world.rendered.includes("(costs)"));
    },
  });

  handlers.push({
    pattern: /^the footer shows the available keybindings$/,
    run: (world) => {
      assert.ok(world.rendered.includes("↑/↓ select"));
    },
  });

  handlers.push({
    pattern: /^the agent (\S+) has the handoff state (one handoff in process)$/,
    run: (world, _step, _example, [role]) => {
      writeInProcessHandoff(world, role, "task");
    },
  });

  handlers.push({
    pattern: /^the agent (\S+) has the handoff state (completed tasks and nothing pending)$/,
    run: (world, _step, _example, [role]) => {
      writeCompletedHandoff(world, role, "done");
    },
  });

  handlers.push({
    pattern: /^the agent (\S+) has the handoff state (nothing completed and nothing pending)$/,
    run: () => {},
  });

  handlers.push({
    pattern: /^the agent (\S+) has the handoff state (a pending note to the user)$/,
    run: (world, _step, _example, [role]) => {
      writeUserNote(world, role);
    },
  });

  handlers.push({
    pattern: /^the agent (\S+) has an in-process handoff for task (\S+)$/,
    run: (world, _step, _example, [role, task]) => {
      writeInProcessHandoff(world, role, task);
    },
  });

  handlers.push({
    pattern: /^the (\S+) row shows the (\S+) marker$/,
    run: (world, _step, _example, [role, marker]) => {
      const row = findAgentRow(world, role);
      assert.equal(row.marker, markerForName(marker), `marker for ${role}`);
    },
  });

  handlers.push({
    pattern: /^the detail pane shows the task (\S+)$/,
    run: (world, _step, _example, [task]) => {
      assert.ok(world.rendered.includes(`Task: ${task}`), `detail pane missing task ${task}`);
    },
  });

  handlers.push({
    pattern: /^the detail pane shows the current state$/,
    run: (world) => {
      assert.ok(world.rendered.includes("State:"), "detail pane missing current state");
    },
  });

  handlers.push({
    pattern: /^the detail pane shows the handoff timestamps$/,
    run: (world) => {
      assert.ok(world.rendered.includes("Timestamps:"), "detail pane missing timestamps");
    },
  });

  handlers.push({
    pattern: /^the detail pane shows the recent handoff events$/,
    run: (world) => {
      assert.ok(world.rendered.includes("Recent events:"), "detail pane missing recent events");
    },
  });

  handlers.push({
    pattern: /^the coder row shows the task name (\S+)$/,
    run: (world, _step, _example, [task]) => {
      assert.ok(roleRowText(world, "coder").includes(task));
    },
  });

  handlers.push({
    pattern: /^the coder row shows a blank status$/,
    run: (world) => {
      const row = findAgentRow(world, "coder");
      assert.equal(row.marker, " ");
    },
  });

  handlers.push({
    pattern: /^the selection is on (\S+)$/,
    run: (world, _step, _example, [role]) => {
      world.app.refreshAgents();
      const index = world.app.agents.findIndex((a) => a.role === role);
      assert.ok(index >= 0, `unknown role ${role}`);
      world.app.selection = index;
      world.app.focus = "agents";
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the selection moves to (\S+)$/,
    run: (world, _step, _example, [role]) => {
      const agent = world.app.agents[world.app.selection];
      assert.ok(agent, "no selected agent");
      assert.equal(agent.role, role);
    },
  });

  handlers.push({
    pattern: /^I press (down|up)$/,
    run: async (world, _step, _example, [key]) => {
      await world.app.press(key as "down" | "up");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^I press enter$/,
    run: async (world) => {
      await world.app.press("enter");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^I press ctrl\+k$/,
    run: async (world) => {
      await world.app.press("ctrl+k");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^I press esc$/,
    run: async (world) => {
      await world.app.press("esc");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^I press tab (\d+) times$/,
    run: async (world, _step, _example, [times]) => {
      for (let i = 0; i < Number(times); i++) await world.app.press("tab");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^I press (j|k|Home|End|g|G)$/,
    run: async (world, _step, _example, [key]) => {
      const mapping: Record<string, Key> = { j: "j", k: "k", Home: "home", End: "end", g: "g", G: "G" };
      await world.app.press(mapping[key]);
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^I press (left|right)$/,
    run: async (world, _step, _example, [key]) => {
      await world.app.press(key as "left" | "right");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^I press \?$/,
    run: async (world) => {
      await world.app.press("?");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the focus is on (agents|detail|menu)$/,
    run: (world, step, _example, [panel]) => {
      const focus = panel as "agents" | "detail" | "menu";
      if (step.keyword.trim() === "Then") {
        assert.equal(world.app.focus, focus);
      } else {
        world.app.focus = focus;
        captureFrame(world);
      }
    },
  });

  handlers.push({
    pattern: /^the menu focus is on (dashboard|specifier|coder|refactorer|architect|logs|costs)$/,
    run: (world, step, _example, [item]) => {
      const index = menuItemIndex(menuItems(world.app.roles), item);
      if (step.keyword.trim() === "Then") {
        assert.equal(world.app.menuFocus, index, `menu focus should be ${item}`);
      } else {
        world.app.menuFocus = index;
        captureFrame(world);
      }
    },
  });

  handlers.push({
    pattern: /^the footer shows "([^"]+)"$/,
    run: (world, _step, _example, [text]) => {
      assert.ok(world.rendered.includes(text), `footer missing ${text}`);
    },
  });

  handlers.push({
    pattern: /^a hint "([^"]+): not implemented" is shown in the footer$/,
    run: (world, _step, _example, [item]) => {
      assert.ok(world.rendered.includes(`${item}: not implemented`), `hint missing ${item}`);
    },
  });

  handlers.push({
    pattern: /^the help overlay is shown$/,
    run: (world) => {
      assert.equal(world.app.helpOpen, true);
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the help overlay is closed$/,
    run: (world) => {
      assert.equal(world.app.helpOpen, false);
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the help overlay shows "([^"]+)"$/,
    run: (world, _step, _example, [key]) => {
      assert.ok(world.helpOverlay.includes(key), `help overlay missing ${key}`);
    },
  });

  handlers.push({
    pattern: /^I press q$/,
    run: async (world) => {
      await world.app.press("quit");
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the terminal attaches to the tmux session (\S+) on the swarm socket$/,
    run: (world, _step, _example, [session]) => {
      assert.equal(world.attachCalls.length, 1);
      assert.equal(world.attachCalls[0].session, session);
      assert.equal(world.attachCalls[0].socket, world.socket);
    },
  });

  handlers.push({
    pattern: /^the TUI is attached to the tmux session (\S+)$/,
    run: async (world, _step, _example, [session]) => {
      world.app.refreshAgents();
      const index = world.app.agents.findIndex((a) => a.session === session);
      assert.ok(index >= 0, `no session ${session}`);
      world.app.selection = index;
      world.autoDetach = false;
      world.pendingAttach = world.app.attachSelected();
    },
  });

  handlers.push({
    pattern: /^the tmux client detaches$/,
    run: async (world) => {
      assert.ok(world.pendingAttach, "no pending attach");
      world.detach();
      await world.pendingAttach;
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the TUI exits$/,
    run: (world) => {
      assert.ok(world.quitCalls > 0);
    },
  });

  handlers.push({
    pattern: /^the terminal is restored to its previous state$/,
    run: (world) => {
      assert.ok(world.restoreCalls > 0);
    },
  });

  handlers.push({
    pattern: /^the status poll interval is 1 second$/,
    run: () => {},
  });

  handlers.push({
    pattern: /^the agent coder is idle$/,
    run: refreshAndCapture,
  });

  handlers.push({
    pattern: /^a handoff for task (\S+) lands in the coder in-process inbox$/,
    run: (world, _step, _example, [task]) => {
      writeInProcessHandoff(world, "coder", task);
    },
  });

  handlers.push({
    pattern: /^the next status poll renders the coder row with a spinner and the task (\S+)$/,
    run: (world, _step, _example, [task]) => {
      world.app.poll();
      captureFrame(world);
      const row = findAgentRow(world, "coder");
      assert.equal(row.marker, "◐");
      assert.ok(roleRowText(world, "coder").includes(task));
    },
  });

  handlers.push({
    pattern: /^the swarm socket does not exist$/,
    run: (world) => {
      world.socketOk = false;
    },
  });

  handlers.push({
    pattern: /^the swarm socket becomes unreadable$/,
    run: (world) => {
      world.socketOk = false;
      world.app.poll();
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^an error screen reports that the swarm is unavailable$/,
    run: (world) => {
      assert.equal(world.app.view, "error");
      assert.ok(world.rendered.includes("Swarm unavailable"));
    },
  });

  handlers.push({
    pattern: /^the TUI does not crash$/,
    run: refreshAndCapture,
  });

  handlers.push({
    pattern: /^the terminal size is (\d+) columns by (\d+) rows$/,
    run: (world, _step, _example, [cols, rows]) => {
      world.size = { cols: Number(cols), rows: Number(rows) };
      world.app.poll();
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^a terminal-too-small screen shows the current size (\d+)x(\d+) and the required size 100x30$/,
    run: (world, _step, _example, [cols, rows]) => {
      assert.equal(world.app.view, "too-small");
      assert.ok(world.rendered.includes(`${cols}x${rows}`));
      assert.ok(world.rendered.includes("100x30"));
    },
  });

  handlers.push({
    pattern: /^the terminal is resized to (\d+) columns by (\d+) rows$/,
    run: (world, _step, _example, [cols, rows]) => {
      world.size = { cols: Number(cols), rows: Number(rows) };
      world.app.poll();
      captureFrame(world);
    },
  });

  handlers.push({
    pattern: /^the state files for the agent coder are missing or malformed$/,
    run: (world) => {
      const coder = world.app.roles.find((r) => r.role === "coder");
      assert.ok(coder, "coder role not configured");
      const inbox = path.join(coder.worktreePath, ".swarmforge", "handoffs", "inbox");
      rmSync(inbox, { recursive: true, force: true });
    },
  });

  return handlers;
}

export function makeWorld(): World {
  return createWorld();
}

export function disposeWorld(world: World): void {
  cleanupWorld(world);
}
