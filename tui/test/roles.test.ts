import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRoles } from "../src/roles.ts";

const SAMPLE = [
  "specifier\tmaster\t/home/p/spec\t.swarmforge-specifier\tSpecifier\topencode\ttask",
  "coder\tcoder\t/home/p/.worktrees/coder\tswarmforge-coder\tCoder\topencode\ttask",
  "reviewer\treviewer\t/home/p/.worktrees/reviewer\tswarmforge-reviewer\tReviewer\tcodex\ttask",
  "architect\tarchitect\t/home/p/.worktrees/architect\tswarmforge-architect\tArchitect\topencode\tbatch",
].join("\n") + "\n";

test("parseRoles keeps configured role order", () => {
  const roles = parseRoles(SAMPLE);
  assert.deepEqual(roles.map((r) => r.role), ["specifier", "coder", "reviewer", "architect"]);
});

test("parseRoles reads session and display name", () => {
  const roles = parseRoles(SAMPLE);
  assert.equal(roles[1].session, "swarmforge-coder");
  assert.equal(roles[1].displayName, "Coder");
});

test("parseRoles ignores blank and comment lines", () => {
  const roles = parseRoles("# comment\n\n" + SAMPLE + "\n");
  assert.equal(roles.length, 4);
});

test("parseRoles defaults display name to role", () => {
  const roles = parseRoles("coder\tcoder\t/home/p\tswarmforge-coder\t\ttask");
  assert.equal(roles[0].displayName, "coder");
});
