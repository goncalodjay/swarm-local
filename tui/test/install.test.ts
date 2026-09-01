import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { installTuiBundle, installedBundlePath, launchInstalledTui } from "../src/install.ts";

function tempDir(prefix: string): string {
  return mkdtempSync(path.join(os.tmpdir(), prefix));
}

function writeSourceBundle(dir: string, content = "bundle-content"): string {
  const file = path.join(dir, "swarm-tui.js");
  writeFileSync(file, content);
  return file;
}

test("installedBundlePath points at .swarmforge/tui/swarm-tui.js under the project root", () => {
  const root = "/tmp/some/project";
  assert.equal(installedBundlePath(root), path.join(root, ".swarmforge", "tui", "swarm-tui.js"));
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
  const outcome = installTuiBundle("/does/not/exist/swarm-tui.js", project);
  assert.equal(outcome.status, "bundle_missing");
  assert.ok(!existsSync(installedBundlePath(project)), "no target should be created");
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

test("launchInstalledTui executes the installed bundle", async () => {
  const sourceDir = tempDir("install-src-");
  const project = tempDir("install-project-");
  const marker = path.join(project, "executed.marker");
  const stub = `require("node:fs").writeFileSync(${JSON.stringify(marker)}, "executed");\nprocess.exit(0);\n`;
  const source = writeSourceBundle(sourceDir, stub);
  const outcome = installTuiBundle(source, project);
  assert.equal(outcome.status, "installed");
  const code = await launchInstalledTui(project);
  assert.equal(code, 0);
  assert.ok(existsSync(marker), "installed bundle should have been executed");
});