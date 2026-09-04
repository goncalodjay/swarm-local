import { mkdirSync } from "node:fs";
import path from "node:path";
import { pino, type DestinationStream, type Logger, type LoggerOptions } from "pino";

export interface TuiLoggerOptions {
  root: string;
  level?: string;
}

let _logger: Logger | null = null;

export function init(options: TuiLoggerOptions): Logger {
  const logDir = path.join(options.root, ".swarmforge", "logs");
  const logFile = path.join(logDir, "tui.log");
  const level = options.level ?? process.env.LOG_LEVEL ?? "info";

  const config: LoggerOptions = {
    level,
    base: { component: "swarm-tui", pid: process.pid },
    timestamp: pino.stdTimeFunctions.isoTime,
    formatters: {
      level: (label) => ({ level: label }),
    },
  };

  const destination = tryDestination(logFile, logDir);
  _logger = destination ? pino(config, destination) : pino(config);
  return _logger;
}

export function get(): Logger {
  if (!_logger) {
    _logger = init({ root: process.cwd() });
  }
  return _logger;
}

export function child(component: string): Logger {
  return get().child({ subcomponent: component });
}

export function level(): string {
  return get().level;
}

function tryDestination(logFile: string, logDir: string): DestinationStream | null {
  try {
    mkdirSync(logDir, { recursive: true });
  } catch (err) {
    process.stderr.write(
      `swarm-tui: warning: cannot create log directory ${logDir}: ${formatErr(err)}\n`,
    );
    return null;
  }
  try {
    return pino.destination({ dest: logFile, sync: true, mkdir: false });
  } catch (err) {
    process.stderr.write(
      `swarm-tui: warning: cannot open log file ${logFile}: ${formatErr(err)}\n`,
    );
    return null;
  }
}

function formatErr(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}