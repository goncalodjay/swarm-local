/**
 * Frame model and text formatting.
 *
 * This module is pure: it turns application state into plain strings and
 * never emits escape sequences. All color and layout live in the OpenTUI
 * layer (`ui/dashboard.ts`), which reads these strings and styles them
 * with theme tokens. Keeping the two apart means the wording can be tested
 * without a terminal, and the styling can change without touching wording.
 */

import { REQUIRED_SIZE, type App } from "./app.ts";
import { isDisabledMenuItem, MENU_DASHBOARD, menuItems } from "./menu.ts";
import type { AgentState, FocusTarget, HandoffInfo, Mode, Role, Status, TerminalSize } from "./types.ts";

/** Width of the agents panel, in columns. */
export const PANEL_WIDTH = 30;

export interface FrameModel {
  view: "dashboard" | "error" | "too-small";
  roles: Role[];
  agents: AgentState[];
  selection: number;
  errorMessage: string;
  attachError: string | null;
  terminalSize: TerminalSize;
  requiredSize: TerminalSize;
  socket: string;
  mode: Mode;
  focus: FocusTarget;
  menuFocus: number;
  hint: string | null;
  helpOpen: boolean;
  herdrMatched: number;
  herdrTotal: number;
}

export function frameModel(app: App): FrameModel {
  let herdrMatched = 0;
  for (const agent of app.agents) {
    if (agent.herdrStatus !== null) herdrMatched += 1;
  }
  return {
    view: app.view === "attached" ? "dashboard" : app.view,
    roles: app.roles,
    agents: app.agents,
    selection: app.selection,
    errorMessage: app.errorMessage,
    attachError: app.attachError,
    terminalSize: app.io.terminalSize(),
    requiredSize: REQUIRED_SIZE,
    socket: app.io.socketPath(),
    mode: app.mode,
    focus: app.focus,
    menuFocus: app.menuFocus,
    hint: app.hint,
    helpOpen: app.helpOpen,
    herdrMatched,
    herdrTotal: app.roles.length,
  };
}

export function markerGlyph(marker: AgentState["marker"]): string {
  switch (marker) {
    case "spinner":
      return "◐";
    case "dot":
      return "●";
    case "bang":
      return "!";
    default:
      return " ";
  }
}

export function statusLabel(status: Status): string {
  switch (status) {
    case "working":
      return "working";
    case "needs-human":
      return "needs human";
    case "finished-idle":
      return "finished";
    default:
      return "idle";
  }
}

export interface HandoffEvent {
  at: string;
  label: string;
  task: string | null;
}

export function snapshotEvents(snapshot: AgentState["handoffs"]): HandoffEvent[] {
  const events: HandoffEvent[] = [];
  for (const [state, handoffs] of [
    ["queued", snapshot.queued],
    ["in process", snapshot.inProcess],
    ["completed", snapshot.completed],
  ] as const) {
    for (const handoff of handoffs) pushEvents(events, handoff, state);
  }
  return events;
}

function pushEvents(events: HandoffEvent[], h: HandoffInfo, state: string): void {
  if (h.created_at) events.push({ at: h.created_at, label: `created (${state})`, task: h.task });
  if (h.dequeued_at) events.push({ at: h.dequeued_at, label: "dequeued", task: h.task });
  if (h.completed_at) events.push({ at: h.completed_at, label: "completed", task: h.task });
}

/** Title shown in the top border of the frame. */
export const APP_TITLE = "SwarmForge TUI";

/** Segments of the header line, so each can carry its own emphasis. */
export interface HeaderSegments {
  socket: string;
  poll: string;
  herdr: string;
  /** True when herdr matched no agent, which the UI warns about. */
  herdrDegraded: boolean;
  size: string;
}

export function headerSegments(model: FrameModel): HeaderSegments {
  return {
    socket: model.socket === "" ? "socket: —" : `socket: ${model.socket}`,
    poll: "poll 1s",
    herdr: `herdr: ${model.herdrMatched}/${model.herdrTotal} matched`,
    herdrDegraded: model.herdrMatched === 0,
    size: `${model.terminalSize.cols}x${model.terminalSize.rows}`,
  };
}

export function renderHeader(model: FrameModel): string {
  const s = headerSegments(model);
  return `${APP_TITLE} · ${s.socket} · ${s.poll} · ${s.herdr} · ${s.size}`;
}

/** One entry of the menu bar, already decorated with its brackets. */
export interface MenuEntry {
  item: string;
  text: string;
  disabled: boolean;
  active: boolean;
  isDashboard: boolean;
}

