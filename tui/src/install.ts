import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";

export const INSTALLED_BUNDLE_REL = path.join(".swarmforge", "tui", "swarm-tui.js");

export type InstallOutcome =
  | { status: "installed"; target: string }
  | { status: "bundle_missing"; source: string }
  | { status: "target_exists"; target: string };

export function installedBundlePath(projectRoot: string): string {
  return path.join(projectRoot, INSTALLED_BUNDLE_REL);
}

export function installTuiBundle(sourceBundle: string, projectRoot: string): InstallOutcome {
  if (!existsSync(sourceBundle)) {
    return { status: "bundle_missing", source: sourceBundle };
  }
  const target = installedBundlePath(projectRoot);
  if (existsSync(target)) {
    return { status: "target_exists", target };
  }
  mkdirSync(path.dirname(target), { recursive: true });
  copyFileSync(sourceBundle, target);
  return { status: "installed", target };
}

export async function launchInstalledTui(projectRoot: string): Promise<number> {
  const bundle = installedBundlePath(projectRoot);
  if (!existsSync(bundle)) {
    throw new Error(`TUI bundle not found at ${bundle}`);
  }
  const { spawn } = await import("node:child_process");
  return new Promise((resolve, reject) => {
    const child = spawn("node", [bundle], { cwd: projectRoot, stdio: "ignore" });
    child.on("error", reject);
    child.on("exit", (code) => resolve(code ?? -1));
  });
}

export function errorMessage(outcome: Extract<InstallOutcome, { status: "bundle_missing" | "target_exists" }>): string {
  if (outcome.status === "bundle_missing") {
    return `TUI bundle not found at ${outcome.source}. Build it with: (cd tui && npm run build)`;
  }
  return `TUI bundle already exists at ${outcome.target}; refusing to overwrite.`;
}

if (import.meta.main) {
  const [source, projectRoot] = process.argv.slice(2);
  if (!source || !projectRoot) {
    process.stderr.write("usage: node tui/src/install.ts <source-bundle> <project-root>\n");
    process.exit(2);
  }
  const outcome = installTuiBundle(source, projectRoot);
  if (outcome.status === "installed") {
    process.stdout.write(`Installed TUI bundle at ${outcome.target}\n`);
    process.exit(0);
  }
  process.stderr.write(`Error: ${errorMessage(outcome)}\n`);
  process.exit(1);
}