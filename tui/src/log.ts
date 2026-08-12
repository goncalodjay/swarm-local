import { appendFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import type { LogFields } from "./types.ts";

export function logPathForRoot(root: string): string {
  return path.join(root, ".swarmforge", "logs", "tui.log");
}

export function formatLogLine(event: string, fields: LogFields): string {
  const timestamp = new Date().toISOString();
  const pairs = Object.entries(fields).map(([key, value]) => `${key}=${formatValue(value)}`);
  return [timestamp, event, ...pairs].join(" ");
}

function formatValue(value: string | number | null): string {
  if (value === null) return "null";
  if (typeof value === "number") return String(value);
  if (value.includes(" ") || value.includes("=")) return `"${value}"`;
  return value;
}

export function appendLogEntry(file: string, event: string, fields: LogFields): boolean {
  try {
    mkdirSync(path.dirname(file), { recursive: true });
    appendFileSync(file, formatLogLine(event, fields) + "\n");
    return true;
  } catch {
    return false;
  }
}