export function menuEntries(
  roles: Role[],
  focus: FocusTarget = "agents",
  menuFocus = 0,
): MenuEntry[] {
  return menuItems(roles).map((item, index) => {
    const disabled = isDisabledMenuItem(item);
    return {
      item,
      text: disabled ? `(${item})` : `[${item}]`,
      disabled,
      active: focus === "menu" && index === menuFocus,
      isDashboard: item === MENU_DASHBOARD,
    };
  });
}

export function renderMenuBar(roles: Role[], focus: FocusTarget = "agents", menuFocus = 0): string {
  return menuEntries(roles, focus, menuFocus)
    .map((entry) => entry.text)
    .join(" ");
}

export function renderAgentRow(agent: AgentState, selected: boolean): string {
  const sel = selected ? ">" : " ";
  const glyph = markerGlyph(agent.marker);
  const task = agent.task ?? "";
  return `${sel} ${glyph} ${agent.role}${task !== "" ? " " + task : ""}`;
}

export function renderAgentsPanel(agents: AgentState[], selection: number): string[] {
  return agents.map((agent, index) => renderAgentRow(agent, index === selection));
}

/** A detail line plus the semantic role its value carries. */
export interface DetailLine {
  text: string;
  tone: "default" | "muted" | "status";
  status?: Status;
}

export function detailLines(agent: AgentState | null): DetailLine[] {
  if (!agent) return [{ text: "No agent selected", tone: "muted" }];
  const lines: DetailLine[] = [];
  lines.push({ text: `Task: ${agent.task ?? "—"}`, tone: "default" });
  lines.push({ text: `State: ${statusLabel(agent.status)}`, tone: "status", status: agent.status });
  if (agent.herdrStatus !== null) {
    lines.push({ text: `Herdr: ${agent.herdrStatus}`, tone: "default" });
  } else {
    lines.push({ text: "Herdr: not detected (no agent in this worktree)", tone: "muted" });
  }
  if (agent.terminalTitle) {
    lines.push({ text: `Title: ${agent.terminalTitle}`, tone: "default" });
  }
  const ip = agent.handoffs.inProcess[0];
  if (ip) {
    lines.push({ text: "Timestamps:", tone: "default" });
    lines.push({ text: `  created:   ${ip.created_at ?? "—"}`, tone: "muted" });
    lines.push({ text: `  dequeued:  ${ip.dequeued_at ?? "—"}`, tone: "muted" });
    lines.push({ text: `  completed: ${ip.completed_at ?? "—"}`, tone: "muted" });
  }
  const events = snapshotEvents(agent.handoffs);
  if (events.length > 0) {
    lines.push({ text: "Recent events:", tone: "default" });
    for (const event of events.slice(0, 6)) {
      lines.push({
        text: `  ${event.at} ${event.label}${event.task ? " " + event.task : ""}`,
        tone: "muted",
      });
    }
  }
  return lines;
}

/** Title of the detail panel for the selected agent. */
export function detailTitle(agent: AgentState | null): string {
  return agent ? `Detail — ${agent.role}` : "Detail";
}

export function renderDetailPane(agent: AgentState | null): string[] {
  if (!agent) return ["No agent selected"];
  return [detailTitle(agent), ...detailLines(agent).map((line) => line.text)];
}

export function renderLegend(): string {
  return `${markerGlyph("spinner")} working · ${markerGlyph("dot")} finished · ${markerGlyph("bang")} needs human · (blank) idle`;
}

export function renderFooter(mode: Mode, hint: string | null, helpOpen: boolean): string {
  if (hint !== null) return hint;
  if (helpOpen) return "q / Esc close help";
  if (mode === "prefix") return "Tab cycle focus · ? help · q quit · Esc cancel";
  return "↑/↓ select · Enter attach · Ctrl+k menu · q quit";
}

export function renderError(errorMessage: string): string[] {
  return ["Swarm unavailable", "", errorMessage];
}

export function renderTooSmall(current: TerminalSize, required: TerminalSize): string[] {
  return [
    "Terminal too small",
    "",
    `Current size: ${current.cols}x${current.rows}`,
    `Required size: ${required.cols}x${required.rows}`,
  ];
}

/** Rows of the help overlay, as label/keys pairs. */
export const HELP_ROWS: ReadonlyArray<readonly [string, string]> = [
  ["Motion", "↑/↓ · j/k   select"],
  ["Jump", "Home/End · g/G"],
  ["Menu", "←/→ · Enter  select"],
  ["Ctrl+k", "Tab   cycle focus"],
  ["Ctrl+k", "?     help"],
  ["Ctrl+k", "q     quit"],
  ["Esc", "cancel"],
];

export function helpLines(focus: FocusTarget): string[] {
  return [
    `Focus: ${focus}`,
    ...HELP_ROWS.map(([label, keys]) => `${label.padEnd(8)}${keys}`),
    "Close   q · Esc · ?",
  ];
}
