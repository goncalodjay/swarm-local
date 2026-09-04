import "./src/instrumentation.ts";
import { startActiveObservation } from "@langfuse/tracing";
import { createCliRenderer, type CliRenderer, type ParsedKey } from "@opentui/core";
import { App } from "./src/app.ts";
import { FileSystemTuiIO, projectRoot, setExitHandler, setTerminalControl } from "./src/io.ts";
import { keyFromParsed } from "./src/keys.ts";
import { frameModel } from "./src/render.ts";
import { selectTheme } from "./src/theme.ts";
import { FrameMount } from "./src/ui/dashboard.ts";
import { init, child } from "./src/logger.ts";
import { alertNeedsHuman } from "./src/notifications.ts";
import { isTracingConfigured, shutdownTracing } from "./src/instrumentation.ts";

const POLL_INTERVAL_MS = 1000;

/**
 * Frames per second the renderer targets.
 *
 * The dashboard only changes on a poll or a keypress, so a low cap keeps
 * the process idle instead of busy-redrawing a static screen.
 */
const TARGET_FPS = 30;

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

  init({ root });
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

  const theme = selectTheme(process.env);
  let renderer: CliRenderer;
  try {
    renderer = await createCliRenderer({
      targetFps: TARGET_FPS,
      // The app owns quitting so it can restore the terminal and flush
      // traces first.
      exitOnCtrlC: false,
    });
  } catch (err) {
    log.fatal({ event: "renderer_start_failed", err }, "cannot start renderer");
    process.stderr.write(
      `swarm-tui: cannot start renderer: ${err instanceof Error ? err.message : String(err)}\n`,
    );
    process.exit(1);
  }
  renderer.setBackgroundColor(theme.bgBase);

  const mount = new FrameMount(renderer, theme);
  const render = (): void => {
    try {
      mount.update(frameModel(app));
      renderer.requestRender();
    } catch (err) {
      log.error({ event: "render_failed", err }, "render failed");
    }
  };

  // Attaching to tmux gives the terminal to a child process, so the
  // renderer must let go of it first and take it back afterwards.
  setTerminalControl({
    release: () => {
      try {
        renderer.stop();
      } catch (err) {
        log.warn({ event: "renderer_stop_failed", err }, "cannot stop renderer for attach");
      }
    },
    reclaim: () => {
      try {
        renderer.start();
        render();
      } catch (err) {
        log.warn({ event: "renderer_start_failed", err }, "cannot restart renderer after attach");
      }
    },
  });

  render();
  renderer.start();

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
  // A pending poll must never hold the process open on its own.
  pollTimer.unref?.();

  renderer.keyInput.on("keypress", (parsed: ParsedKey) => {
    if (app.view === "attached") return;
    const key = keyFromParsed(parsed);
    if (!key) return;
    void (async () => {
      try {
        await app.press(key);
      } catch (err) {
        log.error({ event: "press_failed", key, err }, "press handler failed");
      }
      render();
    })();
  });

  renderer.on("resize", (width: number, height: number) => {
    log.debug({ event: "resize", width, height }, "terminal resized");
    if (app.view === "attached") return;
    try {
      app.checkSize();
      render();
    } catch (err) {
      log.error({ event: "resize_failed", err }, "resize handler failed");
    }
  });

  const shutdown = (code: number): never => {
    clearInterval(pollTimer);
    try {
      mount.destroy();
      renderer.destroy();
    } catch (err) {
      log.warn({ event: "renderer_destroy_failed", err }, "renderer destroy failed");
    }
    try {
      app.io.restore();
    } catch (err) {
      log.warn({ event: "restore_failed", err }, "restore failed");
    }
    process.exit(code);
  };

  // `q` and the menu quit path both land here, so the renderer is always
  // torn down before the process leaves.
  setExitHandler(shutdown);

  for (const sig of ["SIGINT", "SIGTERM"] as const) {
    process.on(sig, () => {
      log.info({ event: "tui_signal", signal: sig }, "tui received shutdown signal");
      shutdown(0);
    });
  }

  process.on("uncaughtException", (err) => {
    log.fatal({ event: "uncaught_exception", err }, "uncaught exception");
    shutdown(1);
  });

  process.on("unhandledRejection", (reason) => {
    log.fatal({ event: "unhandled_rejection", reason }, "unhandled promise rejection");
    shutdown(1);
  });

  rootSpan.update({ output: { roles: app.roles.length, view: app.view } });
  log.info({ event: "tui_loop_entered" }, "tui loop entered");
}

void main();
