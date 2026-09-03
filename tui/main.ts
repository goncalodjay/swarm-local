import "./src/instrumentation.ts";
import { startActiveObservation } from "@langfuse/tracing";
import { App } from "./src/app.ts";
import { FileSystemTuiIO, projectRoot, setStdinDataListener } from "./src/io.ts";
import { parseKey } from "./src/keys.ts";
import { frameModel, renderFrame } from "./src/render.ts";
import { init, child } from "./src/logger.ts";
import { alertNeedsHuman } from "./src/notifications.ts";
import { isTracingConfigured, shutdownTracing } from "./src/instrumentation.ts";

const POLL_INTERVAL_MS = 1000;

async function main(): Promise<void> {
  await startActiveObservation("tui.session", async (rootSpan) => {
    await runTui(rootSpan);
  }).finally(async () => {
    try {
      await shutdownTracing();
    } catch {
      /* swallow */
    }
  });
}

async function runTui(rootSpan: { update: (attrs: Record<string, unknown>) => void }): Promise<void> {
  let root: string;
  try {
    root = projectRoot(process.cwd());
  } catch (err) {
    process.stderr.write(
      `swarm-tui: ${err instanceof Error ? err.message : String(err)}\n`,
    );
    process.exit(1);
  }
  rootSpan.update({ input: { root }, metadata: { tracing_configured: isTracingConfigured() } });

  const logger = init({ root });
  const log = child("main");
  log.info({ event: "tui_starting", root }, "tui starting");

  const io = new FileSystemTuiIO(root);
  const app = new App(io);
  try {
    app.start();
  } catch (err) {
    log.fatal({ event: "tui_start_failed", err }, "tui failed to start");
    process.exit(1);
  }

  if (app.view === "error") {
    log.warn({ event: "tui_starting_in_error", message: app.errorMessage }, "starting in error view");
  }

  const render = (): void => {
    try {
      const frame = renderFrame(frameModel(app));
      process.stdout.write("\x1b[2J\x1b[H");
      process.stdout.write(frame.join("\n") + "\n");
    } catch (err) {
      log.error({ event: "render_failed", err }, "render failed");
    }
  };

  render();

  const alertedRoles = new Set<string>();
  const detectNeedsHumanTransitions = (): void => {
    for (const agent of app.agents) {
      const wantsHuman = agent.status === "needs-human";
      if (wantsHuman && !alertedRoles.has(agent.role)) {
        alertedRoles.add(agent.role);
        try {
          alertNeedsHuman(agent.role);
        } catch (err) {
          log.warn({ event: "alert_failed", role: agent.role, err }, "alert dispatch failed");
        }
      } else if (!wantsHuman) {
        alertedRoles.delete(agent.role);
      }
    }
  };

  const pollTimer = setInterval(() => {
    if (app.view === "attached") return;
    try {
      app.poll();
    } catch (err) {
      log.error({ event: "poll_failed", err }, "poll cycle failed");
    }
    detectNeedsHumanTransitions();
    render();
  }, POLL_INTERVAL_MS);

  if (process.stdin.isTTY) {
    try {
      process.stdin.setRawMode(true);
    } catch (err) {
      log.warn({ event: "raw_mode_failed", err }, "cannot enable raw mode");
    }
  }
  process.stdin.resume();
  process.stdin.setEncoding("utf8");

  const handleData = async (data: Buffer): Promise<void> => {
    const key = parseKey(data.toString("utf8"));
    if (!key) return;
    if (app.view === "attached") return;
    try {
      await app.press(key);
    } catch (err) {
      log.error({ event: "press_failed", key, err }, "press handler failed");
    }
    render();
  };

  setStdinDataListener((data: Buffer) => {
    void handleData(data);
  });

  process.stdout.write("\x1b[?5l");
  process.stdout.write("\x1b[2J\x1b[H");

  process.on("SIGWINCH", () => {
    log.debug({ event: "sigwinch", size: app.io.terminalSize() }, "terminal resized");
    if (app.view === "attached") return;
    try {
      app.checkSize();
      render();
    } catch (err) {
      log.error({ event: "resize_failed", err }, "resize handler failed");
    }
  });

  for (const sig of ["SIGINT", "SIGTERM"] as const) {
    process.on(sig, () => {
      log.info({ event: "tui_signal", signal: sig }, "tui received shutdown signal");
      try {
        app.io.restore();
      } catch (err) {
        log.warn({ event: "restore_on_signal_failed", err }, "restore on signal failed");
      }
      process.exit(0);
    });
  }

  process.on("uncaughtException", (err) => {
    log.fatal({ event: "uncaught_exception", err }, "uncaught exception");
    try {
      app.io.restore();
    } catch {
      /* swallow */
    }
    process.exit(1);
  });

  process.on("unhandledRejection", (reason) => {
    log.fatal({ event: "unhandled_rejection", reason }, "unhandled promise rejection");
    try {
      app.io.restore();
    } catch {
      /* swallow */
    }
    process.exit(1);
  });

  rootSpan.update({ output: { roles: app.roles.length, view: app.view } });
  log.info({ event: "tui_loop_entered" }, "tui loop entered");
}

void main();
