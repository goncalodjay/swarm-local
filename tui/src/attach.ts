import type { AttachDiagnosis, AttachResult } from "./types.ts";

export interface AttachDiagnosisIO {
  socketPath(): string;
  socketAvailable(): boolean;
  sessionExists(session: string): Promise<boolean>;
}

export async function diagnoseAttachEnd(
  result: AttachResult,
  session: string,
  io: AttachDiagnosisIO,
): Promise<AttachDiagnosis> {
  if (result.reason !== "" || result.code === 0) {
    return { reason: result.reason, socketAvailable: null, sessionAlive: null };
  }

  if (!io.socketAvailable()) {
    return {
      reason: `tmux socket ${io.socketPath()} is unavailable`,
      socketAvailable: false,
      sessionAlive: false,
    };
  }

  try {
    const sessionAlive = await io.sessionExists(session);
    if (!sessionAlive) {
      return {
        reason: `tmux session ${session} no longer exists`,
        socketAvailable: true,
        sessionAlive: false,
      };
    }
    return {
      reason: `tmux client exited with status ${result.code ?? "unknown"}`,
      socketAvailable: true,
      sessionAlive: true,
    };
  } catch (error) {
    return {
      reason: error instanceof Error ? error.message : String(error),
      socketAvailable: null,
      sessionAlive: null,
    };
  }
}
