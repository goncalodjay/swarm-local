import { test } from "node:test";
import assert from "node:assert/strict";
import { computeStatus, statusToMarker, currentTask, agentState } from "../src/status.ts";
import type { HandoffSnapshot, Role } from "../src/types.ts";

function snapshot(overrides: Partial<HandoffSnapshot> = {}): HandoffSnapshot {
  return {
    queued: [],
    inProcess: [],
    completed: [],
    pendingUserNote: false,
    ...overrides,
  };
}

const role: Role = {
  role: "coder",
  worktreeName: "coder",
  worktreePath: "/home/p/.worktrees/coder",
  session: "swarmforge-coder",
  workspaceId: "w2",
  displayName: "Coder",
  agent: "opencode",
  receiveMode: "task",
};

test("in-process handoff yields working status", () => {
  assert.equal(computeStatus(snapshot({ inProcess: [{ task: "fix-login", type: "git_handoff", created_at: "x", dequeued_at: "y", completed_at: null }] })), "working");
});

test("pending user note yields needs-human status", () => {
  assert.equal(computeStatus(snapshot({ pendingUserNote: true })), "needs-human");
});

test("completed tasks with nothing pending yields finished-idle", () => {
  assert.equal(computeStatus(snapshot({ completed: [{ task: "x", type: "git_handoff", created_at: "x", dequeued_at: "y", completed_at: "z" }] })), "finished-idle");
});

test("nothing present yields idle", () => {
  assert.equal(computeStatus(snapshot()), "idle");
});

test("statusToMarker maps each status", () => {
  assert.equal(statusToMarker("working"), "spinner");
  assert.equal(statusToMarker("needs-human"), "bang");
  assert.equal(statusToMarker("finished-idle"), "dot");
  assert.equal(statusToMarker("idle"), "blank");
});

test("currentTask reads in-process task", () => {
  const s = snapshot({ inProcess: [{ task: "fix-login", type: "git_handoff", created_at: "x", dequeued_at: "y", completed_at: null }] });
  assert.equal(currentTask(s), "fix-login");
});

test("currentTask returns null when idle", () => {
  assert.equal(currentTask(snapshot()), null);
});

test("agentState combines role and snapshot", () => {
  const state = agentState(role, snapshot({ inProcess: [{ task: "fix-login", type: "git_handoff", created_at: "x", dequeued_at: "y", completed_at: null }] }));
  assert.equal(state.role, "coder");
  assert.equal(state.status, "working");
  assert.equal(state.marker, "spinner");
  assert.equal(state.task, "fix-login");
});
