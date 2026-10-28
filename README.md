# Swarm local

Plantilla local y autónoma para iniciar SwarmForge en otros proyectos con agentes Pi u OpenCode. No descarga `swarm-forge` ni consulta GitHub al ejecutar `swarm-init` o `./swarm`.

## Requisitos

Instalá estas herramientas en el dispositivo destino **antes** de clonar el repo.

| Herramienta | Versión mínima | Para qué se usa | Notas |
| --- | --- | --- | --- |
| `bash` | 5.0 | `swarm-init`, `install_swarm.sh`, `./swarm`, todos los `.sh` | viene con macOS / WSL / la mayoría de Linux |
| `python3` | 3.10 | backend de SwarmForge (`swarmforge/scripts/swarm_python/*.py`) | en WSL/Ubuntu: `sudo apt install python3`; en macOS: `brew install python@3.12` |
| `herdr` | última estable | multiplexor de terminal por agente y superficie de la TUI; reemplaza a tmux | `curl -fsSL https://herdr.dev/install.sh \| sh` (Linux/macOS) o `powershell -ExecutionPolicy Bypass -c "irm https://herdr.dev/install.ps1 \| iex"` (Windows); `brew install herdr`; `./swarm` no arranca sin él |
| `git` | 2.30 | worktrees del swarm, `swarm_handoff.sh` | preinstalado en todas las distros |
| `engram` | última estable | memoria compartida entre los cuatro roles (ver [Memoria compartida](#memoria-compartida-engram)) | `brew install gentleman-programming/tap/engram` o ver [instalación de Engram](https://github.com/Gentleman-Programming/engram/blob/main/docs/INSTALLATION.md); `./swarm` no arranca sin él |
| `curl` | 7.x | descarga del binario de la TUI desde GitHub Releases | `sudo apt install curl` |
| `tar` | cualquier | extracción del bundle si la release viene como `.tar.gz` | preinstalado |
| `opencode`, `codex`, `claude`, `copilot`, `grok`, `hermes`, `kiro-cli`, `pi` | las que publique cada vendor | CLIs de los agentes que elijas para cada rol | instalá solo las que vas a usar; el wizard de `swarm-init` las valida |
| `bun` | 1.4 | solo para compilar la TUI desde fuente | obligatorio **en la máquina donde compilás**, **no** en la destino |
| `node` + `npm` | node 20.x o 22.x, npm 10.x | compilar la TUI desde fuente (cuando no hay binario pre-construido) | mismo caso: solo en la máquina de build |

Resumen rápido:

- **En cada máquina destino** (donde corrés `./swarm`, incluida Windows): `bash`, `python3 >= 3.10`, `herdr`, `git`, `engram`, `curl`, `tar`, y los CLIs de los agentes que vayas a usar.
- **En la máquina de build** (donde compilas la TUI una vez): además de lo anterior, `node >= 20` y `bun >= 1.4`. Si solo vas a usar binarios pre-construidos de las GitHub Releases, no necesitás esta máquina.

Verificación rápida en una sola línea:

```sh
for t in bash python3 herdr git engram curl tar; do command -v "$t" >/dev/null && echo "OK $t" || echo "MISSING $t"; done
```

## Instalación local

```sh
mkdir -p ~/.local/bin
ln -sf ~/projects/swarm-local/swarm-init ~/.local/bin/swarm-init
```

Asegúrate de que `~/.local/bin` esté en `PATH`.

## Instalar SwarmForge en otra máquina

Cloná el repo y corré el instalador:

```sh
git clone <url-del-repo> ~/swarm-local
cd ~/swarm-local
./install_swarm.sh /ruta/al/proyecto
```

`install_swarm.sh` hace:

1. Detecta SO y arquitectura (linux-x64, darwin-arm64, …).
2. Descarga el binario de la TUI correspondiente desde GitHub Releases (`swarm-tui-<os>-<arch>.bin` + `.sha256`) y lo verifica.
3. Si no hay binario publicado para esa plataforma, compila la TUI localmente (necesita `node` y `bun`).
4. Ejecuta `./swarm-init <target>` que copia la plantilla y deja el proyecto listo (o `./swarm-init --update <target>` si ya hay una instalación; ver más abajo).

Variables opcionales:

- `SWARM_RELEASE_REPO=owner/swarm-local` (default: `nousresearch/swarm-local`).
- `SWARM_RELEASE_TAG=v1.2.3` o `latest` (default: `latest`).

## Actualizar una instalación existente

Volver a ejecutar `./install_swarm.sh /ruta/al/proyecto` (o `swarm-init --update /ruta/al/proyecto`) sobre un proyecto que ya tiene SwarmForge **actualiza** la instalación; no hace falta borrar nada.

**Se reemplaza** todo lo que es de la plantilla: `swarmforge/scripts/`, los prompts de rol que trae la plantilla (`swarmforge/roles/*.prompt`), `swarmforge/constitution.prompt` y `swarmforge/constitution/`, `swarm`, `swarm.cmd`, `.swarmforge/toolchain` y el binario del TUI. Se agregan `.swarmforge/` y `.worktrees/` a `.gitignore` si faltan (nunca se quita nada). Los lenguajes se leen de `project.prompt` o de `--languages`.

**`swarmforge.conf` se reconcilia**, no se pisa: se conservan tus filas de agente/modelo de los roles que existen en la plantilla, se pregunta (con el asistente habitual) solo por los roles nuevos de la plantilla, y se quitan los roles que la plantilla ya no tiene (por ejemplo `refactorer`). El archivo anterior queda como `swarmforge/swarmforge.conf.bak` (con marca de tiempo si ya existía uno). Los prompts de rol propios que la plantilla no trae se conservan, con un aviso.

**Antes de tocar nada** se revisa si hay un swarm en ejecución, archivos con cambios sin commit entre los que se van a sobrescribir y worktrees sucios. Si no hay nada en riesgo, sigue sin preguntar. Si lo hay, lista exactamente qué, recomienda hacer commit de tu trabajo y pide confirmación `(s/N)`:

- `N` o Enter: cancela con código distinto de 0 y no modifica nada.
- `s`: detiene el swarm, copia los archivos en riesgo a `.swarmforge/backup-<fecha-hora>/` y actualiza. Nunca se borra un worktree ni una rama con cambios sin commit; para los roles obsoletos con worktree limpio se ofrece eliminarlo, y si no, se imprimen los comandos `git worktree remove` / `git branch -d`.
- Sin terminal (CI, pipes) la actualización se cancela salvo que pases `--yes` (`./install_swarm.sh --yes /ruta/al/proyecto`). Si la plantilla agregó roles nuevos y no hay terminal para elegir su agente, también se cancela.

El estado vivo (`.swarmforge/handoffs`, logs, notify, sesiones y `agents.json` de los roles vigentes) se conserva, de modo que `./swarm` retoma las sesiones. Al terminar se imprime un resumen de lo reemplazado, lo conservado, las copias de seguridad y el siguiente paso.

## Uso

Desde la raíz de un proyecto:

```sh
./install_swarm.sh .          # o: ./swarm-init
# responde los lenguajes, la auto-aprobación de permisos y, para cada rol, backend, modelo y thinking/effort
./swarm
```

`./swarm` levanta el swarm y abre la TUI directamente. Mientras el `specifier` arranca, la TUI muestra "Starting swarm" y no deja entrar a ningún agente; se habilita cuando el `specifier` está listo. Solo corre un agente a la vez: los demás roles se lanzan cuando les llega trabajo y se estacionan (`parked`) al quedar libres.

| Comando | Qué hace |
| --- | --- |
| `./swarm` | Levanta el swarm y abre la TUI. |
| `q` o `Ctrl+k q` en la TUI | Cierra la TUI **y detiene todo**: cada agente, el daemon de handoffs y la sesión de herdr del swarm, para liberar memoria. Cerrar la ventana de la terminal hace lo mismo. |
| `./swarm tui` | Vuelve a abrir la TUI sobre un swarm que ya está corriendo. |
| `./swarm stop` | Detiene todo sin abrir la TUI. |
| `./swarm --no-tui` | Levanta el swarm sin abrir la TUI (modo anterior). |

Al volver a correr `./swarm`, cada rol retoma la sesión de su feature en curso (`.swarmforge/agents.json`).

Puedes indicar los lenguajes por adelantado, pero el inicializador siempre consulta la configuración de los cuatro roles:

```sh
swarm-init --languages "TypeScript, SQL" /ruta/al/proyecto
```

El inicializador copia `swarm` y `swarmforge/`, sustituye `{{LANGUAGES}}` en la constitución y sus artículos, genera `swarmforge/swarmforge.conf` para los cuatro roles y añade `.swarmforge/` y `.worktrees/` al `.gitignore`. No sobrescribe una instalación existente.

## Requisitos locales

`./swarm` requiere `python3` (3.10+), `herdr`, `git` y el ejecutable configurado para cada rol (`pi`, `opencode`, `claude`, `codex`, `copilot`, `grok`, `hermes` o `kiro-cli`). La plantilla incluye localmente `gherkin-parser`, `gherkin-ir-dry-checker` y `gherkin-mutator`; `swarm-init` los instala bajo `.swarmforge/toolchain/bin` y los añade al `PATH` de cada agente. Las herramientas de mutación, CRAP y DRY específicas de cada lenguaje siguen siendo una decisión del proyecto: los agentes no las descargarán y pedirán indicaciones si una tarea las requiere.

`swarm-init` ya no necesita `node` en el destino: copia la TUI con `install -m 0755`. Para compilarla por primera vez o para una plataforma nueva, hace falta `node` + `bun` en la máquina de build (ver tabla de requisitos). El binario del TUI no requiere ningún runtime en la máquina destino.

## TUI

El dashboard (se abre con `./swarm`; `./swarm tui` lo reabre) está construido sobre [OpenTUI](https://github.com/anomalyco/opentui) y se distribuye como **binario autocontenido**: el usuario final no necesita Node ni Bun para ejecutarlo.

```sh
cd tui
npm install
npm run build   # produce tui/dist/swarm-tui para la plataforma actual
npm test
```

- **Bun solo hace falta para compilar y testear**, y viene como devDependency del propio proyecto (`npm install` lo trae). No hay que instalarlo en el sistema.
- El binario se compila **para la plataforma donde corrés el build**, que es la misma donde `swarm-init` lo instala. Si movés el repo a otro sistema operativo, recompilá.
- `tui/build.ts` existe porque OpenTUI carga su core nativo desde un paquete por plataforma. npm solo instala el que corresponde a la máquina, pero el bundler recorre las demás ramas igual; el script las stubea para que el build no dependa de instalar paquetes de otras plataformas.
- Los colores viven en un único archivo, `tui/src/theme.ts`: una paleta cruda, tokens semánticos y el tema que la UI consume. Para cambiar el color principal (violeta claro) editá `PALETTE.violet300` y nada más. `NO_COLOR` selecciona automáticamente el tema monocromo.
- Al presionar Enter sobre un rol, la TUI le entrega la terminal a `herdr` (`herdr --session <sesión>`) para adjuntarte a ese workspace; al salir, la TUI recupera la terminal. `tui/dev-preview.sh` simula los cuatro workspaces con `herdr` sin gastar agentes reales.

## Herdr y Windows

`./swarm` usa [Herdr](https://herdr.dev) (binario Rust, un solo archivo) en vez de tmux para levantar y direccionar la terminal de cada rol. Como Herdr corre nativamente en Windows, macOS y Linux, `./swarm` funciona en Windows sin WSL — antes tmux lo bloqueaba ahí. Cada corrida del swarm usa una sesión con nombre de Herdr (`swarmforge-<hash del path>`, ver `.swarmforge/herdr-session`), aislada de cualquier otra sesión de Herdr en la misma máquina; `swarm-cleanup.sh` cierra los cuatro workspaces cuando el rol `specifier` termina.

## Usar OpenCode

Durante `swarm-init` puedes elegir `opencode` para cualquier rol. También puedes editar una fila de `swarmforge/swarmforge.conf` posteriormente. Por ejemplo:

```text
window coder opencode coder --model provider/model --agent build --auto
```

Los argumentos posteriores al worktree (`--model`, `--agent`, `--auto`, etc.) se pasan directamente a OpenCode. SwarmForge inicia `opencode` en el worktree del rol y entrega las instrucciones de constitución y rol mediante `--prompt`. OpenCode debe estar autenticado/configurado antes de iniciar el swarm; `--auto` aprueba permisos automáticamente y debe usarse solo si lo deseas.

## Usar Hermes

Hermes congela la ruta de una sesión (modelo, provider, reasoning) cuando la crea y **rechaza cambiarla en una reanudación** (`Resume retains gateway session policy; creation overrides are unsupported on resume`). Por eso SwarmForge no usa `-c <nombre> --create-if-missing`: esa vía crea la sesión con los defaults del perfil y después rechaza los flags del rol.

- Arranque en frío: `hermes chat --in <worktree> --provider <p> --model <m> --reasoning <n> --yolo --query-file <prompt>`. La sesión nace con la ruta del rol.
- Reanudación: `hermes chat --in <worktree> --resume latest -q "<wake up>"`. `--in` acota la búsqueda a ese worktree, así que cada rol retoma su propia sesión (verificado: un worktree no toma la sesión más reciente de otro).

Dos consecuencias: cambiar el modelo en `swarmforge/swarmforge.conf` solo aplica a la próxima sesión nueva (la reanudación conserva la ruta congelada), y cada arranque fallido dejaba sesiones basura que se limpian con `hermes sessions list` / `hermes sessions delete <id> --yes`.

Hermes tiene su propio almacén de credenciales, separado del de OpenCode: el provider del rol tiene que estar autenticado **en Hermes** (`hermes auth list`, `hermes setup`), o el agente muere con 401 aunque el comando sea correcto.

Por eso, cuando elegís `hermes` en el wizard, `swarm-init` le pregunta a Hermes qué puede usar de verdad (probe en `swarmforge/scripts/swarm_python/hermes_probe.py`):

```text
Credenciales de Hermes detectadas:
  ✓ openai-codex           ChatGPT or Codex Subscription (verificado con gpt-6.1-sol)
  ✓ opencode-go            OpenCode Go (verificado con qwen3.7-plus)
```

El menú de proveedor y de modelo se arma con lo que el probe devuelve: solo los providers con credencial utilizable, y los modelos que Hermes realmente expone para ese provider (su catálogo propio, no el catálogo estático del swarm).

El probe tiene tres capas, porque ninguna sola alcanza:

1. **La lista de Hermes** (`hermes auth list`) dice qué providers tienen credencial y cuál va a usar el pool. Ojo: el estado que muestra es el **último intento fallido**, no la salud de la credencial — sobrevive al reemplazo de la key, así que un provider sano puede aparecer marcado como fallido.
2. **La verificación real**: el wizard ofrece comprobarlo con una llamada por provider (`hermes chat --source tool --oneshot`, que no deja sesión a la vista). Es la única verdad: un 401/403 significa que la credencial está muerta; cualquier otra respuesta (un modelo deprecado, un modelo que tu plan no cubre) significa que la key **sí** autenticó y el problema es el modelo.
3. **El catálogo del provider**: los modelos salen del caché propio de Hermes. Los curados por el swarm van primero, porque la lista del provider viene sin orden y suele arrancar por modelos ya deprecados.

Cuando un provider falla la verificación, queda fuera de la lista y el wizard imprime el comando exacto para arreglarlo (`hermes auth add`, `hermes auth priority` para promover otra credencial, `hermes auth remove` para borrar la caída). Si el probe no puede correr (sin `python3`, sin Hermes, sin caché de modelos), el wizard cae al catálogo estático de `provider-models.tsv` como siempre.

## Descubrimiento de modelos por agente

Para todos los agentes salvo Hermes (que tiene su probe propio, arriba), `swarm-init` consulta al CLI de cada agente en lugar de confiar solo en `provider-models.tsv` (probe en `swarmforge/scripts/swarm_python/agent_probe.py`). Tres capas: (1) estado de instalación y autenticación, que solo avisa y muestra el comando de login (`claude auth login`, `codex login`, `opencode auth login`, `kiro-cli login`), sin bloquear; (2) listado vivo de modelos; (3) si no hay listado o falla, las filas estáticas del TSV.

| Agente | Autenticación | Listado de modelos |
| --- | --- | --- |
| `claude` | `claude auth status` | no existe comando: se usa el TSV (alias `opus`/`sonnet`/`haiku`) y "Otro" admite cualquier id |
| `codex` | `codex login status` | `codex debug models` |
| `opencode` | `opencode auth list` | `opencode models` (solo providers autenticados, más el gratuito `opencode`) |
| `pi` | `auth.json` y variables de entorno de providers | `pi --list-models` |
| `kiro-cli` | `kiro-cli whoami --format json` | `kiro-cli chat --list-models --format json` |
| `copilot`, `grok` | desconocida | solo TSV |

`kiro-cli` se lanza como `kiro-cli chat --model <id> [--trust-all-tools] "<prompt>"`; no se reanuda entre reinicios. El probe nunca imprime secretos: solo nombres de credenciales o variables.

## Auto-aprobación de permisos

`swarm-init` pregunta si los agentes deben auto-aprobar los permisos de sus herramientas (por defecto, sí). Un swarm desatendido se bloquea si un agente queda esperando confirmación, así que la auto-aprobación es el modo recomendado. Los flags se agregan a cada fila de `swarmforge/swarmforge.conf` según el backend elegido:

| Backend | Flag |
| --- | --- |
| `claude` | `--dangerously-skip-permissions` |
| `codex` | `--dangerously-bypass-approvals-and-sandbox` |
| `copilot` | `--allow-all` |
| `grok` | `--permission-mode bypassPermissions` |
| `hermes` | `--yolo` |
| `opencode` | `--auto` |
| `pi` | (sin flag: no pide permisos por herramienta) |

Ten en cuenta el riesgo: con auto-aprobación, cada agente puede leer, escribir y ejecutar comandos sin confirmación. Los worktrees dan recuperación a nivel git, pero ejecuta el swarm solo en proyectos de confianza. Para una instalación ya inicializada, agrega el flag correspondiente manualmente al final de cada fila de `swarmforge/swarmforge.conf`.

## Ciclo de agentes

El swarm trabaja por ciclos completos de feature, no por idas y vueltas pequeñas.

```
specifier --una feature del plan---> coder
coder     --cada cambio-------------> reviewer
reviewer  --retrabajo---------------> coder
reviewer  --revisión aprobada-------> architect   (siempre)
architect --ajustes-----------------> coder       (vuelve por el reviewer)
architect --arquitectura sana-------> specifier   (feature completa)
specifier: merge sin commit ni push, espera revisión del usuario
```

- **specifier**: entrevista al usuario pregunta por pregunta y divide el módulo en features secuenciales de ~400 líneas de código de producción (ver "Plan de features").
- **coder**: recibe de cualquier rol, implementa la feature que recibe (más la visión general del plan) con TDD y entrega siempre al reviewer.
- **reviewer**: revisa contra la especificación como base absoluta, ejecuta los tests, se conecta a APIs y bases reales, cubre lo que falte, y reenvía siempre al architect.
- **architect**: revisa la arquitectura completa, código muerto y ubicación de archivos; manda ajustes al coder o la señal de cierre al specifier.

El ruteo no es solo una convención de prompts: `swarm_handoff.sh` rechaza los handoffs que salen del ciclo.

## Plan de features

El specifier no entrega el módulo entero: lo divide en features **secuenciales** y las entrega al coder de a una.

1. El specifier te entrevista (una pregunta a la vez) y escribe el plan en `.swarmforge/plan/` del proyecto: `plan.json`, `general.md` (visión general compartida) y `features/<id>.md` (una por feature). Todas se dimensionan en ~400 líneas; es una guía, nunca un límite duro: el tamaño solo genera advertencias.
2. Revisas la división y, si está bien, la apruebas tú desde tu terminal con `./swarm plan approve`. El comando se niega a correr dentro de un agente, y mientras el plan esté en `draft` el swarm rechaza cualquier handoff del specifier al coder.
3. El specifier entrega **solo la siguiente feature lista** (`task:` = id de la feature). El swarm rechaza el handoff si no hay plan aprobado, si el id no existe, si la feature no está `pending`, si sus dependencias no están `merged` o si hay otra feature en curso. Al encolarse, la feature pasa a `coding`. La aprobación guarda un hash del contenido del plan (sin los estados) en `~/.swarmforge/approvals/`; si el specifier modifica `plan.json` o algún `.md` después de aprobar, el handoff se rechaza hasta que vuelvas a ejecutar `./swarm plan approve`. Es un control de integridad, no una barrera de seguridad.
4. El coder ve la visión general más solo el slice de su feature; nunca las demás.
5. Cuando una feature se mergea, marca el estado a mano (paso temporal hasta automatizarlo): `./swarm plan set-status <id> merged`. Solo entonces el specifier puede entregar la siguiente.

Comandos (se ejecutan desde la raíz del proyecto o desde cualquier worktree; el plan siempre se lee del checkout principal):

| Comando | Qué hace |
| --- | --- |
| `./swarm plan validate` | Valida el plan: errores (campos, ids duplicados, dependencias inexistentes o en ciclo, criterios vacíos, archivos faltantes) y advertencias de tamaño. |
| `./swarm plan show` | Muestra el plan completo. |
| `./swarm plan status` | Estado de cada feature y cuál es la próxima lista. |
| `./swarm plan approve` | Valida y aprueba el plan. Solo lo puede ejecutar una persona. |
| `./swarm plan set-status <id> <estado>` | Cambia el estado de una feature (`pending`, `coding`, `review`, `arch`, `awaiting_merge`, `merged`). Solo una persona. |

## Memoria compartida (engram)

Los cuatro roles comparten memoria de proyecto mediante [Engram](https://github.com/Gentleman-Programming/engram), un binario Go agnóstico de agente con CLI, MCP y SQLite+FTS5 local. `engram` es requisito obligatorio: `swarm_cli.py` corre `require("engram")` junto con `herdr` y `git`, así que `./swarm` no arranca si falta.

- `./swarm` exporta `ENGRAM_PROJECT` para cada workspace de herdr, con el mismo valor (el nombre del directorio raíz del proyecto) para los cuatro roles y sus cuatro worktrees distintos. Esto evita que la detección automática de Engram por directorio de trabajo (`cwd`) invente un proyecto de memoria diferente por worktree.
- Los agentes usan la CLI de `engram` (`engram context`, `engram search`, `engram save`, ...) en vez de depender de que cada backend tenga configurado el servidor MCP: `grok` y `hermes` no tienen soporte oficial de Engram, así que la CLI es el único camino que funciona igual para los siete backends soportados.
- El protocolo completo (cuándo orientarse, cuándo guardar, cómo usar `topic_key`) vive en `template/swarmforge/scripts/shared-articles/memory.prompt` y lo obedecen los cuatro roles vía la constitución.
- Bootstrap de un proyecto nuevo: si `engram search ... --project "$ENGRAM_PROJECT"` no devuelve nada, el specifier es quien crea la primera memoria (`engram save "Project overview" ... --type architecture --topic architecture/overview --project "$ENGRAM_PROJECT"`) una vez aprobada la primera spec. Ese primer `save` es lo que da de alta el proyecto en Engram; el resto de los roles lo encuentran después solo por compartir el mismo `ENGRAM_PROJECT`, sin que el handoff tenga que transportar el contenido de la memoria.
- `--project "$ENGRAM_PROJECT"` es obligatorio en cada llamada a `engram`: lo comprobé contra una instalación real y sin el flag explícito, `save`/`search`/`context` no aíslan por proyecto (un `save` sin `--project` queda huérfano, sin proyecto asignado). El env var por sí solo no alcanza.

## Configuración por rol

`swarm-init` pregunta para `specifier`, `coder`, `reviewer` y `architect`, en este orden: backend (`claude`, `codex`, `copilot`, `grok`, `opencode` o `pi`), modelo y nivel de thinking/effort. Los cuatro roles pueden usar backends distintos.

Al elegir OpenCode, aparece un menú de modelos legible (por ejemplo, `OpenCode Go — Kimi K3`); el inicializador guarda internamente su identificador canónico sin espacios (`opencode-go/kimi-k3`). El catálogo local está en `provider-models.tsv` (columnas `agent`, `provider`, `provider_label`, `model_id`, `model_label`; cubre los siete agentes soportados, no solo OpenCode); se actualiza deliberadamente en `swarm-local`, no mediante una descarga durante la inicialización.

Para los roles `pi`, el inicializador guarda el modelo como `openai-codex/modelo`, por ejemplo `--model openai-codex/gpt-5.6-terra`; así Pi resuelve explícitamente el proveedor Codex y no hereda Azure como predeterminado. Pi usa `--thinking`, Claude usa `--effort` y Codex usa `-c model_reasoning_effort=...`. OpenCode, Copilot y Grok reciben el modelo; sus opciones de razonamiento dependen de su configuración/proveedor y puedes añadir sus flags específicos manualmente a `swarmforge.conf`.

`template/` es la fuente de verdad de las configuraciones compartidas. Los cambios de un proyecto ya inicializado no modifican esta plantilla.
