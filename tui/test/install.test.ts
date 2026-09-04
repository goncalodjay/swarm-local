import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  installTuiBundle,
  installedBundlePath,
  runInstallCli,
} from "../src/install.ts";
import { launchInstalledTui } from "./helpers/launch-installed-tui.ts";

function tempDir(prefix: string): string {
  return mkdtempSync(path.join(os.tmpdir(), prefix));
}

function writeSourceBundle(dir: string, content = "bundle-content"): string {
  const file = path.join(dir, "swarm-tui");
  writeFileSync(file, content);
  return file;
}

function output(): { stream: { write(message: string): void }; read: () => string } {
  let value = "";
  return {
    stream: { write: (message: string) => { value += message; } },
    read: () => value,
  };
}

test("installedBundlePath points at .swarmforge/tui/swarm-tui under the project root", () => {
  const root = "/tmp/some/project";
  assert.equal(installedBundlePath(root), path.join(root, ".swarmforge", "tui", "swarm-tui"));
});

test("installTuiBundle copies the bundle into the project when the source exists", () => {
  const sourceDir = tempDir("install-src-");
  const project = tempDir("install-project-");
  const source = writeSourceBundle(sourceDir, "bundle-v1");
  const outcome = installTuiBundle(source, project);
  assert.equal(outcome.status, "installed");
  const target = installedBundlePath(project);
  assert.ok(existsSync(target), "installed bundle should exist");
  assert.equal(readFileSync(target, "utf8"), "bundle-v1");
});

test("installTuiBundle reports bundle_missing when the source bundle does not exist", () => {
  const project = tempDir("install-project-");
  const outcome = installTuiBundle("/does/not/exist/swarm-tui", project);
  assert.equal(outcome.status, "bundle_missing");
  assert.ok(!existsSync(installedBundlePath(project)), "no target should be created");
});

test("runInstallCli reports usage errors without touching the filesystem", () => {
  const stdout = output();
  const stderr = output();
  const status = runInstallCli([], stdout.stream, stderr.stream);
  assert.equal(status, 2);
  assert.equal(stdout.read(), "");
  assert.match(stderr.read(), /^usage:/);
});

test("runInstallCli reports an install failure through its return code and error output", () => {
  const stdout = output();
  const stderr = output();
  const project = tempDir("install-project-");
  const status = runInstallCli(["/does/not/exist/swarm-tui", project], stdout.stream, stderr.stream);
  assert.equal(status, 1);
  assert.equal(stdout.read(), "");
  assert.match(stderr.read(), /TUI bundle not found/);
});

test("runInstallCli reports success through its return code and output", () => {
  const sourceDir = tempDir("install-src-");
  const project = tempDir("install-project-");
  const source = writeSourceBundle(sourceDir);
  const stdout = output();
  const stderr = output();
  const status = runInstallCli([source, project], stdout.stream, stderr.stream);
  assert.equal(status, 0);
  assert.match(stdout.read(), /Installed TUI bundle at/);
  assert.equal(stderr.read(), "");
});

test("installTuiBundle reports target_exists and does not overwrite when the target already exists", () => {
  const sourceDir = tempDir("install-src-");
  const project = tempDir("install-project-");
  const source = writeSourceBundle(sourceDir, "bundle-new");
  const target = installedBundlePath(project);
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, "bundle-old");
  const outcome = installTuiBundle(source, project);
  assert.equal(outcome.status, "target_exists");
  assert.equal(readFileSync(target, "utf8"), "bundle-old", "existing bundle must be unchanged");
});

test("runInstallCli reports an existing target without overwriting it", () => {
  const sourceDir = tempDir("install-src-");
  const project = tempDir("install-project-");
  const source = writeSourceBundle(sourceDir, "bundle-new");
  const target = installedBundlePath(project);
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, "bundle-old");
  const stdout = output();
  const stderr = output();
  const status = runInstallCli([source, project], stdout.stream, stderr.stream);
  assert.equal(status, 1);
  assert.equal(stdout.read(), "");
  assert.match(stderr.read(), /refusing to overwrite/);
  assert.equal(readFileSync(target, "utf8"), "bundle-old");
});

test("launchInstalledTui executes the installed bundle", async () => {
  const sourceDir = tempDir("install-src-");
  const project = tempDir("install-project-");
  const marker = path.join(project, "executed.marker");
  const stub = `#!/bin/sh\nprintf executed > ${JSON.stringify(marker)}\nexit 0\n`;
  const source = writeSourceBundle(sourceDir, stub);
  const outcome = installTuiBundle(source, project);
  assert.equal(outcome.status, "installed");
  const code = await launchInstalledTui(project);
  assert.equal(code, 0);
  assert.ok(existsSync(marker), "installed bundle should have been executed");
});

test("launchInstalledTui rejects when the installed bundle is missing", async () => {
  const project = tempDir("install-project-");
  await assert.rejects(
    () => launchInstalledTui(project),
    new RegExp(`TUI bundle not found at ${installedBundlePath(project)}`),
  );
});
