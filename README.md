# Swarm local

Plantilla local y autónoma para iniciar SwarmForge en otros proyectos con agentes Pi u OpenCode. No descarga `swarm-forge` ni consulta GitHub al ejecutar `swarm-init` o `./swarm`.

## Instalación (sin clonar el repo)

Igual que Claude Code, opencode o Hermes: un comando instala el CLI `swarm-kura` y otro lo actualiza.

```sh
# macOS / Linux / Git Bash
curl -fsSL https://raw.githubusercontent.com/goncalodjay/swarm-local/master/install.sh | bash
```

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/goncalodjay/swarm-local/master/install.ps1 | iex
```

El instalador:

1. Detecta el sistema y la arquitectura (`darwin-arm64`, `darwin-x64`, `linux-x64`, `linux-arm64`, `windows-x64`; bajo Rosetta instala la build nativa arm64).
2. Revisa las dependencias y ofrece instalar las que faltan con el gestor del sistema: Homebrew en macOS, `apt`/`dnf`/`pacman`/`zypper`/`apk` en Linux, `winget` en Windows. Pregunta antes (`--yes` para no preguntar, `--no-deps` para saltear).
3. Descarga el bundle de la última GitHub Release para tu plataforma (`swarm-kura-<os>-<arch>.tar.gz`: TUI precompilada + toolchain Gherkin), verifica su sha256 y lo deja en `~/.swarm-kura/versions/<versión>`. Si todavía no hay releases publicadas, instala la rama principal y compila la TUI una vez con Bun (lo instala solo si hace falta).
4. Activa esa versión (`~/.swarm-kura/current`), crea `~/.local/bin/swarm-kura` y lo agrega al `PATH` de tu shell (zsh, bash o fish). Se conservan las últimas 3 versiones.

Opciones: `--version vX.Y.Z` (una release concreta), `--branch <rama>` (canal de desarrollo; `update` lo recuerda), `--yes`, `--no-deps`, `--no-modify-path`, `--from <dir>` (instalar desde un checkout local). Variables: `SWARM_KURA_REPO`, `SWARM_KURA_HOME`, `SWARM_KURA_BIN_DIR`, `GITHUB_TOKEN`.

### Comandos

| Comando | Qué hace |
| --- | --- |
| `swarm-kura init [dir] [-l "Go, SQL"]` | Instala el swarm en el proyecto (wizard de lenguajes, agentes, modelos y thinking). |
| `swarm-kura update` | Baja la última versión de swarm-kura y, si estás dentro de un proyecto, lo actualiza también (`swarm-init --update`: scripts, prompts, TUI, toolchain; tu `swarmforge.conf` se conserva). `--check` solo avisa, `--no-project` solo actualiza el CLI, `--force` reinstala, `-y` sin confirmaciones. |
| `swarm-kura change` | Cambia el agente, el modelo o el thinking de uno o más roles sin repetir la instalación (ver abajo). |
| `swarm-kura run` · `stop` · `tui` · `plan ...` | Atajos a `./swarm`, `./swarm stop`, `./swarm tui`, `./swarm plan` del proyecto (buscan el proyecto hacia arriba desde el directorio actual). |
| `swarm-kura doctor` | Revisa bash ≥ 4, Python ≥ 3.10, git, herdr, engram, los agentes instalados y el estado del proyecto (versión, TUI, toolchain de la plataforma correcta). |
| `swarm-kura version` · `uninstall` | Versión/canal instalado · desinstala (no toca los proyectos). |

### Cambiar agentes o modelos: `swarm-kura change`

Interactivo: muestra la tabla de roles, elegís uno o varios (`2`, `1,3`, `a` = todos) y para cada uno si cambiás **solo el modelo**, **el agente (y su modelo)** o **solo el thinking**. Los modelos salen del mismo descubrimiento en vivo que usa `swarm-init` (con el catálogo estático como respaldo y "Otro" para cualquier id). Muestra el diff, pide confirmación y guarda; el archivo anterior queda como `swarmforge.conf.bak.<fecha>`.

Sin preguntas, para los casos típicos:

```sh
swarm-kura change architect --model opencode-go/kimi-k3          # minimax no anda hoy: solo ese rol
swarm-kura change --from-agent claude --agent codex --model gpt-5.6-luna   # sin créditos en Claude
swarm-kura change --all --thinking high
swarm-kura change --list
```

Se conservan el worktree, el modo `batch`, la auto-aprobación (se traduce al flag del agente nuevo) y los argumentos extra propios (si el agente no cambia). Un rol con agente o modelo distinto arranca una **sesión nueva** en su próximo lanzamiento: el scheduler guarda con qué backend creó cada sesión y no reanuda una sesión de otro agente/modelo. Si el swarm está corriendo, el cambio se aplica al reiniciarlo (`swarm-kura stop && swarm-kura run`).

## Requisitos

| Herramienta | Versión mínima | Notas |
| --- | --- | --- |
| `bash` | 4.0 | macOS trae bash 3.2 en `/bin/bash`: el instalador instala uno nuevo con `brew install bash`. `swarm-init` se re-ejecuta solo con el bash ≥ 4 que encuentre. `install.sh` y `swarm-kura` funcionan con 3.2. |
| `python3` | 3.10 | macOS trae 3.9 en `/usr/bin/python3`: el instalador ofrece `brew install python@3.12`. Los scripts buscan `python3.1x`/Homebrew antes de rendirse; `SWARMFORGE_PYTHON` fuerza uno. |
| `herdr` | última estable | `brew install herdr` o `curl -fsSL https://herdr.dev/install.sh \| sh` (Windows: `irm https://herdr.dev/install.ps1 \| iex`). |
| `engram` | última estable | `brew install gentleman-programming/tap/engram` o ver [instalación de Engram](https://github.com/Gentleman-Programming/engram/blob/main/docs/INSTALLATION.md). |
| `git`, `curl`, `tar` | — | preinstalados casi siempre. |
| CLIs de agentes | — | `opencode`, `codex`, `claude`, `copilot`, `grok`, `hermes`, `kiro-cli`, `pi`: solo los que uses. |

Bun/Node solo hacen falta para compilar la TUI cuando no hay binario publicado; el instalador se encarga.

## Desde un clon (desarrollo)

`./install_swarm.sh /ruta/al/proyecto` sigue funcionando desde un clon del repo (instala o, si ya hay instalación, actualiza con `swarm-init --update`). También podés usar el CLI desde el clon: `bin/swarm-kura ...`, o instalar el checkout con `./install.sh --from .`.

## Actualizar una instalación existente

`swarm-kura update` (o `swarm-init --update /ruta/al/proyecto` desde un clon) **actualiza** la instalación; no hace falta borrar nada.

**Se reemplaza** todo lo que es de la plantilla: `swarmforge/scripts/`, los prompts de rol que trae la plantilla (`swarmforge/roles/*.prompt`), `swarmforge/constitution.prompt` y `swarmforge/constitution/`, `swarm`, `swarm.cmd`, `.swarmforge/toolchain` y el binario del TUI. Se agregan `.swarmforge/` y `.worktrees/` a `.gitignore` si faltan (nunca se quita nada). Los lenguajes se leen de `project.prompt` o de `--languages`.

**`swarmforge.conf` se reconcilia**, no se pisa: se conservan tus filas de agente/modelo de los roles que existen en la plantilla, se pregunta (con el asistente habitual) solo por los roles nuevos de la plantilla, y se quitan los roles que la plantilla ya no tiene (por ejemplo `refactorer`). El archivo anterior queda como `swarmforge/swarmforge.conf.bak` (con marca de tiempo si ya existía uno). Los prompts de rol propios que la plantilla no trae se conservan, con un aviso.

**Antes de tocar nada** se revisa si hay un swarm en ejecución, archivos con cambios sin commit entre los que se van a sobrescribir y worktrees sucios. Si no hay nada en riesgo, sigue sin preguntar. Si lo hay, lista exactamente qué, recomienda hacer commit de tu trabajo y pide confirmación `(s/N)`:

- `N` o Enter: cancela con código distinto de 0 y no modifica nada.
- `s`: detiene el swarm, copia los archivos en riesgo a `.swarmforge/backup-<fecha-hora>/` y actualiza. Nunca se borra un worktree ni una rama con cambios sin commit; para los roles obsoletos con worktree limpio se ofrece eliminarlo, y si no, se imprimen los comandos `git worktree remove` / `git branch -d`.
- Sin terminal (CI, pipes) la actualización se cancela salvo que pases `--yes`. Si la plantilla agregó roles nuevos y no hay terminal para elegir su agente, también se cancela.

El estado vivo (`.swarmforge/handoffs`, logs, notify, sesiones y `agents.json` de los roles vigentes) se conserva, de modo que `./swarm` retoma las sesiones. `.swarmforge/kura-version` registra con qué versión de swarm-kura se instaló o actualizó el proyecto (`swarm-kura update` lo usa para saber si el proyecto quedó atrás).

## Uso

Desde la raíz de un proyecto:

```sh
swarm-kura init               # o, desde un clon: ./install_swarm.sh .
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

`./swarm` requiere `python3` (3.10+), `herdr`, `git` y el ejecutable configurado para cada rol (`pi`, `opencode`, `claude`, `codex`, `copilot`, `grok`, `hermes` o `kiro-cli`). La plantilla incluye localmente `gherkin-parser`, `gherkin-ir-dry-checker` y `gherkin-mutator` (build por plataforma, ver [Herramientas Gherkin por plataforma](#herramientas-gherkin-por-plataforma)); `swarm-init` instala el que corresponde al equipo bajo `.swarmforge/toolchain/bin` y los añade al `PATH` de cada agente. Las herramientas de mutación, CRAP y DRY específicas de cada lenguaje siguen siendo una decisión del proyecto: los agentes no las descargarán y pedirán indicaciones si una tarea las requiere.

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

### Demonio resistente y UTF-8

- El demonio de handoffs (`handoffd`) no muere por un error puntual: cada ciclo (entrega, plan y scheduler) está protegido, el error se registra con traceback en `.swarmforge/daemon/handoffd.log` y el bucle sigue. Un error idéntico y persistente se registra una vez y luego solo cada 60 repeticiones con un contador, para no inundar el log.
- Todo `subprocess` en modo texto decodifica en UTF-8 con `errors="replace"` (en Windows el códec por defecto, cp1252, rompía con bytes que `herdr` imprime). El demonio y los paneles de los agentes arrancan con `PYTHONUTF8=1` y `PYTHONIOENCODING=utf-8`.

### Parking verificado y reintentos de wake-up

- Al aparcar un agente ocioso, el scheduler envía la interrupción y **verifica** que el proceso en primer plano del panel volvió a ser el shell; solo entonces marca el rol como `parked`. En Windows se envía `Ctrl+C`, una pausa de ~1 s y otro `Ctrl+C`. Si el agente ignora la interrupción, el rol sigue `running` y se registra `park-pending`/`park-failed` en `handoffd.log`.
- Nunca se escribe el launcher (`.swarmforge/prompts/<rol>.sh`) en un panel donde el agente sigue vivo (evita una segunda sesión vacía): primero se vuelve a detener y verificar; si no se consigue, el lanzamiento se difiere (`launch-deferred`) y se reintenta en el siguiente ciclo. Si herdr no informa el proceso en primer plano, se registra y, tras 3 intentos, se lanza igualmente (`launch-unverified`).
- Con correo sin leer en `inbox/new`, el wake-up se reenvía con espera creciente (15, 30, 60 y 120 s) hasta que el agente lo recoge; los 3 primeros intentos no exigen que herdr marque el panel ocioso. Tras 6 intentos en vano se detiene (verificado) y se relanza al agente. Nunca se escribe en un panel que está en el prompt del shell (agente muerto: se relanza).
- Reglas de seguridad: el último intento de wake-up solo detiene y relanza a un agente tras 30 min de espera (`SWARMFORGE_WAKE_GIVEUP_MS`) y 3 ciclos seguidos en estado ocioso; el primer rol (el specifier, el que habla contigo) nunca se detiene automáticamente y solo recibe texto cuando herdr lo marca ocioso. No se despierta a un agente que aún está arrancando (~30 s), y se detiene como máximo un agente por ciclo. El recordatorio de tarea atascada solo aplica a coder, reviewer y architect, y solo a handoffs con `task`.
- El correo que llega mientras hay una tarea en `in_process` se lee cuando el agente ejecuta `done_with_current`/`ready_for_next`.
- Si una tarea lleva más de 5 min en `in_process` con el agente ocioso y sin respuesta enviada, se le recuerda continuar o ejecutar `done_with_current`. `agents.json` antiguo sigue funcionando (los campos nuevos se rellenan solos).

### `done_with_current` y `--no-reply`

El coder, el reviewer y el architect deben enviar su propio `git_handoff` de la tarea **antes** de correr `done_with_current`. Si no lo hicieron, el script se niega (`REPLY_REQUIRED`, código 2) y no completa nada, para que una feature no quede colgada sin que nadie la reciba. El specifier no está sujeto a esta regla (cierra la cadena) ni las notas. Para un paso que legítimamente termina sin respuesta: `done_with_current --no-reply`.

### Agregar `swarm` al PATH

`swarm` y `swarm.cmd` viven en la raíz del repo. Los agentes ya lo reciben en su `PATH` automáticamente. Para usarlo desde tu propia terminal agrega esa carpeta al `PATH`: en Windows, variable de entorno `Path` del usuario (Configuración > Variables de entorno) y reabre la terminal; en Linux/macOS, `export PATH="$PATH:/ruta/al/repo"` en `~/.bashrc` o `~/.zshrc`.

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
reviewer  --verdict: fail-----------> coder       (retrabajo; con el tope de rondas escala al architect)
reviewer  --verdict: pass-----------> architect   (siempre)
architect --ajustes-----------------> coder       (vuelve por el reviewer)
architect --arquitectura sana-------> specifier   (feature completa: el swarm publica el PR)
código    --PR + detección de merge + siguiente feature--> coder   (automático, ver "Plan de features")
```

- **specifier**: entrevista al usuario pregunta por pregunta y divide el módulo en features secuenciales de ~400 líneas de código de producción (ver "Plan de features").
- **coder**: recibe de cualquier rol, implementa la feature que recibe (más la visión general del plan) con TDD y entrega siempre al reviewer.
- **reviewer**: revisa contra la especificación como base absoluta, ejecuta los tests, se conecta a APIs y bases reales, cubre lo que falte, y reenvía al architect cuando la revisión pasa. Todo `git_handoff` del reviewer lleva el header obligatorio `verdict: pass|fail` y el ruteo sale de ahí: `pass` va solo al architect, `fail` va solo al coder (salvo al llegar al tope de rondas, ver abajo).
- **architect**: revisa la arquitectura completa, código muerto y ubicación de archivos; manda ajustes al coder o la señal de cierre al specifier.

El ruteo no es solo una convención de prompts: `swarm_handoff` rechaza los handoffs que salen del ciclo.

## Plan de features

El specifier no entrega el módulo entero: lo divide en features **secuenciales** y las entrega al coder de a una.

1. El specifier te entrevista (una pregunta a la vez) y escribe el plan en `.swarmforge/plan/` del proyecto: `plan.json`, `decisions.md` (registro de la entrevista), `general.md` (visión general compartida) y `features/<id>.md` (una por feature).
   - **La entrevista es obligatoria y se verifica en código.** Primero entrevista el módulo **entero** (objetivo de negocio, consumidores, datos, modos de fallo, integraciones, restricciones, definición de "hecho") y recién después lo parte en features. Mientras no termine, no puede escribir `plan.json`, `general.md` ni `features/`; un pedido vago como "armá el plan" no la saltea. Cada pregunta cerrada queda en `decisions.md` como `## D<n>: <pregunta>` seguida de una línea `Decision: <respuesta>` (y `Why:` opcional). `./swarm plan validate` y `./swarm plan approve` fallan si faltan decisiones: por defecto el mínimo es 5, ajustable con `min_decisions` (entero >= 0) en `plan.json`. `decisions.md` entra en el hash de la aprobación: editarlo después obliga a re-aprobar. Los planes **ya aprobados** antes de este cambio no se traban: el handoff al coder sigue validando la estructura clásica y el hash de aprobación, pero **no** exige `decisions.md` ni las secciones nuevas. Esas exigencias aplican solo a `./swarm plan validate` y `./swarm plan approve`; para re-aprobar un plan viejo hay que escribir `decisions.md` con al menos N entradas `## D<n>:`, agregar las 5 secciones a cada feature y volver a ejecutar `./swarm plan approve`. Los ids `D<n>` deben ser únicos, y las respuestas vacías o de relleno (`TBD`, `<respuesta>`, `[completar]`, `none`, `n/a`) no cuentan; el error nombra las rechazadas. La respuesta puede empezar en la línea siguiente a `Decision:`.
   - **~400 líneas limita el CÓDIGO de cada feature, nunca el detalle de la especificación.** Un archivo de feature corto no es una especificación completa: cada `features/<id>.md` debe tener las secciones `## Behavior`, `## Edge cases`, `## Acceptance examples`, `## Out of scope` y `## Files`, todas con contenido (nombres, campos, firmas y rutas exactos; ejemplos entrada -> salida). `validate` falla si falta una sección o está vacía. El tamaño de los archivos del plan sigue generando solo advertencias.
2. Revisas la división y, si está bien, la apruebas tú desde tu terminal con `./swarm plan approve`. El comando se niega a correr dentro de un agente, y mientras el plan esté en `draft` el swarm rechaza cualquier handoff del specifier al coder.
3. El specifier entrega **solo la siguiente feature lista** (`task:` = id de la feature). El swarm rechaza el handoff si no hay plan aprobado, si el id no existe, si la feature no está `pending`, si sus dependencias no están `merged` o si hay otra feature en curso. Al encolarse, la feature pasa a `coding`. La aprobación guarda un hash del contenido del plan (sin los estados) en `~/.swarmforge/approvals/`; si el specifier modifica `plan.json` o algún `.md` después de aprobar, el handoff se rechaza hasta que vuelvas a ejecutar `./swarm plan approve`. Es un control de integridad, no una barrera de seguridad.
4. El coder ve la visión general más solo el slice de su feature; nunca las demás.
5. Cuando el architect da una feature por completa, **el código** (no un agente) la publica: sube el commit final a la rama `swarm/<plan>/<feature>` (sin tocar tu checkout, sin `--force`, nunca a la rama base) y abre un PR hacia la rama base con `gh pr create`. La rama base es la que tenías activa al ejecutar `plan approve` (queda en `base_branch` de `plan.json`). El PR lleva resumen, criterios de aceptación como checklist, métricas informativas (líneas de producción frente a ~400, archivos fuera de `allowed_paths`), rondas de revisión, enlace al PR anterior y el reporte del architect.
6. **Las features esperan a que tú mergees cada PR.** El swarm nunca mergea ni pushea a la rama base. El demonio revisa cada 30 s (`SWARMFORGE_PLAN_POLL_S`) si el PR quedó `MERGED` (con `gh pr view`) o si el commit final ya es alcanzable desde `origin/<rama base>`; al detectarlo marca la feature `merged`, hace `git fetch`, avanza tu checkout con fast-forward (solo si está limpio y en la rama base), reinicia las ramas `swarmforge-<rol>` de los worktrees (solo si están limpios y sin trabajo pendiente; si no, reintenta en el siguiente ciclo sin perder nada) y entrega solo la siguiente feature al coder pasando por la misma validación del handoff manual. Con la última feature mergeada el plan pasa a `done`. El specifier recibe una nota corta en cada paso.
   - **Sincronización antes de despachar:** el despacho de la siguiente feature y la sincronización van juntos. Tras detectar el merge, el código hace `git fetch`, avanza tu checkout con fast-forward (solo si no tiene cambios en archivos versionados) y reinicia los worktrees ociosos al tip de la rama base; el handoff usa ese commit post-merge. La sincronización es **por rol**: un worktree trabado ya no deja a los demás en la base vieja. Solo el worktree del **coder** bloquea el despacho (es el que construye la feature siguiente): si está trabado, el despacho queda pendiente (queda en `.swarmforge/logs/plan-flow.log` como `sync-deferred ... NOT dispatched`) y se reintenta en el siguiente ciclo sin perder nada. Los demás roles bloqueados se registran (`worktree-blocked`), se avisa al specifier con una nota y se reintentan en los ciclos siguientes hasta que se los puede mover. Las carpetas sin versionar `swarmforge/` y `.swarmforge/` que el swarm copia en cada worktree **no** cuentan como cambios; cualquier otro archivo sin versionar o modificado sí bloquea. Si tu checkout principal tiene cambios, no se toca y el specifier recibe una nota, pero el commit del handoff sigue siendo el tip de la base.
   - **La feature arranca sobre la base, no sobre la historia vieja:** el commit base de la feature se toma del `commit:` del handoff (después de comprobar que es el tip de la rama base recién consultada), no del HEAD que el checkout del specifier tenga en ese momento. Y antes de encolar la feature, el swarm comprueba que el worktree del coder descienda de ese commit: si está atrasado lo adelanta con fast-forward, si tiene la historia vieja del merge por squash lo reinicia (guardando los commits anteriores en `refs/swarmforge/backup/<rol>/<fecha>`) y si tiene trabajo propio que no está en la base **rechaza el handoff** con un mensaje claro. Así el problema aparece al **empezar** la feature y no al publicar el PR, cuando el ciclo entero de código/revisión/arquitecto ya se gastó. Cuando el coder entrega su trabajo, si el commit no desciende del commit base el swarm avisa (en el handoff al reviewer) para que rebase antes de terminar la revisión.
   - **Commit obsoleto rechazado:** el specifier debe correr `git fetch origin --prune` y `git pull --ff-only` antes de cada handoff al coder. `swarm_handoff` repite el `fetch` y rechaza un `commit:` del specifier que quede detrás de `origin/<base_branch>` (o de la rama base local si no hay remoto). Si no hay remoto, no hay red o el `fetch` falla, avisa y deja pasar. `SWARMFORGE_SKIP_BASE_CHECK=1` lo desactiva (pruebas).
   - **Seguridad del flujo:** la rama base y la URL de `origin` quedan fijadas al ejecutar `plan approve`; si cambian, el swarm se niega a pushear y avisa. Las respuestas de `gh` solo se aceptan si coinciden con la rama base, la rama `swarm/...` y el commit publicado. Antes de mover una rama `swarmforge-<rol>` con commits locales sin integrar se crea una copia en `refs/swarmforge/backup/<rol>/<fecha>`. El trabajo de git/gh corre en un hilo aparte del demonio, con reintentos cada vez más espaciados (hasta 5 min) si algo falla. Si un PR se cierra sin mergear, el specifier recibe una nota y la feature se republica con `swarm plan publish <id>`. Sin `gh` autenticado los merges por squash o rebase no se detectan (el commit no queda alcanzable): corré `swarm plan set-status <id> merged`; en ese caso el swarm avanza tu checkout pero no reinicia los worktrees ni despacha solo la siguiente feature.
7. Si no hay `origin`, el push falla o falta `gh`, el demonio no se cae: lo registra en `.swarmforge/logs/plan-flow.log`, avisa al specifier con una nota y la feature queda en `awaiting_merge`. Corrige la causa y ejecuta `./swarm plan publish <id>`.

Los estados se mueven solos con los handoffs de la feature (solo para tareas cuyo `task:` es el id de una feature del plan; el trabajo fuera del plan no se toca):

| Handoff | Cambio de estado |
| --- | --- |
| specifier -> coder | `pending` -> `coding` (guarda el `base_commit`) |
| coder -> reviewer | `coding` -> `review` |
| reviewer (`fail`) -> coder | `review` -> `coding` y `review_rounds` + 1 |
| reviewer (`pass`) -> architect | `review` -> `arch` |
| architect -> coder (ajustes) | `arch` -> `coding` |
| architect -> specifier | `arch` -> `awaiting_merge` (el swarm publica el PR; `awaiting_merge` = PR abierto) |
| PR mergeado (detectado por el demonio) | `awaiting_merge` -> `merged`, y la siguiente feature pasa a `coding` |
| tú: `set-status <id> merged` | alternativa manual si la detección no ve el merge |

- **Tope de rondas de revisión**: `plan.json` acepta `max_review_rounds` (entero >= 1, por defecto 3). Al alcanzarlo, un `verdict: fail` ya no vuelve al coder: el swarm lo acepta solo si va al architect, marca la feature con `escalated: true` y el architect decide si corrige los defectos restantes o los acepta y los informa al specifier. El contador se reinicia al reservar la feature y cuando una feature `awaiting_merge` se reabre a `coding`; el retrabajo `arch` -> `coding` lo conserva.
- **Métricas informativas**: en coder -> reviewer el swarm calcula con git las líneas de producción cambiadas (sin tests, lockfiles ni `docs/*.md`) y los archivos fuera de `allowed_paths`, las guarda en `feature.metrics`, las muestra en el stderr del coder y las agrega al handoff del reviewer como bloque `Plan check:`. Son solo advertencias: nunca rechazan un handoff ni cortan el trabajo del coder.
- **Ajustes de entrega** en `plan.json` (booleanos, por defecto `true`): `auto_publish` publica el PR automáticamente y `auto_next` despacha la siguiente feature tras el merge; con `false` esos pasos quedan manuales (`./swarm plan publish <id>`, o el specifier entrega la feature). También `base_branch` (rama destino de los PR). Son ajustes de entrega, no alcance: cambiarlos **no** invalida la aprobación, igual que los estados y los campos de ejecución (`final_commit`, `pr_url`, `published_at`, `merge_commit`).
- Mientras exista un plan, un `note` del specifier al coder solo se acepta si hay una feature en curso (`coding`, `review` o `arch`); el trabajo nuevo solo entra por el handoff de una feature aprobada.
- `set-status` valida los saltos contra la tabla de arriba (y que no haya dos features en curso); para reparar un estado a mano usa `--force`.

Comandos (se ejecutan desde la raíz del proyecto o desde cualquier worktree; el plan siempre se lee del checkout principal):

| Comando | Qué hace |
| --- | --- |
| `./swarm plan validate` | Valida el plan: errores (campos, ids duplicados, dependencias inexistentes o en ciclo, criterios vacíos, archivos faltantes, `decisions.md` con menos decisiones que `min_decisions`, secciones obligatorias ausentes o vacías en una feature) y advertencias de tamaño. |
| `./swarm plan show` | Muestra el plan completo. |
| `./swarm plan status` | Estado de cada feature, su commit final y su PR, y cuál es la próxima lista. |
| `./swarm plan publish <id>` | Publica (o reintenta publicar) una feature en `awaiting_merge`: sube el commit y abre el PR. Solo una persona. |
| `./swarm plan sync` | Revisa ahora mismo si hay PR mergeados, sincroniza y despacha la siguiente feature. Solo una persona. |
| `./swarm plan rebase <id>` | Devuelve una feature trabada a `pending` y pone el worktree del coder sobre la base (los commits que se pierden quedan en `refs/swarmforge/backup/`). Es la recuperación soportada cuando una feature no se puede publicar o quedó con una base vieja. Solo una persona. |
| `./swarm plan approve` | Valida y aprueba el plan. Solo lo puede ejecutar una persona. |
| `./swarm plan set-status <id> <estado> [--force]` | Cambia el estado de una feature (`pending`, `coding`, `review`, `arch`, `awaiting_merge`, `merged`). Rechaza saltos ilegales (p. ej. `pending` -> `merged`) salvo con `--force`. Solo una persona. |

## Herramientas Gherkin por plataforma

La plantilla trae las herramientas (`gherkin-parser`, `gherkin-mutator`, `gherkin-ir-dry-checker`) en `template/toolchain/<os>-<arch>/bin/`, donde `<os>` es `linux`, `darwin` o `windows` y `<arch>` es `x64` o `arm64`. `swarm-init` (instalación y `--update`) detecta la plataforma del equipo, copia **solo** el build que corresponde a `.swarmforge/toolchain/bin` y anota el resultado en `.swarmforge/toolchain/PLATFORM`. Si mueves el proyecto a otro SO, `swarm-init --update` reinstala el build correcto.

Builds precompilados incluidos: `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64` y `windows-x64` (en Windows son `.exe`; Git Bash los resuelve por el nombre sin extensión). Para cualquier otra plataforma (`windows-arm64`) `swarm-init` compila las herramientas desde `vendor/acceptance-pipeline-specification` con `scripts/build_toolchain.sh`, siempre que Go esté en el `PATH`. Sin Go (o si faltan `scripts/build_toolchain.sh` o `vendor/`, que deben estar junto a `swarm-init`) avisa qué falta y cómo arreglarlo y sigue la instalación sin fallar: **si ya había un toolchain instalado se conserva tal cual** (nunca se reemplaza por uno vacío ni incompleto) y `PLATFORM` solo se escribe tras una instalación correcta. Antes de reemplazar, `swarm-init --update` copia el toolchain anterior (`bin/` y `PLATFORM`) a `.swarmforge/backup-<fecha>/.swarmforge/toolchain`. A mano: `scripts/build_toolchain.sh <triple> [carpeta-bin]`, por ejemplo `scripts/build_toolchain.sh darwin-arm64 .swarmforge/toolchain/bin`.

Al arrancar, `./swarm` **ejecuta** cada herramienta (`--version`, con timeout corto y sin shell) y la clasifica como `ok`, `missing` o `wrong-platform`/`not-executable`. Si alguna no sirve, avisa con el SO/arquitectura detectados, indica cómo arreglarlo (`swarm-init --update` o `scripts/build_toolchain.sh <triple>`, y si `PLATFORM` no coincide con el equipo, lo menciona) y deja el detalle en `.swarmforge/toolchain-check.txt`; no es fatal (los proyectos sin Gherkin no la necesitan). El specifier lee ese archivo y, si hay herramientas inutilizables, no genera `.feature` y lo deja dicho en `general.md`.

Alternativa: correr el swarm dentro de WSL/Linux, donde el build `linux-x64` funciona tal cual. En Windows se prefiere `<herramienta>.exe` o `.cmd` sobre el archivo sin extensión, así que también sirve un wrapper propio, por ejemplo `.swarmforge/toolchain/bin/gherkin-parser.cmd`:

```
@wsl gherkin-parser %*
```

Las herramientas se prueban en paralelo (timeout de 3 s cada una; si una se cuelga se mata su árbol de procesos). Si una herramienta se resuelve desde el `PATH` y no desde `.swarmforge/toolchain/bin`, el aviso y `.swarmforge/toolchain-check.txt` lo indican junto con la ruta usada. El swarm no descarga binarios por su cuenta.

Para refrescar los precompilados (mantenimiento): `scripts/build_toolchain.sh linux-x64` y `scripts/build_toolchain.sh windows-x64` (o `--all`) reescriben `template/toolchain/<triple>/bin`. Los binarios se marcan como `binary` en `.gitattributes` para que Git no los altere.

## Notas de uso

- **Esfuerzo del specifier**: es el rol que entrevista y escribe las especificaciones de las que depende todo lo demás, así que `swarm-init` propone `high` (en vez de `medium`) como esfuerzo por defecto para el specifier. Se aplica a los agentes que aceptan `--effort`/reasoning (p. ej. `claude`, `codex`).
- **`swarm` en el PATH de los agentes**: el comando de lanzamiento agrega la raíz del proyecto al **final** del `PATH` de cada agente, para que puedan ejecutar `swarm plan status`, etc., sin que nada del proyecto tape comandos del sistema. Los agentes ya existentes lo reciben al relanzarse.
- **Un solo repositorio**: el swarm trabaja sobre un único repo. Si el trabajo toca otros repos, el specifier debe escribir la parte del otro repo como un archivo de especificación (por ejemplo `bk-repo-spec.md`) para implementarla allí por separado, en vez de intentar tocar ambos desde este swarm.

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
