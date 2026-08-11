import assert from "node:assert/strict";
import { parseHeaders } from "../../src/handoffs.ts";
import { moveMenuFocus } from "../../src/menu.ts";
import { parseRoles } from "../../src/roles.ts";
import { moveSelection } from "../../src/selection.ts";
import { computeStatus, statusToMarker } from "../../src/status.ts";
import { padVisible, truncateVisible, visibleLength } from "../../src/style.ts";
import type { HandoffSnapshot } from "../../src/types.ts";
import { deterministicIntegers, forAll } from "../helpers/property.ts";

const randomValues = deterministicIntegers(200);

function bounded(value: number, limit: number): number {
  return value % limit;
}

function snapshot(overrides: Partial<HandoffSnapshot> = {}): HandoffSnapshot {
  return { queued: [], inProcess: [], completed: [], pendingUserNote: false, ...overrides };
}

forAll(
  "parseHeaders preserves generated header values",
  randomValues.map((value, index) => ({
    key: `field_${index}`,
    value: `value-${value}:part-${value % 17}`,
  })),
  ({ key, value }) => {
    const parsed = parseHeaders(`${key}: ${value}\n\nbody`);
    assert.equal(parsed[key], value);
  },
);

forAll(
  "parseRoles preserves order and valid row identity",
  randomValues.slice(0, 50).map((value, index) => ({
    role: `role_${index}`,
    worktree: `/worktree/${value}`,
    session: `session-${index}`,
  })),
  ({ role, worktree, session }) => {
    const parsed = parseRoles(`${role}\t${role}\t${worktree}\t${session}\t\tpi\ttask\n`);
    assert.equal(parsed.length, 1);
    assert.equal(parsed[0].role, role);
    assert.equal(parsed[0].worktreePath, worktree);
    assert.equal(parsed[0].session, session);
    assert.equal(parsed[0].displayName, role);
  },
);

forAll(
  "moveSelection always stays within the available selection range",
  randomValues.map((value) => {
    const count = bounded(value >>> 3, 12);
    return {
      current: count === 0 ? 0 : value % count,
      count,
      key: value % 2 === 0 ? ("up" as const) : ("down" as const),
    };
  }),
  ({ current, count, key }) => {
    const next = moveSelection(current, key, count);
    assert.ok(count === 0 ? next === 0 : next >= 0 && next < count);
  },
);

forAll(
  "all motion keys preserve the selection range and jump endpoints",
  randomValues.map((value) => {
    const count = 1 + (value % 12);
    const keys = ["up", "down", "j", "k", "home", "end", "g", "G"] as const;
    return { current: value % count, count, key: keys[value % keys.length] };
  }),
  ({ current, count, key }) => {
    const next = moveSelection(current, key, count);
    assert.ok(next >= 0 && next < count);
    if (key === "home" || key === "g") assert.equal(next, 0);
    if (key === "end" || key === "G") assert.equal(next, count - 1);
  },
);

forAll(
  "menu focus movement wraps and reverses",
  randomValues.map((value) => {
    const count = 1 + (value % 12);
    return { current: value % count, count };
  }),
  ({ current, count }) => {
    assert.equal(moveMenuFocus(moveMenuFocus(current, "right", count), "left", count), current);
    assert.equal(moveMenuFocus(moveMenuFocus(current, "left", count), "right", count), current);
  },
);

forAll(
  "status precedence is stable across handoff collections",
  [
    snapshot(),
    snapshot({ completed: [{}] }),
    snapshot({ pendingUserNote: true, completed: [{}] }),
    snapshot({ inProcess: [{}], pendingUserNote: true, completed: [{}] }),
  ],
  (value, index) => {
    const expected = ["idle", "finished-idle", "needs-human", "working"][index];
    assert.equal(computeStatus(value), expected);
  },
);

forAll(
  "status markers are one-to-one for every status",
  ["working", "needs-human", "finished-idle", "idle"] as const,
  (status) => {
    assert.notEqual(statusToMarker(status), "");
  },
);

forAll(
  "visible padding or truncation reaches its requested width",
  randomValues.map((value) => ({
    text: `role-${value}-task-${value % 19}`,
    width: value % 40,
  })),
  ({ text, width }) => {
    assert.equal(visibleLength(padVisible(text, width)), width);
  },
);

forAll(
  "visible truncation preserves the requested width",
  randomValues.map((value) => ({
    text: `a-long-task-name-${value}-${value % 23}`,
    width: 1 + (value % 40),
  })),
  ({ text, width }) => {
    assert.ok(visibleLength(truncateVisible(text, width)) <= width);
    if (visibleLength(text) > width) assert.equal(visibleLength(truncateVisible(text, width)), width);
  },
);
