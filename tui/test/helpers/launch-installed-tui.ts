import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { installedBundlePath } from "../../src/install.ts";

export function launchInstalledTui(projectRoot: string): Promise<number> {
  const bundle = installedBundlePath(projectRoot);
  if (!existsSync(bundle)) {
    return Promise.reject(new Error(`TUI bundle not found at ${bundle}`));
  }
  return new Promise((resolve, reject) => {
    const child = spawn("node", [bundle], { cwd: projectRoot, stdio: "ignore" });
    child.on("error", reject);
    child.on("exit", (code) => resolve(code ?? -1));
  });
}
