import { REQUIRED_SIZE, type App } from "./app.ts";
import { isDisabledMenuItem, MENU_DASHBOARD, menuItems } from "./menu.ts";
import { createStyle, detectColors, padVisible, stripAnsi, type Style } from "./style.ts";
import type { AgentState, FocusTarget, HandoffInfo, Mode, Role, Status, TerminalSize } from "./types.ts";

const style: Style = createStyle({ colors: detectColors() });

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

export function renderHeader(model: FrameModel): string {
  const socket = model.socket === "" ? "socket: —" : `socket: ${model.socket}`;
  const size = `${model.terminalSize.cols}x${model.terminalSize.rows}`;
  const herdrLabel = model.herdrMatched === 0
    ? style.yellow(`herdr: 0/${model.herdrTotal} matched`)
    : `herdr: ${model.herdrMatched}/${model.herdrTotal} matched`;
  return `${style.bold(style.cyan("SwarmForge TUI"))} · ${socket} · poll 1s · ${herdrLabel} · ${size}`;
}

export function renderMenuBar(roles: Role[], focus: FocusTarget = "agents", menuFocus = 0): string {
  return menuItems(roles)
    .map((item, index) => renderMenuItem(item, focus === "menu" && index === menuFocus))
    .join(" ");
}

function renderMenuItem(item: string, active: boolean): string {
  const text = isDisabledMenuItem(item) ? `(${item})` : `[${item}]`;
  if (active) return style.bold(style.cyan(text));
  if (isDisabledMenuItem(item)) return style.dim(text);
  if (item === MENU_DASHBOARD) return style.bold(text);
  return text;
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

export function renderDetailPane(agent: AgentState | null): string[] {
  if (!agent) return ["No agent selected"];
  const lines: string[] = [];
  lines.push(`Detail — ${agent.role}`);
  lines.push(`Task: ${agent.task ?? "—"}`);
  lines.push(`State: ${style.statusColor(agent.status)(statusLabel(agent.status))}`);
  if (agent.herdrStatus !== null) {
    lines.push(`Herdr: ${agent.herdrStatus}`);
  } else {
    lines.push(`Herdr: ${style.dim("not detected (no agent in this worktree)")}`);
  }
  if (agent.terminalTitle) {
    lines.push(`Title: ${agent.terminalTitle}`);
  }
  const ip = agent.handoffs.inProcess[0];
  if (ip) {
    lines.push("Timestamps:");
    lines.push(`  created:   ${ip.created_at ?? "—"}`);
    lines.push(`  dequeued:  ${ip.dequeued_at ?? "—"}`);
    lines.push(`  completed: ${ip.completed_at ?? "—"}`);
  }
  const events = snapshotEvents(agent.handoffs);
  if (events.length > 0) {
    lines.push("Recent events:");
    for (const event of events.slice(0, 6)) {
      lines.push(`  ${event.at} ${event.label}${event.task ? " " + event.task : ""}`);
    }
  }
  return lines;
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

function contentRow(left: string, right: string, inner: number): string {
  const rightWidth = inner - PANEL_WIDTH - 2;
  return `│ ${padVisible(left, PANEL_WIDTH - 1)}│ ${padVisible(right, rightWidth)}│`;
}

function dividerRow(cols: number, joint: string): string {
  const inner = cols - 2;
  return `├${"─".repeat(PANEL_WIDTH)}${joint}${"─".repeat(inner - PANEL_WIDTH - 1)}┤`;
}

function bottomBorder(cols: number): string {
  return `└${"─".repeat(cols - 2)}┘`;
}

function panelTitle(text: string, focused: boolean): string {
  return focused ? style.bold(style.cyan(text)) : text;
}

function renderDashboard(model: FrameModel): string[] {
  const cols = Math.max(model.terminalSize.cols, REQUIRED_SIZE.cols);
  const inner = cols - 2;
  const lines: string[] = [];
  lines.push(`┌${"─".repeat(inner)}┐`);
  lines.push(`│ ${padVisible(renderHeader(model), inner - 1)}│`);
  lines.push(`├${"─".repeat(inner)}┤`);
  lines.push(`│ ${padVisible(renderMenuBar(model.roles, model.focus, model.menuFocus), inner - 1)}│`);
  lines.push(dividerRow(cols, "┬"));

  const agents = renderAgentsPanel(model.agents, model.selection);
  const detail = renderDetailPane(model.agents[model.selection] ?? null);
  const detailTitle = detail[0] ?? "";
  lines.push(contentRow(panelTitle("Agents", model.focus === "agents"), panelTitle(detailTitle, model.focus === "detail"), inner));

  const bodyHeight = Math.max(agents.length, detail.length - 1);
  for (let i = 0; i < bodyHeight; i++) {
    lines.push(contentRow(agents[i] ?? "", detail[i + 1] ?? "", inner));
  }

  lines.push(dividerRow(cols, "┴"));
  if (model.attachError) {
    lines.push(`│ ${padVisible(style.bold(style.red(model.attachError)), inner - 1)}│`);
  }
  lines.push(`│ ${padVisible(style.dim(renderLegend()), inner - 1)}│`);
  lines.push(`│ ${padVisible(renderFooter(model.mode, model.hint, model.helpOpen), inner - 1)}│`);
  lines.push(bottomBorder(cols));
  return lines;
}

export function renderHelpBox(cols: number, focus: FocusTarget): string[] {
  const width = Math.min(46, cols - 4);
  const inner = width - 2;
  const row = (text: string): string => `│ ${padVisible(text, inner - 1)}│`;
  return [
    `┌${"─".repeat(inner)}┐`,
    row(`Focus: ${focus}`),
    `├${"─".repeat(inner)}┤`,
    row("Motion  ↑/↓ · j/k   select"),
    row("Jump    Home/End · g/G"),
    row("Menu    ←/→ · Enter  select"),
    row("Ctrl+k  Tab   cycle focus"),
    row("Ctrl+k  ?     help"),
    row("Ctrl+k  q     quit"),
    row("Esc     cancel"),
    `├${"─".repeat(inner)}┤`,
    row("Close   q · Esc · ?"),
    `└${"─".repeat(inner)}┘`,
  ];
}

function overlayFrame(frame: string[], overlay: string[]): string[] {
  const result = [...frame];
  const overlayWidth = overlay.length > 0 ? stripAnsi(overlay[0]).length : 0;
  const startRow = Math.max(0, Math.floor((frame.length - overlay.length) / 2));
  for (let i = 0; i < overlay.length; i++) {
    const row = startRow + i;
    if (row >= result.length) break;
    const base = stripAnsi(result[row]);
    const startCol = Math.max(0, Math.floor((base.length - overlayWidth) / 2));
    result[row] = base.slice(0, startCol) + overlay[i] + base.slice(startCol + overlayWidth);
  }
  return result;
}

function renderNoticeFrame(cols: number, title: string, body: string[]): string[] {
  const inner = cols - 2;
  const lines: string[] = [];
  lines.push(`┌${"─".repeat(inner)}┐`);
  lines.push(`│ ${padVisible(style.bold(title), inner - 1)}│`);
  lines.push(`├${"─".repeat(inner)}┤`);
  for (const bodyLine of body) {
    lines.push(`│ ${padVisible(bodyLine, inner - 1)}│`);
  }
  lines.push(`├${"─".repeat(inner)}┤`);
  lines.push(`│ ${padVisible(renderFooter("normal", null, false), inner - 1)}│`);
  lines.push(bottomBorder(cols));
  return lines;
}

export function renderFrame(model: FrameModel): string[] {
  const cols = Math.max(model.terminalSize.cols, REQUIRED_SIZE.cols);
  if (model.view === "error") {
    const message = renderError(model.errorMessage);
    return renderNoticeFrame(cols, message[0], message.slice(2));
  }
  if (model.view === "too-small") {
    const message = renderTooSmall(model.terminalSize, model.requiredSize);
    return renderNoticeFrame(cols, message[0], message.slice(2));
  }
  const frame = renderDashboard(model);
  return model.helpOpen ? overlayFrame(frame, renderHelpBox(cols, model.focus)) : frame;
}
