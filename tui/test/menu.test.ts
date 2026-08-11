import { test } from "node:test";
import assert from "node:assert/strict";
import { isDisabledMenuItem, menuItemIndex, menuItems, moveMenuFocus } from "../src/menu.ts";
import type { Role } from "../src/types.ts";

const roles: Role[] = [
  { role: "specifier", worktreeName: "master", worktreePath: "/p", session: "s", displayName: "Specifier", agent: "opencode", receiveMode: "task" },
  { role: "coder", worktreeName: "coder", worktreePath: "/p", session: "s", displayName: "Coder", agent: "opencode", receiveMode: "task" },
];

test("menuItems lists dashboard, roles, logs and costs", () => {
  assert.deepEqual(menuItems(roles), ["dashboard", "specifier", "coder", "logs", "costs"]);
});

test("isDisabledMenuItem flags logs and costs", () => {
  assert.equal(isDisabledMenuItem("logs"), true);
  assert.equal(isDisabledMenuItem("costs"), true);
  assert.equal(isDisabledMenuItem("dashboard"), false);
  assert.equal(isDisabledMenuItem("coder"), false);
});

test("moveMenuFocus moves right and wraps to the first item", () => {
  assert.equal(moveMenuFocus(0, "right", 5), 1);
  assert.equal(moveMenuFocus(4, "right", 5), 0);
});

test("moveMenuFocus moves left and wraps to the last item", () => {
  assert.equal(moveMenuFocus(1, "left", 5), 0);
  assert.equal(moveMenuFocus(0, "left", 5), 4);
});

test("moveMenuFocus with zero items stays at zero", () => {
  assert.equal(moveMenuFocus(0, "right", 0), 0);
});

test("menuItemIndex finds the item or falls back to the first", () => {
  assert.equal(menuItemIndex(menuItems(roles), "costs"), 4);
  assert.equal(menuItemIndex(menuItems(roles), "missing"), 0);
});
