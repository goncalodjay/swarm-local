import type { AgentState, AppView, AttachResult, FocusTarget, HerdrAgent, HerdrStatus, HandoffSnapshot, Key, Lifecycle, Mode, Role, TerminalSize } from "./types.ts";
import { diagnoseAttachEnd } from "./attach.ts";
import { moveSelection } from "./selection.ts";
import { isDisabledMenuItem, menuItems, moveMenuFocus } from "./menu.ts";
import { agentState, isAttachable, unavailableMessage } from "./status.ts";
import { child } from "./logger.ts";
import { findAgentForCwd } from "./herdr.ts";
import { notifyBlocked, notifyFinished } from "./sound.ts";

const log = child("app");

export const REQUIRED_SIZE: TerminalSize = { cols: 80, rows: 24 };

/**
 * herdr states in which the first role's agent is ready for the user: idle
 * at its prompt, done, or blocked on a question to the human.
 */
const READY_HERDR_STATUSES = new Set(["idle", "done", "blocked"]);

/**
 * Open the dashboard anyway this long after the first role started running,
 * in case herdr cannot detect that backend's state at all.
 */
export const STARTUP_TIMEOUT_MS = 120_000;

const FOCUS_ORDER: FocusTarget[] = ["agents", "detail", "menu"];

export interface TuiIO {
  readRoles(): Role[];
  readSnapshot(role: Role): HandoffSnapshot;
  /** Role -> lifecycle from .swarmforge/agents.json; empty when absent. */
  readLifecycles(): Record<string, Lifecycle>;
  socketPath(): string;
  socketAvailable(): boolean;
  terminalSize(): TerminalSize;
  attach(session: string, socket: string): Promise<AttachResult>;
  sessionExists(session: string): Promise<boolean>;
  queryHerdrAgents(): Promise<HerdrAgent[]>;
  log(event: string, fields: Record<string, string | number | boolean | null>): void;
  restore(): void;
  quit(): void;
  /** Stop every agent, the scheduler and the swarm's herdr session. */
  stopSwarm(): Promise<void>;
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
  /**
   * The swarm is still coming up: nothing but quit is allowed until the
   * first role's agent is running and ready, so the user cannot attach to
   * or type into a half-started swarm. Cleared once, never set again.
   */
  starting = true;
  /** Quit was requested and the swarm is being torn down. */
  stopping = false;
  now: () => number = () => Date.now();
  io: TuiIO;
  private firstRoleRunningSince: number | null = null;
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
    const lifecycles = this.io.readLifecycles();
    this.refreshAgentStates(lifecycles);
    this.updateStarting(lifecycles);
    this.detectHerdrTransitions();
  }

  private refreshAgentStates(lifecycles: Record<string, Lifecycle>): void {
    this.agents = this.roles.map((role) => {
      const snapshot = this.io.readSnapshot(role);
      const herdrAgent = findAgentForCwd(this.herdrAgents, role.worktreePath);
      return agentState(
        role,
        snapshot,
        herdrAgent?.agent_status ?? null,
        herdrAgent?.terminal_title ?? null,
        lifecycles[role.role] ?? null,
      );
    });
  }

  private updateStarting(lifecycles: Record<string, Lifecycle>): void {
    if (!this.starting) return;
    const first = this.agents[0];
    // Only the first role's startup is waited on. Once it has run (parked
    // after forwarding work, or failed) the swarm is past startup, e.g. when
    // `./swarm tui` reattaches in the middle of a feature.
    let ready: boolean;
    if (Object.keys(lifecycles).length === 0 || !first || first.lifecycle === null) {
      ready = true; // a swarm without the scheduler has nothing to wait for
    } else if (first.lifecycle === "starting") {
      ready = false;
    } else if (first.lifecycle === "running") {
      this.firstRoleRunningSince ??= this.now();
      ready =
        READY_HERDR_STATUSES.has(first.herdrStatus ?? "") ||
        this.now() - this.firstRoleRunningSince >= STARTUP_TIMEOUT_MS;
    } else {
      ready = true; // parked or failed: already past startup
    }
    if (ready) {
      this.starting = false;
      this.io.log("swarm_ready", { role: first?.role ?? null, herdr: first?.herdrStatus ?? null });
      log.info({ event: "swarm_ready" }, "swarm ready");
    }
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
    if (this.stopping) return;
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
    if (this.view === "attached" || this.stopping) return;
    this.hint = null;
    if (this.starting) {
      if (key === "quit") await this.quit();
      else if (key === "ctrl+k") this.mode = "prefix";
      else if (this.mode === "prefix") this.mode = "normal";
      return;
    }
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
          await this.quit();
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
        await this.quit();
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
    if (!isAttachable(agent)) {
      // A parked role's pane is an empty shell: handing the terminal to it
      // would only show a prompt. Say so instead of attaching.
      this.attachError = unavailableMessage(agent);
      this.io.log("attach_unavailable", { role: agent.role, lifecycle: agent.lifecycle });
      log.info({ event: "attach_unavailable", role: agent.role, lifecycle: agent.lifecycle }, "agent not running");
      return;
    }
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

  /**
   * Quitting the TUI ends the swarm: every agent, the scheduler and the
   * herdr session are stopped so nothing keeps holding memory. Sessions are
   * kept, so the next `./swarm` resumes them.
   */
  async quit(): Promise<void> {
    if (this.stopping) return;
    this.stopping = true;
    log.info({ event: "tui_quit" }, "tui quitting; stopping swarm");
    this.io.log("swarm_stop", {});
    try {
      await this.io.stopSwarm();
    } catch (err) {
      log.error({ event: "swarm_stop_failed", err }, "stopping the swarm failed");
    }
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