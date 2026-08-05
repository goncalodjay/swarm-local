# Swarm local

Plantilla local y autónoma para iniciar SwarmForge en otros proyectos con agentes Pi u OpenCode. No descarga `swarm-forge` ni consulta GitHub al ejecutar `swarm-init` o `./swarm`.

## Instalación local

```sh
mkdir -p ~/.local/bin
ln -sf ~/projects/swarm-local/swarm-init ~/.local/bin/swarm-init
```

Asegúrate de que `~/.local/bin` esté en `PATH`.

## Uso

Desde la raíz de un proyecto:

```sh
swarm-init
# responde los lenguajes, por ejemplo: Go, Python
./swarm
```

También admite uso no interactivo:

```sh
swarm-init --languages "TypeScript, SQL" /ruta/al/proyecto
```

El inicializador copia `swarm` y `swarmforge/`, sustituye `{{LANGUAGES}}` en la constitución y sus artículos, y añade `.swarmforge/` y `.worktrees/` al `.gitignore`. No sobrescribe una instalación existente.

## Requisitos locales

`./swarm` requiere `bb`, `tmux`, `git` y el ejecutable configurado para cada rol (`pi`, `opencode`, `claude`, `codex`, `copilot` o `grok`). La plantilla incluye localmente `gherkin-parser`, `gherkin-ir-dry-checker` y `gherkin-mutator`; `swarm-init` los instala bajo `.swarmforge/toolchain/bin` y los añade al `PATH` de cada agente. Las herramientas de mutación, CRAP y DRY específicas de cada lenguaje siguen siendo una decisión del proyecto: los agentes no las descargarán y pedirán indicaciones si una tarea las requiere.

## Usar OpenCode

Edita una fila de `swarmforge/swarmforge.conf` y sustituye el backend por `opencode`. Por ejemplo:

```text
window coder opencode coder --model provider/model --agent build --auto
```

Los argumentos posteriores al worktree (`--model`, `--agent`, `--auto`, etc.) se pasan directamente a OpenCode. SwarmForge inicia `opencode` en el worktree del rol y entrega las instrucciones de constitución y rol mediante `--prompt`. OpenCode debe estar autenticado/configurado antes de iniciar el swarm; `--auto` aprueba permisos automáticamente y debe usarse solo si lo deseas.

`template/` es la fuente de verdad de las configuraciones compartidas. Los cambios de un proyecto ya inicializado no modifican esta plantilla.
