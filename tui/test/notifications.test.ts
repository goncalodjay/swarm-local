import { test } from "node:test";
import assert from "node:assert/strict";
import { buildNotifyCommand, isNotifyEnabled, ringBell, systemNotify } from "../src/notifications.ts";

test("buildNotifyCommand uses osascript on darwin with quoted strings", () => {
  const cmd = buildNotifyCommand("darwin", "swarm-tui", `Refactorer is "waiting"`);
  assert.ok(cmd);
  assert.equal(cmd[0], "osascript");
  assert.equal(cmd[1], "-e");
  assert.match(cmd[2]!, /display notification /);
  assert.match(cmd[2]!, /with title /);
  assert.match(cmd[2]!, /sound name "Submarine"/);
  assert.ok(cmd[2]!.includes('Refactorer is \\"waiting\\"'));
});

test("buildNotifyCommand uses notify-send -u critical on linux", () => {
  const cmd = buildNotifyCommand("linux", "swarm-tui", "Spec needs input");
  assert.deepEqual(cmd, ["notify-send", "-u", "critical", "-t", "0", "swarm-tui", "Spec needs input"]);
});

test("buildNotifyCommand uses powershell NotifyIcon on win32", () => {
  const cmd = buildNotifyCommand("win32", "swarm-tui", "Architect blocked");
  assert.ok(cmd);
  assert.equal(cmd[0], "powershell");
  assert.equal(cmd[1], "-NoProfile");
  assert.equal(cmd[2], "-Command");
  assert.match(cmd[3]!, /NotifyIcon/);
  assert.match(cmd[3]!, /ShowBalloonTip/);
  assert.match(cmd[3]!, /SystemIcons]::Warning/);
  assert.match(cmd[3]!, /'swarm-tui'/);
  assert.match(cmd[3]!, /'Architect blocked'/);
});

test("buildNotifyCommand escapes single quotes for PowerShell", () => {
  const cmd = buildNotifyCommand("win32", "t", "it's pending");
  assert.ok(cmd);
  assert.match(cmd[3]!, /'it''s pending'/);
});

test("buildNotifyCommand returns null on unsupported platforms", () => {
  assert.equal(buildNotifyCommand("aix" as NodeJS.Platform, "t", "b"), null);
  assert.equal(buildNotifyCommand("freebsd" as NodeJS.Platform, "t", "b"), null);
});

test("isNotifyEnabled returns false when SWARMTUI_NOTIFY=0", () => {
  const previous = process.env.SWARMTUI_NOTIFY;
  process.env.SWARMTUI_NOTIFY = "0";
  try {
    assert.equal(isNotifyEnabled(), false);
  } finally {
    if (previous === undefined) delete process.env.SWARMTUI_NOTIFY;
    else process.env.SWARMTUI_NOTIFY = previous;
  }
});

test("isNotifyEnabled returns true by default and for any other value", () => {
  const previous = process.env.SWARMTUI_NOTIFY;
  delete process.env.SWARMTUI_NOTIFY;
  assert.equal(isNotifyEnabled(), true);
  process.env.SWARMTUI_NOTIFY = "1";
  assert.equal(isNotifyEnabled(), true);
  process.env.SWARMTUI_NOTIFY = "true";
  assert.equal(isNotifyEnabled(), true);
  if (previous === undefined) delete process.env.SWARMTUI_NOTIFY;
  else process.env.SWARMTUI_NOTIFY = previous;
});

test("ringBell writes a BEL byte to stderr without throwing", () => {
  const chunks: Buffer[] = [];
  const originalWrite = process.stderr.write.bind(process.stderr);
  process.stderr.write = ((chunk: string | Uint8Array) => {
    if (typeof chunk === "string") chunks.push(Buffer.from(chunk));
    else chunks.push(Buffer.from(chunk));
    return true;
  }) as typeof process.stderr.write;
  try {
    ringBell();
    const combined = Buffer.concat(chunks);
    assert.ok(combined.includes(0x07), "stderr did not include BEL");
  } finally {
    process.stderr.write = originalWrite;
  }
});

test("systemNotify is a no-op when SWARMTUI_NOTIFY=0", () => {
  const previous = process.env.SWARMTUI_NOTIFY;
  process.env.SWARMTUI_NOTIFY = "0";
  try {
    systemNotify("t", "b");
  } finally {
    if (previous === undefined) delete process.env.SWARMTUI_NOTIFY;
    else process.env.SWARMTUI_NOTIFY = previous;
  }
});
