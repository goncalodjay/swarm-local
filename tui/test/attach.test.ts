import { test } from "node:test";
import assert from "node:assert/strict";
import { diagnoseAttachEnd } from "../src/attach.ts";
import type { AttachResult } from "../src/types.ts";

const session = "swarmforge-coder";

function result(code: number | null, reason = ""): AttachResult {
  return { code, reason };
}

function io(overrides: Partial<{
  socketPath: string;
  socketAvailable: boolean;
  sessionAlive: boolean;
  sessionError: unknown;
}> = {}) {
  return {
    socketPath: () => overrides.socketPath ?? "/tmp/swarmforge/test.sock",
    socketAvailable: () => overrides.socketAvailable ?? true,
    sessionExists: async () => {
      if ("sessionError" in overrides) throw overrides.sessionError;
      return overrides.sessionAlive ?? true;
    },
  };
}

test("diagnosis preserves clean and explicit attach reasons", async () => {
  assert.deepEqual(await diagnoseAttachEnd(result(0), session, io()), {
    reason: "",
    socketAvailable: null,
    sessionAlive: null,
  });
  assert.deepEqual(await diagnoseAttachEnd(result(1, "tmux: permission denied"), session, io()), {
    reason: "tmux: permission denied",
    socketAvailable: null,
    sessionAlive: null,
  });
});

test("diagnosis reports an unavailable socket", async () => {
  assert.deepEqual(
    await diagnoseAttachEnd(result(1), session, io({ socketAvailable: false })),
    {
      reason: "tmux socket /tmp/swarmforge/test.sock is unavailable",
      socketAvailable: false,
      sessionAlive: false,
    },
  );
});

test("diagnosis reports whether the session remains alive", async () => {
  assert.match(
    (await diagnoseAttachEnd(result(1), session, io({ sessionAlive: false }))).reason,
    /session swarmforge-coder no longer exists/,
  );
  assert.equal((await diagnoseAttachEnd(result(1), session, io())).reason, "tmux client exited with status 1");
});

test("diagnosis preserves unknown failures from the session check", async () => {
  const diagnosis = await diagnoseAttachEnd(result(1), session, io({ sessionError: "session check failed" }));
  assert.deepEqual(diagnosis, {
    reason: "session check failed",
    socketAvailable: null,
    sessionAlive: null,
  });
});
