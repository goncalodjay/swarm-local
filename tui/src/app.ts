import type { AgentState, AppView, FocusTarget, HandoffSnapshot, Key, Mode, Role, TerminalSize } from "./types.ts";
import { moveSelection } from "./selection.ts";
import { isDisabledMenuItem, menuItems, moveMenuFocus } from "./menu.ts";
import { agentState } from "./status.ts";

export const REQUIRED_SIZE: TerminalSize = { cols: 100, rows: 30 };

const FOCUS_ORDER: FocusTarget[] = ["agents", "detail", "menu"];

export interface TuiIO {
  readRoles(): Role[];
  readSnapshot(role: Role): HandoffSnapshot;
  socketPath(): string;
  socketAvailable(): boolean;
  terminalSize(): TerminalSize;
  attach(session: string, socket: string): Promise<string | null>;
  restore(): void;
  quit(): void;
}

export class App {
  roles: Role[] = [];
  agents: AgentState[] = [];
  selection = 0;
  view: AppView = "dashboard";
  errorMessage = "";
  mode: Mode = "normal";
  focus: FocusTarget = "agents";
  menuFocus = 0;
  hint: string | null = null;
  attachError: string | null = null;
  helpOpen = false;
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
    if (this.view === "attached") return;
    this.hint = null;
    if (this.helpOpen) {
      if (key === "quit" || key === "esc" || key === "?") this.helpOpen = false;
      return;
    }
    if (this.mode === "prefix") {
      this.mode = "normal";
      switch (key) {
        case "tab":
          this.cycleFocus();
          break;
        case "?":
          this.helpOpen = true;
          break;
        case "quit":
          this.quit();
          break;
        default:
          break;
      }
      return;
    }
    switch (key) {
      case "up":
      case "down":
      case "j":
      case "k":
      case "home":
      case "end":
      case "g":
      case "G":
        this.selection = moveSelection(this.selection, key, this.agents.length);
        break;
      case "left":
      case "right":
        if (this.focus === "menu") this.menuFocus = moveMenuFocus(this.menuFocus, key, menuItems(this.roles).length);
        break;
      case "tab":
        this.cycleFocus();
        break;
      case "enter":
        await this.activate();
        break;
      case "ctrl+k":
        this.mode = "prefix";
        break;
      case "quit":
        this.quit();
        break;
      default:
        break;
    }
  }

  cycleFocus(): void {
    const index = FOCUS_ORDER.indexOf(this.focus);
    this.focus = FOCUS_ORDER[(index + 1) % FOCUS_ORDER.length];
  }

  async activate(): Promise<void> {
    if (this.focus === "agents") {
      await this.attachSelected();
      return;
    }
    if (this.focus !== "menu") return;
    const items = menuItems(this.roles);
    const item = items[this.menuFocus] ?? "dashboard";
    if (isDisabledMenuItem(item)) {
      this.hint = `${item}: not implemented`;
      return;
    }
    const roleIndex = this.roles.findIndex((role) => role.role === item);
    if (roleIndex >= 0) {
      this.focus = "agents";
      this.selection = roleIndex;
    }
  }

  async attachSelected(): Promise<void> {
    const agent = this.agents[this.selection];
    if (!agent) return;
    this.beginAttach();
    const reason = await this.io.attach(agent.session, this.io.socketPath());
    this.resumeAfterDetach(reason);
  }

  beginAttach(): void {
    this.view = "attached";
  }

  resumeAfterDetach(reason: string | null = null): void {
    this.view = "dashboard";
    this.attachError = reason;
    this.poll();
  }

  quit(): void {
    this.io.restore();
    this.io.quit();
  }
}
