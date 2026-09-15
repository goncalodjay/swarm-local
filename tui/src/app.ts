import type { AgentState, AppView, AttachResult, FocusTarget, HerdrAgent, HerdrStatus, HandoffSnapshot, Key, Mode, Role, TerminalSize } from "./types.ts";
import { diagnoseAttachEnd } from "./attach.ts";
import { moveSelection } from "./selection.ts";
import { isDisabledMenuItem, menuItems, moveMenuFocus } from "./menu.ts";
import { agentState } from "./status.ts";
import { child } from "./logger.ts";
import { findAgentForCwd } from "./herdr.ts";
import { notifyBlocked, notifyFinished } from "./sound.ts";

const log = child("app");

export const REQUIRED_SIZE: TerminalSize = { cols: 100, rows: 30 };

const FOCUS_ORDER: FocusTarget[] = ["agents", "detail", "menu"];

export interface TuiIO {
  readRoles(): Role[];
  readSnapshot(role: Role): HandoffSnapshot;
  socketPath(): string;
  socketAvailable(): boolean;
  terminalSize(): TerminalSize;
  attach(session: string, socket: string): Promise<AttachResult>;
  sessionExists(session: string): Promise<boolean>;
  queryHerdrAgents(): Promise<HerdrAgent[]>;
  log(event: string, fields: Record<string, string | number | boolean | null>): void;
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
  private lastSocketAvailable: boolean | null = null;
  private lastView: AppView = "dashboard";
  private lastHerdrStatus: Map<string, HerdrStatus> = new Map();
  private herdrAgents: HerdrAgent[] = [];

  constructor(io: TuiIO) {
    this.io = io;
  }

  start(): void {
    this.io.log("tui_start", {});
    this.checkSocket();
    if (this.view === "error") return;
    try {
      this.roles = this.io.readRoles();
    } catch (err) {
      log.error({ event: "start_roles_failed", err }, "cannot read roles at start");
      this.view = "error";
      this.errorMessage = "Cannot read roles.tsv.";
      return;
    }
    this.refreshAgents();
    this.checkSize();
    log.info(
      { event: "tui_ready", roles: this.roles.length, agents: this.agents.length },
      "tui ready",
    );
  }

  refreshAgents(): void {
    this.agents = this.roles.map((role) => {
      const snapshot = this.io.readSnapshot(role);
      const herdrAgent = findAgentForCwd(this.herdrAgents, role.worktreePath);
      return agentState(role, snapshot, herdrAgent?.agent_status ?? null, herdrAgent?.terminal_title ?? null);
    });
    this.detectHerdrTransitions();
  }

  private detectHerdrTransitions(): void {
    for (const agent of this.agents) {
      const prev = this.lastHerdrStatus.get(agent.role) ?? null;
      const curr = agent.herdrStatus ?? null;
      if (prev === curr) continue;
      this.lastHerdrStatus.set(agent.role, curr);
      if (prev === null || curr === null) continue;
      if (prev === "working" && curr === "blocked") {
        try { notifyBlocked(agent.role); } catch (err) {
          log.warn({ event: "sound_failed", role: agent.role, err }, "sound dispatch failed");
        }
      } else if (prev === "working" && (curr === "idle" || curr === "done")) {
        try { notifyFinished(agent.role); } catch (err) {
          log.warn({ event: "sound_failed", role: agent.role, err }, "sound dispatch failed");
        }
      }
    }
  }

  checkSocket(): void {
    if (this.view === "attached") return;
    const available = this.io.socketAvailable();
    if (available === this.lastSocketAvailable) return;
    this.lastSocketAvailable = available;
    if (!available) {
      this.view = "error";
      this.errorMessage = "The swarm's herdr session is unavailable.";
      this.io.log("socket_check", { status: "unavailable" });
      log.warn({ event: "socket_unavailable" }, "socket unavailable");
    } else {
      this.io.log("socket_check", { status: "available" });
      log.info({ event: "socket_available" }, "socket available");
      if (this.view === "error") this.view = "dashboard";
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
    this.logViewTransition();
  }

  poll(): void {
    void this.refreshHerdrAndAgents();
    this.checkSocket();
    this.checkSize();
  }

  private async refreshHerdrAndAgents(): Promise<void> {
    try {
      this.herdrAgents = await this.io.queryHerdrAgents();
    } catch (err) {
      log.warn({ event: "herdr_query_failed", err }, "herdr query threw");
      this.herdrAgents = [];
    }
    this.refreshAgents();
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
      case "esc":
        this.attachError = null;
        break;
      default:
        break;
    }
  }

  cycleFocus(): void {
    const index = FOCUS_ORDER.indexOf(this.focus);
    this.focus = FOCUS_ORDER[(index + 1) % FOCUS_ORDER.length];
    log.debug({ event: "focus_changed", focus: this.focus }, "focus cycled");
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
      log.debug({ event: "menu_disabled", item }, "menu item disabled");
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
    if (!agent) {
      log.warn({ event: "attach_no_agent", selection: this.selection }, "no agent at selection");
      return;
    }
    this.attachError = null;
    log.info(
      { event: "attach_start", role: agent.role, workspaceId: agent.workspaceId, socket: this.io.socketPath() },
      "attaching",
    );
    this.io.log("attach_start", { role: agent.role, workspaceId: agent.workspaceId, socket: this.io.socketPath() });
    this.beginAttach();
    let result: AttachResult;
    try {
      result = await this.io.attach(agent.workspaceId, this.io.socketPath());
    } catch (err) {
      log.error({ event: "attach_threw", workspaceId: agent.workspaceId, err }, "attach threw");
      this.resumeAfterDetach({ code: -1, reason: errMessage(err) });
      return;
    }
    const diagnosis = await diagnoseAttachEnd(result, agent.workspaceId, this.io);
    this.io.log("attach_end", {
      role: agent.role,
      workspaceId: agent.workspaceId,
      code: result.code,
      reason: diagnosis.reason,
      socket_available: diagnosis.socketAvailable,
      session_alive: diagnosis.sessionAlive,
    });
    log.info(
      {
        event: "attach_end",
        role: agent.role,
        workspaceId: agent.workspaceId,
        code: result.code,
        reason: diagnosis.reason,
        socket_available: diagnosis.socketAvailable,
        session_alive: diagnosis.sessionAlive,
      },
      "attach ended",
    );
    this.resumeAfterDetach({ code: result.code, reason: diagnosis.reason });
  }

  beginAttach(): void {
    this.view = "attached";
  }

  resumeAfterDetach(result: AttachResult): void {
    this.view = "dashboard";
    this.attachError = result.reason === "" ? null : result.reason;
    this.poll();
    this.logViewTransition();
  }

  quit(): void {
    log.info({ event: "tui_quit" }, "tui quitting");
    this.io.restore();
    this.io.quit();
  }

  private logViewTransition(): void {
    if (this.view !== this.lastView) {
      log.debug(
        { event: "view_changed", from: this.lastView, to: this.view },
        "view changed",
      );
      this.lastView = this.view;
    }
  }
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}