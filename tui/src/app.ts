import type { AgentState, AppView, HandoffSnapshot, Key, Role, TerminalSize } from "./types.ts";
import { moveSelection } from "./selection.ts";
import { agentState } from "./status.ts";

export const REQUIRED_SIZE: TerminalSize = { cols: 100, rows: 30 };

export interface TuiIO {
  readRoles(): Role[];
  readSnapshot(role: Role): HandoffSnapshot;
  socketPath(): string;
  socketAvailable(): boolean;
  terminalSize(): TerminalSize;
  attach(session: string, socket: string): Promise<void>;
  restore(): void;
  quit(): void;
}

export class App {
  roles: Role[] = [];
  agents: AgentState[] = [];
  selection = 0;
  view: AppView = "dashboard";
  errorMessage = "";
  attachedSession: string | null = null;
  io: TuiIO;

  constructor(io: TuiIO) {
    this.io = io;
  }

  start(): void {
    this.checkSocket();
    if (this.view === "error") return;
    this.roles = this.io.readRoles();
    this.refreshAgents();
    this.checkSize();
  }

  refreshAgents(): void {
    this.agents = this.roles.map((role) => agentState(role, this.io.readSnapshot(role)));
  }

  checkSocket(): void {
    if (this.view === "attached") return;
    if (!this.io.socketAvailable()) {
      this.view = "error";
      this.errorMessage = "The swarm socket is unavailable.";
    } else if (this.view === "error") {
      this.view = "dashboard";
    }
  }

  checkSize(): void {
    if (this.view === "attached" || this.view === "error") return;
    const size = this.io.terminalSize();
    if (size.cols < REQUIRED_SIZE.cols || size.rows < REQUIRED_SIZE.rows) {
      this.view = "too-small";
    } else if (this.view === "too-small") {
      this.view = "dashboard";
    }
  }

  poll(): void {
    this.refreshAgents();
    this.checkSocket();
    this.checkSize();
  }

  async press(key: Key): Promise<void> {
    switch (key) {
      case "up":
      case "down": {
        this.selection = moveSelection(this.selection, key, this.agents.length);
        break;
      }
      case "enter": {
        await this.attachSelected();
        break;
      }
      case "quit": {
        this.quit();
        break;
      }
    }
  }

  async attachSelected(): Promise<void> {
    const agent = this.agents[this.selection];
    if (!agent) return;
    this.beginAttach(agent.session);
    await this.io.attach(agent.session, this.io.socketPath());
    this.resumeAfterDetach();
  }

  beginAttach(session: string): void {
    this.attachedSession = session;
    this.view = "attached";
  }

  resumeAfterDetach(): void {
    this.attachedSession = null;
    this.view = "dashboard";
    this.poll();
  }

  quit(): void {
    this.io.restore();
    this.io.quit();
  }
}
