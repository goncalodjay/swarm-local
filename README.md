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
# responde los lenguajes y, para cada rol, backend, modelo y thinking/effort
./swarm
```

Puedes indicar los lenguajes por adelantado, pero el inicializador siempre consulta la configuración de los cuatro roles:

```sh
swarm-init --languages "TypeScript, SQL" /ruta/al/proyecto
```

El inicializador copia `swarm` y `swarmforge/`, sustituye `{{LANGUAGES}}` en la constitución y sus artículos, genera `swarmforge/swarmforge.conf` para los cuatro roles y añade `.swarmforge/` y `.worktrees/` al `.gitignore`. No sobrescribe una instalación existente.

## Requisitos locales

`./swarm` requiere `bb`, `tmux`, `git` y el ejecutable configurado para cada rol (`pi`, `opencode`, `claude`, `codex`, `copilot` o `grok`). La plantilla incluye localmente `gherkin-parser`, `gherkin-ir-dry-checker` y `gherkin-mutator`; `swarm-init` los instala bajo `.swarmforge/toolchain/bin` y los añade al `PATH` de cada agente. Las herramientas de mutación, CRAP y DRY específicas de cada lenguaje siguen siendo una decisión del proyecto: los agentes no las descargarán y pedirán indicaciones si una tarea las requiere.

## Usar OpenCode

Durante `swarm-init` puedes elegir `opencode` para cualquier rol. También puedes editar una fila de `swarmforge/swarmforge.conf` posteriormente. Por ejemplo:

```text
window coder opencode coder --model provider/model --agent build --auto
```

Los argumentos posteriores al worktree (`--model`, `--agent`, `--auto`, etc.) se pasan directamente a OpenCode. SwarmForge inicia `opencode` en el worktree del rol y entrega las instrucciones de constitución y rol mediante `--prompt`. OpenCode debe estar autenticado/configurado antes de iniciar el swarm; `--auto` aprueba permisos automáticamente y debe usarse solo si lo deseas.

## Configuración por rol

`swarm-init` pregunta para `specifier`, `coder`, `refactorer` y `architect`, en este orden: backend (`claude`, `codex`, `copilot`, `grok`, `opencode` o `pi`), modelo y nivel de thinking/effort. Los cuatro roles pueden usar backends distintos.

El inicializador aplica el nivel al argumento compatible: `pi` usa `--thinking`, Claude usa `--effort` y Codex usa `-c model_reasoning_effort=...`. OpenCode, Copilot y Grok reciben el modelo; sus opciones de razonamiento dependen de su configuración/proveedor y puedes añadir sus flags específicos manualmente a `swarmforge.conf`.

`template/` es la fuente de verdad de las configuraciones compartidas. Los cambios de un proyecto ya inicializado no modifican esta plantilla.
