# Project overview

`swarm-local` es un repo autocontenido que arma SwarmForge en cualquier carpeta de proyecto sin clonar nada de GitHub. Tiene un único entry point (`install_swarm.sh`) y dos scripts de soporte (`swarm-init`, `./swarm`).

## Estructura del repo

```
swarm-local/
├── install_swarm.sh            # entry point: descarga TUI + corre swarm-init
├── swarm-init                  # installer bash: entrevista + copia template/ al target
├── swarm                       # shim bash que delega en swarmforge.sh del proyecto destino
├── provider-models.tsv         # catálogo agente → proveedor → modelo (usado por swarm-init)
│
├── template/                   # fuente de verdad de todo lo que se copia a un proyecto destino
│   ├── swarm                   # shim copiado al target
│   ├── swarmforge/
│   │   ├── scripts/            # backend Python: herdr, handoffs
│   │   ├── roles/              # prompts por rol (specifier, coder, reviewer, architect)
│   │   └── constitution*       # archivo base que cada rol lee
│   └── toolchain/              # binarios auxiliares (gherkin-*) que swarm-init copia a .swarmforge
│
├── tui/                        # dashboard OpenTUI; se compila con Bun y produce un ELF standalone
│   ├── main.ts                 # entry del TUI
│   ├── build.ts                # Bun.build({compile}) → dist/swarm-tui
│   ├── src/                    # módulos TypeScript (render, attach, io, theme, …)
│   ├── test/                   # tests (bun test)
│   ├── dev-preview.sh          # arranca la TUI con workspaces de herdr fake
│   └── dist/swarm-tui          # binario compilado (platform-specific)
│
├── docs/                       # esta carpeta
│   ├── project-overview.md     # este archivo
│   └── distribution.md         # cómo se publica un binario por plataforma
│
└── docs/_archive/              # documentos viejos que describen carpetas inexistentes; sólo referencia
```

## Flujo end-to-end

1. Usuario corre `./install_swarm.sh /ruta/al/proyecto` en una máquina nueva.
2. `install_swarm.sh` detecta plataforma y descarga el binario de la TUI desde GitHub Releases (o compila local si no existe).
3. Delega a `./swarm-init /ruta/al/proyecto`, que pregunta lenguajes, auto-approve y (por cada rol) agente → proveedor → modelo → nivel de razonamiento.
4. `swarm-init` copia `template/swarmforge/`, `template/toolchain/`, `template/swarm`, y el binario de la TUI al target. Edita `swarmforge/swarmforge.conf` con las elecciones y sustituye `{{LANGUAGES}}` en la constitución.
5. En el proyecto destino, `./swarm` valida que `herdr` y `engram` estén instalados (`require("herdr")`, `require("engram")`, igual que `git`), levanta 4 workspaces de herdr (uno por rol, dentro de una sesión de herdr con nombre `swarmforge-<hash del path>`) con `ENGRAM_PROJECT` exportado al mismo valor en las cuatro, y lanza el agente CLI correspondiente con sus flags.
6. `./swarm tui` adjunta la TUI a esos workspaces para inspeccionar/adjuntar manualmente.

## Memoria compartida

Los cuatro roles leen y escriben memoria de proyecto compartida con la CLI de [Engram](https://github.com/Gentleman-Programming/engram) (`engram context`/`search`/`save`), no vía MCP, porque `grok` y `hermes` no tienen integración oficial de Engram y los otros backends corren en modo batch/exec sin garantía de servidor MCP configurado. `ENGRAM_PROJECT` se fija una sola vez por sesión de swarm (nombre del directorio raíz), así los cuatro worktrees resuelven al mismo proyecto de Engram en vez de que la detección por `cwd` de Engram invente uno distinto por worktree. El protocolo de uso vive en `template/swarmforge/scripts/shared-articles/memory.prompt`.

## Componentes por responsabilidad

| Componente | Archivo(s) | Responsabilidad |
| --- | --- | --- |
| Installer | `install_swarm.sh` | descarga TUI, valida SHA, llama a `swarm-init` |
| Interview + copy | `swarm-init` | menús numerados (bash), copia `template/`, escribe `swarmforge.conf`, sustituye placeholders |
| Runtime backend | `template/swarmforge/scripts/swarm_python/` | herdr workspaces/panes, handoffs |
| Dashboard | `tui/` | OpenTUI sobre Bun, empaquetado como binario standalone |

## Documentación obsoleta

Los archivos en `docs/_archive/` describen carpetas (`swarmforge/scripts/**/*` para Ansible, `vendor/acceptance-pipeline-specification/`, etc.) que **no existen** en este repo. Se conservaron sólo como referencia histórica. Si necesitás saber qué hace algo, mirá el código — no esos archivos.
