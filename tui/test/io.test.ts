import { test } from "node:test";
import assert from "node:assert/strict";
import { NESTED_HERDR_HINT, nestedHerdrBlocked } from "../src/io.ts";

const refused = () => "error: nested herdr is disabled by default.\nsee configuration if you want to enable it.";
const noTerminal = () => "herdr: cannot attach without a usable terminal";

test("outside herdr the attach is never probed", () => {
  let probed = false;
  const result = nestedHerdrBlocked("s", {}, () => {
    probed = true;
    return refused();
  });
  assert.equal(result, null);
  assert.equal(probed, false);
});

test("inside herdr a refused nested client yields the allow_nested hint", () => {
  assert.equal(nestedHerdrBlocked("s", { HERDR_ENV: "1" }, refused), NESTED_HERDR_HINT);
});

test("inside herdr with nesting allowed the attach proceeds", () => {
  assert.equal(nestedHerdrBlocked("s", { HERDR_ENV: "1" }, noTerminal), null);
});
