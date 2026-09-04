/**
 * Build the SwarmForge TUI into a standalone binary.
 *
 * Why this is a script and not a one-line `bun build --compile`:
 *
 * OpenTUI loads its native Zig core through a per-platform package
 * (`@opentui/core-linux-x64`, `@opentui/core-darwin-arm64`, ...). Only the
 * one matching the build machine is installed, because npm skips optional
 * dependencies whose `os`/`cpu`/`libc` do not match. The bundler, however,
 * still walks every branch that survives target pruning — on glibc Linux
 * that includes the musl sibling, which is never installed and never runs.
 *
 * So: resolve the packages that exist, and stub the ones that do not. A
 * stub can only be reached on a platform the binary was not built for, and
 * it throws a clear message if that ever happens.
 */

import path from "node:path";
import type { BunPlugin } from "bun";

const NATIVE_PACKAGE = /^@opentui\/core-(?:linux|darwin|win32)-[a-z0-9-]+$/;

const stubUninstalledNativeTargets: BunPlugin = {
  name: "stub-uninstalled-opentui-targets",
  setup(build) {
    build.onResolve({ filter: NATIVE_PACKAGE }, (args) => {
      try {
        Bun.resolveSync(args.path, import.meta.dir);
        // Installed for this platform: let Bun bundle the real thing.
        return undefined;
      } catch {
        return { path: args.path, namespace: "opentui-absent" };
      }
    });
    build.onLoad({ filter: /.*/, namespace: "opentui-absent" }, (args) => ({
      contents:
        `throw new Error(${JSON.stringify(
          `This binary was not built for the platform served by ${args.path}.`,
        )});\nexport default null;\n`,
      loader: "js",
    }));
  },
};

const outfile = path.join(import.meta.dir, "dist", "swarm-tui");

const result = await Bun.build({
  entrypoints: [path.join(import.meta.dir, "main.ts")],
  plugins: [stubUninstalledNativeTargets],
  compile: { outfile },
});

if (!result.success) {
  for (const message of result.logs) console.error(String(message));
  process.exit(1);
}

console.log(`built ${outfile}`);
