import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { parseHeaders, readHandoffSnapshot } from "../src/handoffs.ts";

function makeRoot(): string {
  const root = mkdtempSync(path.join(os.tmpdir(), "handoffs-test-"));
  return root;
}

function handoffsDir(root: string): string {
  const dir = path.join(root, ".swarmforge", "handoffs", "inbox");
  for (const sub of ["new", "in_process", "completed"]) {
    mkdirSync(path.join(dir, sub), { recursive: true });
  }
  return dir;
}

function writeHandoff(root: string, sub: string, name: string, headers: Record<string, string>): void {
  const dir = path.join(root, ".swarmforge", "handoffs", "inbox", sub);
  mkdirSync(dir, { recursive: true });
  const lines = Object.entries(headers).map(([k, v]) => `${k}: ${v}`);
  writeFileSync(path.join(dir, name), lines.join("\n") + "\n\nbody\n");
}

test("parseHeaders extracts RFC822-style headers", () => {
  const headers = parseHeaders("task: fix-login\npriority: 50\ncreated_at: 2026-01-01T00:00:00Z\n\nbody");
  assert.equal(headers.task, "fix-login");
  assert.equal(headers.created_at, "2026-01-01T00:00:00Z");
});

test("readHandoffSnapshot classifies handoff dirs", () => {
  const root = makeRoot();
  handoffsDir(root);
  writeHandoff(root, "new", "a.handoff", { task: "queued-task", type: "git_handoff" });
  writeHandoff(root, "in_process", "b.handoff", { task: "active-task", type: "git_handoff" });
  writeHandoff(root, "completed", "c.handoff", { task: "done-task", type: "git_handoff" });
  const snapshot = readHandoffSnapshot(path.join(root, ".swarmforge", "handoffs"));
  assert.equal(snapshot.queued.length, 1);
  assert.equal(snapshot.queued[0].task, "queued-task");
  assert.equal(snapshot.inProcess.length, 1);
  assert.equal(snapshot.inProcess[0].task, "active-task");
  assert.equal(snapshot.completed.length, 1);
  assert.equal(snapshot.completed[0].task, "done-task");
  rmSync(root, { recursive: true, force: true });
});

test("readHandoffSnapshot detects pending user note", () => {
  const root = makeRoot();
  handoffsDir(root);
  writeHandoff(root, "new", "note.handoff", { type: "note", to: "user" });
  const snapshot = readHandoffSnapshot(path.join(root, ".swarmforge", "handoffs"));
  assert.equal(snapshot.pendingUserNote, true);
  rmSync(root, { recursive: true, force: true });
});

test("readHandoffSnapshot does not flag notes to roles", () => {
  const root = makeRoot();
  handoffsDir(root);
  writeHandoff(root, "new", "note.handoff", { type: "note", to: "coder" });
  const snapshot = readHandoffSnapshot(path.join(root, ".swarmforge", "handoffs"));
  assert.equal(snapshot.pendingUserNote, false);
  rmSync(root, { recursive: true, force: true });
});

test("readHandoffSnapshot handles missing dirs", () => {
  const root = makeRoot();
  const snapshot = readHandoffSnapshot(path.join(root, ".swarmforge", "handoffs"));
  assert.deepEqual(snapshot.queued, []);
  assert.deepEqual(snapshot.inProcess, []);
  assert.deepEqual(snapshot.completed, []);
  assert.equal(snapshot.pendingUserNote, false);
  rmSync(root, { recursive: true, force: true });
});
