import { readdirSync, readFileSync, existsSync } from "node:fs";
import path from "node:path";
import type { HandoffInfo, HandoffSnapshot } from "./types.ts";

const HANDOFF_STATES = ["new", "in_process", "completed"] as const;

export function parseHeaders(text: string): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const line of text.split("\n")) {
    if (line.trim() === "") break;
    const idx = line.indexOf(": ");
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 2).trim();
    if (key !== "") headers[key] = value;
  }
  return headers;
}

export function readHandoff(filePath: string): HandoffInfo {
  const headers = parseHeaders(readFileSync(filePath, "utf8"));
  return {
    task: headers.task ?? null,
    type: headers.type ?? null,
    created_at: headers.created_at ?? null,
    dequeued_at: headers.dequeued_at ?? null,
    completed_at: headers.completed_at ?? null,
  };
}

function listHandoffs(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((name) => name.endsWith(".handoff"))
    .sort()
    .map((name) => path.join(dir, name));
}

function hasPendingUserNote(inboxRoot: string): boolean {
  const toUserFiles = HANDOFF_STATES
    .map((sub) => listHandoffs(path.join(inboxRoot, sub)))
    .flat();
  for (const file of toUserFiles) {
    const text = readFileSync(file, "utf8");
    const headers = parseHeaders(text);
    if (headers.type === "note" && (headers.to ?? "").split(",").map((s) => s.trim()).includes("user")) {
      return true;
    }
  }
  return false;
}

export function readHandoffSnapshot(handoffsRoot: string): HandoffSnapshot {
  const inboxRoot = path.join(handoffsRoot, "inbox");
  const queued = listHandoffs(path.join(inboxRoot, "new")).map(readHandoff);
  const inProcess = listHandoffs(path.join(inboxRoot, "in_process")).map(readHandoff);
  const completed = listHandoffs(path.join(inboxRoot, "completed")).map(readHandoff);
  return {
    queued,
    inProcess,
    completed,
    pendingUserNote: hasPendingUserNote(inboxRoot),
  };
}
