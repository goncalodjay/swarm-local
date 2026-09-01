# swarm-init TUI installation — Feature Brief

## Goal and context

`swarm-init` is the command run on every project to install the local SwarmForge
template. The TUI already exists in swarm-local under `tui/` and `./swarm tui`
can launch it, but `swarm-init` currently copies only `swarm` and `swarmforge/`
into the target project. This feature makes `swarm-init` also install a
pre-built TUI bundle so that `./swarm tui` works immediately after
initialization.

## Decisions settled with the user

- **Distribution model**: pre-built bundle, not source. `swarm-init` copies a
  single bundle file instead of the whole `tui/` source tree. This keeps the
  target project clean and the install step trivial.
- **Build tooling**: `esbuild` is installed as a devDependency in swarm-local.
  The bundle is built in swarm-local before `swarm-init` runs; target projects
  do not download or install build tools.
- **Bundle location in swarm-local**: `tui/dist/swarm-tui.js`.
- **Bundle location in target project**: `.swarmforge/tui/swarm-tui.js`, so it
  lives alongside other SwarmForge runtime files and is already covered by the
  existing `.swarmforge/` `.gitignore` entry.
- **Launcher update**: `./swarm tui` (via `swarm_python/entrypoints/swarm_cli.py`)
  executes the installed bundle at `.swarmforge/tui/swarm-tui.js` instead of
  looking for source at `tui/main.ts`.
- **Collision handling**: `swarm-init` fails if `.swarmforge/tui/swarm-tui.js`
  already exists, consistent with the existing refusal to overwrite `swarmforge/`
  or `swarm`.
- **Missing bundle**: `swarm-init` fails with a clear error if the bundle has
  not been built in swarm-local.

## Edge cases and error handling

- Bundle missing at `tui/dist/swarm-tui.js` → `swarm-init` fails before touching
  the target project.
- Target already has `.swarmforge/tui/swarm-tui.js` → `swarm-init` fails without
  overwriting.
- Bundle exists but is corrupted/empty → `swarm-init` copies it blindly; runtime
  failure is handled by `./swarm tui` as an execution error, out of scope for
  this feature.
- `./swarm tui` called in a project where the bundle is missing → existing TUI
  error handling reports that the TUI is unavailable.

## Non-functional constraints

- No network access in `swarm-init`; the bundle must already exist in
  swarm-local.
- The installed bundle must be executable with the Node version available on the
  target machine (Node 26.4.0+).
- `swarm-init` must not increase the install time significantly; copying one
  file is acceptable.

## Non-goals

- Building the bundle during `swarm-init`.
- Installing the TUI source or tests into the target project.
- Supporting per-project TUI customization or rebuilds.
- Removing `pino` from the TUI; the bundler includes it in the single output
  file.

## Open questions

- None.
