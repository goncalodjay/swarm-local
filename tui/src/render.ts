import type { AgentState, HandoffInfo, Role, Status, TerminalSize } from "./types.ts";

export interface FrameModel {
  view: "dashboard" | "error" | "too-small";
  roles: Role[];
  agents: AgentState[];
  selection: number;
  errorMessage: string;
  terminalSize: TerminalSize;
  requiredSize: TerminalSize;
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

export function renderMenuBar(roles: Role[]): string {
  const parts = ["[dashboard]"];
  for (const role of roles) parts.push(`[${role.role}]`);
  parts.push("(logs)");
  parts.push("(costs)");
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
  lines.push(`State: ${statusLabel(agent.status)}`);
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

export function renderFrame(model: FrameModel): string[] {
  const lines: string[] = [];
  lines.push(renderMenuBar(model.roles));
  lines.push("");
  if (model.view === "error") {
    lines.push(...renderError(model.errorMessage));
    return lines;
  }
  if (model.view === "too-small") {
    lines.push(...renderTooSmall(model.terminalSize, model.requiredSize));
    return lines;
  }
  lines.push(...renderAgentsPanel(model.agents, model.selection));
  lines.push("");
  lines.push(...renderDetailPane(model.agents[model.selection] ?? null));
  lines.push("");
  lines.push(renderFooter());
  return lines;
}
