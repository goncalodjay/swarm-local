import type { FrameModel } from "../../src/render.ts";
import type { AgentState, Role } from "../../src/types.ts";

export const roles: Role[] = [
  { role: "specifier", worktreeName: "master", worktreePath: "/p", session: "swarmforge-specifier", displayName: "Specifier", agent: "opencode", receiveMode: "task" },
  { role: "coder", worktreeName: "coder", worktreePath: "/p", session: "swarmforge-coder", displayName: "Coder", agent: "opencode", receiveMode: "task" },
  { role: "reviewer", worktreeName: "reviewer", worktreePath: "/p", session: "swarmforge-reviewer", displayName: "Reviewer", agent: "codex", receiveMode: "task" },
  { role: "architect", worktreeName: "architect", worktreePath: "/p", session: "swarmforge-architect", displayName: "Architect", agent: "opencode", receiveMode: "batch" },
];

export function agent(role: Role, marker: AgentState["marker"], task: string | null): AgentState {
  return {
    role: role.role,
    displayName: role.displayName,
    session: role.session,
    status:
      marker === "spinner" ? "working"
      : marker === "bang" ? "needs-human"
      : marker === "dot" ? "finished-idle"
      : "idle",
    marker,
    task,
    handoffs: { queued: [], inProcess: [], completed: [], pendingUserNote: marker === "bang" },
    herdrStatus: null,
    terminalTitle: null,
  };
}

export function model(
  view: FrameModel["view"],
  agents: AgentState[],
  selection: number,
  overrides: Partial<FrameModel> = {},
): FrameModel {
  return {
    view,
    roles,
    agents,
    selection,
    errorMessage: "boom",
    attachError: null,
    terminalSize: { cols: 120, rows: 40 },
    requiredSize: { cols: 100, rows: 30 },
    socket: "/p/.swarmforge/swarm.sock",
    mode: "normal",
    focus: "menu",
    menuFocus: 0,
    hint: null,
    helpOpen: false,
    herdrMatched: 0,
    herdrTotal: agents.length,
    ...overrides,
  };
}

/** Agents for every role, with `spinner` on the one at `active`. */
export function agentsForRoles(active: number, task: string | null = null): AgentState[] {
  return roles.map((r, i) => agent(r, i === active ? "spinner" : "blank", i === active ? task : null));
}
