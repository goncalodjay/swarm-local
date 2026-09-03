import { spawn } from "node:child_process";
import { startObservation } from "@langfuse/tracing";
import { child } from "./logger.ts";
import { isTracingConfigured } from "./instrumentation.ts";

const log = child("herdr");

export type HerdrStatus = "working" | "idle" | "blocked" | "done" | "unknown";

export interface HerdrAgent {
  agent: string;
  cwd: string;
  agent_status: HerdrStatus;
  terminal_title: string | null;
  terminal_title_stripped: string | null;
  pane_id: string;
  tab_id: string;
  workspace_id: string;
}

interface HerdrListResponse {
  result?: { agents?: HerdrAgent[] };
}

const DEFAULT_TIMEOUT_MS = 800;

async function queryHerdrAgentsInternal(timeoutMs: number): Promise<HerdrAgent[]> {
  return new Promise((resolve) => {
    let proc: ReturnType<typeof spawn>;
    try {
      proc = spawn("herdr", ["agent", "list"], { stdio: ["ignore", "pipe", "pipe"] });
    } catch (err) {
      log.warn({ event: "herdr_spawn_failed", err: errMessage(err) }, "cannot spawn herdr");
      resolve([]);
      return;
    }
    let stdout = "";
    let settled = false;
    const finish = (agents: HerdrAgent[]): void => {
      if (settled) return;
      settled = true;
      resolve(agents);
    };
    const timer = setTimeout(() => {
      try { proc.kill(); } catch { /* swallow */ }
      log.warn({ event: "herdr_query_timeout", timeoutMs }, "herdr query timeout");
      finish([]);
    }, timeoutMs);
    proc.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    proc.stderr.on("data", (chunk: Buffer) => {
      log.debug({ event: "herdr_stderr", chunk: chunk.toString("utf8").slice(0, 200) }, "herdr stderr");
    });
    proc.on("error", (err) => {
      clearTimeout(timer);
      log.warn({ event: "herdr_spawn_error", err: errMessage(err) }, "herdr spawn error");
      finish([]);
    });
    proc.on("exit", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        log.debug({ event: "herdr_exit_nonzero", code }, "herdr exited non-zero");
        return finish([]);
      }
      try {
        const parsed = JSON.parse(stdout) as HerdrListResponse;
        const agents = parsed.result?.agents ?? [];
        log.debug({ event: "herdr_query_ok", count: agents.length }, "herdr query ok");
        finish(agents);
      } catch (err) {
        log.warn({ event: "herdr_parse_failed", err: errMessage(err) }, "cannot parse herdr json");
        finish([]);
      }
    });
  });
}

export async function queryHerdrAgents(timeoutMs = DEFAULT_TIMEOUT_MS): Promise<HerdrAgent[]> {
  if (!isTracingConfigured()) {
    return queryHerdrAgentsInternal(timeoutMs);
  }
  const span = startObservation(
    "herdr.query_agents",
    { input: { timeout_ms: timeoutMs } },
    { asType: "tool" },
  );
  try {
    const agents = await queryHerdrAgentsInternal(timeoutMs);
    span.update({ output: { count: agents.length, statuses: agents.map(a => a.agent_status) } });
    return agents;
  } catch (err) {
    span.update({ output: { error: errMessage(err) }, level: "ERROR" });
    throw err;
  } finally {
    span.end();
  }
}

export function findAgentForCwd(agents: HerdrAgent[], cwd: string): HerdrAgent | null {
  return agents.find((a) => a.cwd === cwd) ?? null;
}

export function mapHerdrStatusToTui(status: HerdrStatus): "working" | "needs-human" | "finished-idle" | "idle" {
  switch (status) {
    case "working": return "working";
    case "blocked": return "needs-human";
    case "done": return "finished-idle";
    case "idle":
    case "unknown":
    default:
      return "idle";
  }
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

