import type { AttachDiagnosis, AttachResult } from "./types.ts";
import { child } from "./logger.ts";

const log = child("attach");

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
    const diagnosis: AttachDiagnosis = { reason: result.reason, socketAvailable: null, sessionAlive: null };
    log.debug(
      { event: "attach_diagnosis", session, ...diagnosis },
      "attach ended cleanly",
    );
    return diagnosis;
  }

  let socketAvailable: boolean;
  try {
    socketAvailable = io.socketAvailable();
  } catch (err) {
    log.warn({ event: "socket_check_failed", session, err }, "socket check raised");
    return {
      reason: err instanceof Error ? err.message : String(err),
      socketAvailable: null,
      sessionAlive: null,
    };
  }

  if (!socketAvailable) {
    const diagnosis: AttachDiagnosis = {
      reason: `tmux socket ${io.socketPath()} is unavailable`,
      socketAvailable: false,
      sessionAlive: false,
    };
    log.warn({ event: "attach_socket_unavailable", session, ...diagnosis }, "socket unavailable");
    return diagnosis;
  }

  let sessionAlive: boolean;
  try {
    sessionAlive = await io.sessionExists(session);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    log.warn({ event: "session_exists_failed", session, err: message }, "session check raised");
    return { reason: message, socketAvailable: null, sessionAlive: null };
  }

  if (!sessionAlive) {
    const diagnosis: AttachDiagnosis = {
      reason: `tmux session ${session} no longer exists`,
      socketAvailable: true,
      sessionAlive: false,
    };
    log.warn({ event: "attach_session_gone", session, ...diagnosis }, "session gone");
    return diagnosis;
  }

  const diagnosis: AttachDiagnosis = {
    reason: `tmux client exited with status ${result.code ?? "unknown"}`,
    socketAvailable: true,
    sessionAlive: true,
  };
  log.warn({ event: "attach_client_exit", session, ...diagnosis }, "client exited unexpectedly");
  return diagnosis;
}