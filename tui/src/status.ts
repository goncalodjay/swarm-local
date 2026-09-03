import type { AgentState, HerdrStatus, HandoffSnapshot, Marker, Role, Status } from "./types.ts";
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
): AgentState {
  const status = computeStatus(snapshot, herdr);
  return {
    role: role.role,
    displayName: role.displayName,
    session: role.session,
    status,
    marker: statusToMarker(status),
    task: currentTask(snapshot),
    handoffs: snapshot,
    herdrStatus: herdr,
    terminalTitle,
  };
}

export function mapHerdrStatus(herdr: HerdrStatus | null): Status | null {
  return herdr ? mapHerdrStatusToTui(herdr) : null;
}
