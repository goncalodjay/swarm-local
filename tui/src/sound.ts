import { spawn } from "node:child_process";
import { startObservation } from "@langfuse/tracing";
import { child } from "./logger.ts";
import { isTracingConfigured } from "./instrumentation.ts";

const log = child("sound");

export type HerdrSound = "none" | "done" | "request";

export function playHerdrSound(sound: HerdrSound, title: string, body: string): void {
  if (sound === "none") return;
  if (process.env.SWARMTUI_SOUND === "0") {
    log.debug({ event: "sound_skipped", reason: "disabled" }, "sound disabled by env");
    return;
  }
  const traced = isTracingConfigured();
  const span = traced
    ? startObservation(
        "sound.play",
        { input: { sound, title, body }, metadata: { kind: "herdr_notification" } },
        { asType: "event" },
      )
    : null;
  let proc: ReturnType<typeof spawn>;
  try {
    proc = spawn(
      "herdr",
      ["notification", "show", title, "--body", body, "--sound", sound],
      { stdio: ["ignore", "ignore", "pipe"], detached: true },
    );
  } catch (err) {
    log.warn({ event: "sound_spawn_failed", sound, err: errMessage(err) }, "cannot spawn herdr notification");
    span?.update({ output: { error: errMessage(err) }, level: "ERROR" });
    span?.end();
    return;
  }
  proc.on("error", (err) => {
    log.warn({ event: "sound_spawn_error", sound, err: errMessage(err) }, "herdr notification error");
    span?.update({ output: { error: errMessage(err) }, level: "ERROR" });
    span?.end();
  });
  proc.on("spawn", () => {
    span?.update({ output: { status: "spawned" } });
    span?.end();
  });
  proc.unref();
}

export function notifyBlocked(role: string): void {
  log.info({ event: "sound_blocked", role }, "agent blocked, playing request sound");
  playHerdrSound("request", "swarm-tui", `${role} is blocked and waiting for input`);
}

export function notifyFinished(role: string): void {
  log.info({ event: "sound_finished", role }, "agent finished work, playing done sound");
  playHerdrSound("done", "swarm-tui", `${role} finished its current task`);
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
