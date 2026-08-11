export interface Role {
  role: string;
  worktreeName: string;
  worktreePath: string;
  session: string;
  displayName: string;
  agent: string;
  receiveMode: string;
}

export type Status = "working" | "finished-idle" | "needs-human" | "idle";
export type Marker = "spinner" | "dot" | "bang" | "blank";

export interface HandoffInfo {
  task: string | null;
  type: string | null;
  created_at: string | null;
  dequeued_at: string | null;
  completed_at: string | null;
}

export interface HandoffSnapshot {
  queued: HandoffInfo[];
  inProcess: HandoffInfo[];
  completed: HandoffInfo[];
  pendingUserNote: boolean;
}

export interface AgentState {
  role: string;
  displayName: string;
  session: string;
  status: Status;
  marker: Marker;
  task: string | null;
  handoffs: HandoffSnapshot;
}

export type AppView = "dashboard" | "error" | "too-small" | "attached";

export type Mode = "normal" | "prefix";

export type FocusTarget = "agents" | "detail" | "menu";

export type Key =
  | "up"
  | "down"
  | "left"
  | "right"
  | "j"
  | "k"
  | "home"
  | "end"
  | "g"
  | "G"
  | "enter"
  | "quit"
  | "esc"
  | "tab"
  | "ctrl+k"
  | "?";

export interface TerminalSize {
  cols: number;
  rows: number;
}
