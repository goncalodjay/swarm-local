import type { AgentState, HandoffSnapshot, Marker, Role, Status } from "./types.ts";

export function computeStatus(snapshot: HandoffSnapshot): Status {
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

export function agentState(role: Role, snapshot: HandoffSnapshot): AgentState {
  const status = computeStatus(snapshot);
  return {
    role: role.role,
    displayName: role.displayName,
    session: role.session,
    status,
    marker: statusToMarker(status),
    task: currentTask(snapshot),
    handoffs: snapshot,
  };
}
