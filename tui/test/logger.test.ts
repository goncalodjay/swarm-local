import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { init, child } from "../src/logger.ts";

function tempRoot(): string {
  return mkdtempSync(path.join(os.tmpdir(), "tui-logger-"));
}

test("init creates the log directory and writes a JSON line per event", () => {
  const root = tempRoot();
  try {
    const logger = init({ root });
    logger.info({ event: "tui_start", roles: 4 }, "starting");
    logger.warn({ event: "socket_unavailable" }, "no socket");
    logger.error({ event: "snapshot_read_failed", role: "coder" }, "missing");

    const logFile = path.join(root, ".swarmforge", "logs", "tui.log");
    assert.ok(existsSync(logFile), "log file was created");
    const lines = readFileSync(logFile, "utf8").trim().split("\n");
    assert.equal(lines.length, 3);

    const first = JSON.parse(lines[0]);
    assert.equal(first.level, "info");
    assert.equal(first.event, "tui_start");
    assert.equal(first.roles, 4);
    assert.equal(first.component, "swarm-tui");
    assert.equal(first.msg, "starting");
    assert.match(first.time, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    assert.equal(typeof first.pid, "number");

    const second = JSON.parse(lines[1]);
    assert.equal(second.level, "warn");
    assert.equal(second.event, "socket_unavailable");

    const third = JSON.parse(lines[2]);
    assert.equal(third.level, "error");
    assert.equal(third.event, "snapshot_read_failed");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("child loggers add a subcomponent tag without overriding the global component", () => {
  const root = tempRoot();
  try {
    init({ root });
    const log = child("io");
    log.warn({ event: "socket_read_failed", file: "/tmp/x" }, "msg");
    log.debug({ event: "focus_changed", focus: "menu" }, "msg");

    const logFile = path.join(root, ".swarmforge", "logs", "tui.log");
    const lines = readFileSync(logFile, "utf8").trim().split("\n");
    const first = JSON.parse(lines[0]);
    assert.equal(first.component, "swarm-tui");
    assert.equal(first.subcomponent, "io");
    assert.equal(first.level, "warn");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("errors are serialized with type, message, and stack", () => {
  const root = tempRoot();
  try {
    const logger = init({ root });
    const err = new Error("boom");
    logger.error({ event: "crash", err }, "exploded");

    const logFile = path.join(root, ".swarmforge", "logs", "tui.log");
    const line = JSON.parse(readFileSync(logFile, "utf8").trim());
    assert.equal(line.err.type, "Error");
    assert.equal(line.err.message, "boom");
    assert.ok(line.err.stack.includes("Error: boom"));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("init falls back to stderr when the log directory cannot be created", () => {
  const root = tempRoot();
  try {
    const probe = path.join(root, ".swarmforge");
    writeFileSync(probe, "regular file, not a directory");
    const stderrChunks: string[] = [];
    const originalWrite = process.stderr.write.bind(process.stderr);
    process.stderr.write = ((chunk: string | Uint8Array) => {
      stderrChunks.push(typeof chunk === "string" ? chunk : chunk.toString());
      return true;
    }) as typeof process.stderr.write;
    try {
      const logger = init({ root });
      logger.info({ event: "tui_start" }, "still works");
      const combined = stderrChunks.join("");
      assert.match(combined, /cannot create log directory/);
    } finally {
      process.stderr.write = originalWrite;
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("level filter excludes lower-priority events", () => {
  const root = tempRoot();
  try {
    const logger = init({ root, level: "warn" });
    logger.info({ event: "tui_start" }, "filtered");
    logger.warn({ event: "socket_unavailable" }, "kept");
    logger.error({ event: "snapshot_failed" }, "kept");

    const logFile = path.join(root, ".swarmforge", "logs", "tui.log");
    const lines = readFileSync(logFile, "utf8").trim().split("\n");
    assert.equal(lines.length, 2);
    const events = lines.map((line) => JSON.parse(line).event);
    assert.deepEqual(events, ["socket_unavailable", "snapshot_failed"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});