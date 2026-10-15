import type { AgentState, HerdrStatus, HandoffSnapshot, Lifecycle, Marker, Role, Status } from "./types.ts";
import { mapHerdrStatusToTui } from "./herdr.ts";

export function computeStatus(snapshot: HandoffSnapshot, herdr: HerdrStatus | null): Status {
  if (herdr === "working") return "working";
  if (herdr === "blocked") return "needs-human";
  if (herdr === "done" && snapshot.completed.length > 0) return "finished-idle";
  if (snapshot.inProcess.length > 0) return "working";
  if (snapshot.pendingUserNote) return "needs-human";
  if (snapshot.completed.length > 0) return "finished-idle";
  return "idle";
}

export function statusToMarker(status: Status): Marker {
  switch (status) {
    case "working":
      return "spinner";
    case "needs-human":
      return "bang";
    case "finished-idle":
      return "dot";
    default:
      return "blank";
  }
}

export function currentTask(snapshot: HandoffSnapshot): string | null {
  return snapshot.inProcess[0]?.task ?? null;
}

export function agentState(
  role: Role,
  snapshot: HandoffSnapshot,
  herdr: HerdrStatus | null = null,
  terminalTitle: string | null = null,
  lifecycle: Lifecycle | null = null,
): AgentState {
  const status = computeStatus(snapshot, herdr);
  return {
    role: role.role,
    displayName: role.displayName,
    session: role.session,
    workspaceId: role.workspaceId,
    status,
    marker: statusToMarker(status),
    task: currentTask(snapshot),
    handoffs: snapshot,
    herdrStatus: herdr,
    terminalTitle,
    lifecycle,
  };
}

/** True when there is an agent process to attach to. */
export function isAttachable(agent: AgentState): boolean {
  return agent.lifecycle === null || agent.lifecycle === "running";
}

export function lifecycleLabel(lifecycle: Lifecycle): string {
  switch (lifecycle) {
    case "running":
      return "running";
    case "starting":
      return "starting";
    case "parked":
      return "parked";
    default:
      return "failed";
  }
}

/** Shown when Enter is pressed on a role whose agent is not running. */
export function unavailableMessage(agent: AgentState): string {
  const why = agent.lifecycle === null ? "" : ` (${lifecycleLabel(agent.lifecycle)})`;
  return `${agent.displayName} is not available at the moment${why}`;
}

export function mapHerdrStatus(herdr: HerdrStatus | null): Status | null {
  return herdr ? mapHerdrStatusToTui(herdr) : null;
}
