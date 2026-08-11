import { REQUIRED_SIZE, type App } from "./app.ts";
import { createStyle, detectColors, padVisible, type Style } from "./style.ts";
import type { AgentState, HandoffInfo, Role, Status, TerminalSize } from "./types.ts";

const style: Style = createStyle({ colors: detectColors() });

export const PANEL_WIDTH = 30;

export interface FrameModel {
  view: "dashboard" | "error" | "too-small";
  roles: Role[];
  agents: AgentState[];
  selection: number;
  errorMessage: string;
  terminalSize: TerminalSize;
  requiredSize: TerminalSize;
  socket: string;
}

export function frameModel(app: App): FrameModel {
  return {
    view: app.view === "attached" ? "dashboard" : app.view,
    roles: app.roles,
    agents: app.agents,
    selection: app.selection,
    errorMessage: app.errorMessage,
    terminalSize: app.io.terminalSize(),
    requiredSize: REQUIRED_SIZE,
    socket: app.io.socketPath(),
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
  return `${style.bold(style.cyan("SwarmForge TUI"))} · ${socket} · poll 1s · ${size}`;
}

export function renderMenuBar(roles: Role[]): string {
  const parts = [style.bold("[dashboard]")];
  for (const role of roles) parts.push(`[${role.role}]`);
  parts.push(style.dim("(logs)"));
  parts.push(style.dim("(costs)"));
  return parts.join(" ");
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

export function renderFooter(): string {
  return "↑/↓ select · Enter attach · q quit";
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

function renderDashboard(model: FrameModel): string[] {
  const cols = Math.max(model.terminalSize.cols, REQUIRED_SIZE.cols);
  const inner = cols - 2;
  const lines: string[] = [];
  lines.push(`┌${"─".repeat(inner)}┐`);
  lines.push(`│ ${padVisible(renderHeader(model), inner - 1)}│`);
  lines.push(`├${"─".repeat(inner)}┤`);
  lines.push(`│ ${padVisible(renderMenuBar(model.roles), inner - 1)}│`);
  lines.push(dividerRow(cols, "┬"));

  const agents = renderAgentsPanel(model.agents, model.selection);
  const detail = renderDetailPane(model.agents[model.selection] ?? null);
  const detailTitle = detail[0] ?? "";
  lines.push(contentRow(style.bold(style.cyan("Agents")), style.bold(detailTitle), inner));

  const bodyHeight = Math.max(agents.length, detail.length - 1);
  for (let i = 0; i < bodyHeight; i++) {
    lines.push(contentRow(agents[i] ?? "", detail[i + 1] ?? "", inner));
  }

  lines.push(dividerRow(cols, "┴"));
  lines.push(`│ ${padVisible(style.dim(renderLegend()), inner - 1)}│`);
  lines.push(`│ ${padVisible(renderFooter(), inner - 1)}│`);
  lines.push(bottomBorder(cols));
  return lines;
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
  lines.push(`│ ${padVisible(renderFooter(), inner - 1)}│`);
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
  return renderDashboard(model);
}
