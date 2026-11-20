# Guía completa de SwarmForge local: arquitectura, agentes, prompts, handoffs, sesiones y operación

> **Qué describe este documento:** el código que existe en `C:/dev/swarm-local`, incluyendo los cambios locales sin commit presentes al realizar la revisión. No es una descripción idealizada del diseño ni una transcripción de conversaciones anteriores.
>
> **Repositorio remoto configurado:** `https://github.com/goncalodjay/swarm-local.git`.
>
> **Punto de referencia del historial:** `3210131` — `fix(swarm): harden the daemon, sync and agent wake-ups on Windows`.
>
> **Fecha de verificación:** 5 de noviembre de 2026, UTC. Las fechas de la cronología son las que registra Git.
>
> **Alcance de la comprobación:** lectura del código, prompts, scripts, configuración, workflows, historial y pruebas locales. No se iniciaron agentes reales, no se consumieron modelos, no se consultaron credenciales ni se publicaron ramas o PRs. Las integraciones se distinguen entre implementadas, configuradas y efectivamente verificadas.

## Índice de lectura

1. [La explicación corta, antes de entrar en detalle](#1-la-explicación-corta-antes-de-entrar-en-detalle)
2. [Conceptos que conviene distinguir](#2-conceptos-que-conviene-distinguir)
3. [Lenguajes, tecnologías y carpetas](#3-lenguajes-tecnologías-y-carpetas)
4. [Servicios, herramientas e integraciones reales](#4-servicios-herramientas-e-integraciones-reales)
5. [La configuración concreta de este repositorio](#5-la-configuración-concreta-de-este-repositorio)
6. [Instalación y actualización](#6-instalación-y-actualización)
7. [Qué sucede al ejecutar swarm](#7-qué-sucede-al-ejecutar-swarm)
8. [Cómo entra la constitución inicial en cada agente](#8-cómo-entra-la-constitución-inicial-en-cada-agente)
9. [Por qué empieza el specifier](#9-por-qué-empieza-el-specifier)
10. [Entrevista, especificación y plan de features](#10-entrevista-especificación-y-plan-de-features)
11. [Aprobación humana y controles de integridad](#11-aprobación-humana-y-controles-de-integridad)
12. [Handoff exclusivo del specifier al coder](#12-handoff-exclusivo-del-specifier-al-coder)
13. [El daemon: correo en archivos y coordinación](#13-el-daemon-correo-en-archivos-y-coordinación)
14. [Cómo recibe y termina una tarea un agente](#14-cómo-recibe-y-termina-una-tarea-un-agente)
15. [Scheduler, parking y conservación de sesiones](#15-scheduler-parking-y-conservación-de-sesiones)
16. [Responsabilidades del coder, reviewer y architect](#16-responsabilidades-del-coder-reviewer-y-architect)
17. [Publicación del PR, merge humano y siguiente feature](#17-publicación-del-pr-merge-humano-y-siguiente-feature)
18. [Git, worktrees y el problema de la base vieja](#18-git-worktrees-y-el-problema-de-la-base-vieja)
19. [Memoria compartida con Engram](#19-memoria-compartida-con-engram)
20. [TUI, observabilidad y notificaciones](#20-tui-observabilidad-y-notificaciones)
21. [Pruebas y herramientas Gherkin](#21-pruebas-y-herramientas-gherkin)
22. [Problemas encontrados y soluciones en el historial](#22-problemas-encontrados-y-soluciones-en-el-historial)
23. [Hallazgos, contradicciones y limitaciones vigentes](#23-hallazgos-contradicciones-y-limitaciones-vigentes)
24. [Recorrido completo con un ejemplo](#24-recorrido-completo-con-un-ejemplo)
25. [Manual de operación y diagnóstico](#25-manual-de-operación-y-diagnóstico)
26. [Mapa de archivos y funciones para seguir leyendo](#26-mapa-de-archivos-y-funciones-para-seguir-leyendo)
27. [Conclusiones y prioridades](#27-conclusiones-y-prioridades)

---

## 1. La explicación corta, antes de entrar en detalle

Este proyecto es un **orquestador local de agentes de programación**. No implementa un modelo de inteligencia artificial propio. Arranca programas externos de agentes, les entrega instrucciones de rol, les asigna carpetas de trabajo y hace que se transfieran tareas por un protocolo controlado.

El circuito normal es:

```text
Persona
  │ describe el módulo y responde la entrevista
  ▼
Specifier
  │ escribe decisiones, visión general y features
  ▼
Persona ejecuta: ./swarm plan approve
  │
  ▼
Specifier ──feature aprobada──► Coder
                                 │ implementación + commit
                                 ▼
                              Reviewer
                                 │
                     ┌───────────┴───────────┐
                     │ fail                  │ pass
                     ▼                       ▼
                   Coder                 Architect
                                             │
                         ┌───────────────────┴──────────────────┐
                         │ ajustes                              │ cierre técnico
                         ▼                                      ▼
                       Coder                                Specifier
                                                                │ informa al usuario
                                                                │
                      Código de PlanFlow publica el PR ◄────────┘
                                 │
                                 ▼
                      Persona revisa y mergea el PR
                                 │
                                 ▼
                      Daemon detecta el merge
                                 │
                                 ▼
                 Sincroniza base y worktrees seguros
                                 │
                                 ▼
                Despacha la siguiente feature al Coder
```

El camino de un `fail` cambia cuando se alcanza el tope de rondas: en ese caso se escala al architect en lugar de volver indefinidamente al coder.

Hay tres ideas centrales:

- **Los modelos proponen y ejecutan trabajo; Python controla el protocolo.** La aprobación del plan, el ruteo, las transiciones y la publicación no dependen únicamente de que el agente obedezca una frase.
- **La comunicación persistente es el sistema de archivos.** Los handoffs son archivos `.handoff`, con headers y cuerpo, guardados en bandejas por rol.
- **No se mantienen necesariamente cuatro procesos de IA vivos.** Se crean cuatro espacios de terminal, pero los agentes se levantan bajo demanda y se estacionan cuando no tienen trabajo.

Dos precisiones fundamentales:

1. “Un agente activo” es una estrategia de ahorro, **no un mutex global**. Puede haber solapamiento breve entre emisor y receptor, y el specifier puede seguir hablando con la persona mientras otro rol trabaja.
2. “Se conserva la sesión” no significa lo mismo para todos los backends. El launcher implementa reanudación para **Claude, Pi y Hermes**. Para **OpenCode, Codex, Copilot, Grok y Kiro** vuelve a iniciar una conversación nueva. El plan y los archivos de tareas sobreviven por separado.

**Archivos de referencia:** [swarm_cli.py](../swarmforge/scripts/swarm_python/entrypoints/swarm_cli.py), [scheduler.py](../swarmforge/scripts/swarm_python/scheduler.py), [sessions.py](../swarmforge/scripts/swarm_python/sessions.py), [plan_flow.py](../swarmforge/scripts/swarm_python/plan_flow.py).

## 2. Conceptos que conviene distinguir

### 2.1 Swarm, rol, agente, proveedor y modelo

**Swarm** es el conjunto coordinado: cuatro roles, sus terminales, sus carpetas, el daemon y el estado del plan.

**Rol** es la responsabilidad: `specifier`, `coder`, `reviewer` o `architect`.

**Agente o backend CLI** es el programa ejecutable que maneja una conversación y herramientas. Por ejemplo, `opencode`, `codex`, `claude` o `pi`.

**Proveedor** es el servicio que atiende el modelo, cuando el backend permite elegirlo. Por ejemplo, el identificador `opencode-go` dentro de OpenCode.

**Modelo** es el identificador concreto que el backend solicita. El swarm lo transmite como argumento; no garantiza que la suscripción permita usarlo ni que siga disponible.

Por eso, la frase “el coder usa OpenCode” no identifica por sí sola el modelo; y “el reviewer usa Codex” tampoco significa que SwarmForge esté usando un SDK de OpenAI directamente.

### 2.2 Tres cosas distintas llamadas sesión

**Sesión de Herdr:** el contenedor de terminales de un swarm. Se llama `swarmforge-<hash>`. No es la conversación del modelo.

**Workspace y pane de Herdr:** espacio y panel en los que se ejecuta el shell y, temporalmente, un agente. Un pane puede existir aunque el agente esté estacionado.

**Sesión del backend de IA:** la conversación que Claude, Pi o Hermes pueden recuperar. El swarm guarda su identidad o marcador en `agents.json`; el contenido de la conversación lo conserva el backend, no ese JSON.

También existe `sessions.tsv`, pero ese archivo inventaría nombres y metadatos de roles, no transcripciones del modelo.

### 2.3 Checkout principal y worktree

Un **checkout** es una carpeta de archivos de un repositorio Git.

Un **worktree** es otro checkout del mismo repositorio. Tiene sus propios archivos y rama, pero comparte la base de objetos de Git. Eso permite pasar un hash de commit entre roles sin copiar todo el código por un canal de mensajes.

En esta configuración:

- El specifier usa la carpeta principal.
- El coder usa `.worktrees/coder`.
- El reviewer usa `.worktrees/reviewer`.
- El architect usa `.worktrees/architect`.

El valor `master` en la columna worktree significa **checkout principal**. No obliga a que la rama activa se llame `master`.

### 2.4 Plan, feature y task

El **plan** define el módulo completo.

Una **feature** es una entrega vertical: comportamiento, pruebas y alcance que se pueden revisar y mergear como una unidad.

El header **`task:`** identifica la tarea en el protocolo. Para una feature del plan, debe conservar exactamente el id de esa feature durante todo el circuito. Si el coder cambia ese nombre al responder, puede perderse la vinculación automática con el plan y con la sesión de la feature.

### 2.5 Terminar tarea no es mergear feature

`done_with_current.sh` marca **un mensaje recibido** como completado.

El handoff del architect al specifier declara **el cierre técnico** y lleva la feature a `awaiting_merge`.

El merge del PR por la persona lleva la feature a `merged`.

Son tres hechos diferentes. Una tarea completada en la bandeja no prueba que un PR haya sido mergeado.

## 3. Lenguajes, tecnologías y carpetas

### 3.1 Qué lenguajes utilizamos realmente

El proyecto no es exclusivamente TypeScript, aunque la constitución de esta instalación dice `Project languages: Typescript`.

- **Python 3.10+**: backend de orquestación. Configuración, procesos, daemon, scheduler, validación, planes, sesiones y operaciones Git.
- **TypeScript**: TUI y componentes de aceptación específicos del proyecto.
- **Bash**: instaladores, inicializador, shims de arranque y wrappers de comandos Python.
- **Go**: herramientas Gherkin vendorizadas bajo `vendor/acceptance-pipeline-specification` y sus binarios precompilados.
- **Gherkin**: escenarios de comportamiento en `features/*.feature`.
- **JSON**: estado de agentes, plan y reportes estructurados.
- **TSV**: configuración derivada de roles y catálogo de proveedores/modelos.
- **YAML**: workflows de GitHub Actions.
- **Markdown y `.prompt`**: documentación, constitución y reglas de cada rol.
- **CMD/PowerShell**: bordes de compatibilidad y ejecución en Windows.

Herdr es una dependencia externa escrita en Rust. OpenTUI aporta un núcleo nativo de Zig. Eso no convierte a Rust o Zig en los lenguajes principales del código de orquestación de este repositorio.

### 3.2 Árbol mental del proyecto

```text
swarm-local/
├── install_swarm.sh              Instalador de alto nivel; puede usar la red
├── install_swarm.cmd             Entrada de instalación para Windows
├── swarm-init                    Wizard y copia de plantilla
├── swarm                         Entrada del runtime local
├── provider-models.tsv           Catálogo estático de respaldo
├── VERSION                       Versión de distribución
├── package.json                  Workspaces npm: tui y acceptance
├── template/                     Fuente distribuible para otros proyectos
│   ├── swarm
│   ├── swarm.cmd
│   ├── swarmforge/
│   └── toolchain/<plataforma>/bin/
├── swarmforge/                   Instalación/configuración de este proyecto
│   ├── swarmforge.conf
│   ├── constitution.prompt
│   ├── constitution/articles/project.prompt
│   ├── roles/*.prompt
│   └── scripts/
│       ├── *.sh
│       ├── shared-articles/*.prompt
│       └── swarm_python/
├── tui/                          Dashboard de terminal
│   ├── main.ts
│   ├── build.ts
│   ├── src/
│   └── test/
├── acceptance/                   Pipeline de aceptación TypeScript
├── features/                     Especificaciones Gherkin y briefs
├── tests/swarm_python/           Pruebas del backend
├── tests/swarm_update/           Pruebas del instalador/update
├── scripts/build_toolchain.sh    Build de herramientas Go por plataforma
├── vendor/acceptance-pipeline-specification/
├── .github/workflows/            CI y release
└── docs/                         Documentación actual y archivo histórico
```

### 3.3 Por qué hay `template/` y `swarmforge/`

`template/` es la fuente que el inicializador copia a un proyecto destino.

`swarmforge/` es la instalación de SwarmForge usada por este propio repositorio. Puede contener elecciones locales de modelos y lenguaje.

Modificar solo la instalación local no actualiza automáticamente la plantilla que recibirán otros proyectos. Modificar solo la plantilla tampoco cambia por arte de magia una instalación ya copiada.

En la comparación de esta revisión:

- Los scripts Python y wrappers de ambas copias coincidían, incluyendo los cambios locales de flujo y scheduler.
- Los prompts de rol coincidían.
- `shared-articles/engineering.prompt` difería por la sustitución local del lenguaje configurado.

Las pruebas Python añaden al `sys.path` la copia de **`template/swarmforge/scripts`**. Por eso importa verificar que la instalación local y la plantilla sigan sincronizadas.

### 3.4 Estado generado durante la ejecución

```text
.swarmforge/
├── agents.json                  Estado por rol y referencia de conversación
├── roles.tsv                    Rol, backend, worktree, pane y workspace
├── sessions.tsv                 Inventario de nombres/metadatos de roles
├── herdr-session                Nombre de la sesión aislada de Herdr
├── prompts/
│   ├── specifier.md              Constitución/artículos/rol ensamblados
│   ├── specifier.sh              Launcher generado
│   └── <otros roles>.md/.sh
├── daemon/
│   ├── handoffd.pid
│   ├── handoffd.log
│   └── stop                      Señal de parada
├── plan/
│   ├── plan.json
│   ├── decisions.md
│   ├── general.md
│   └── features/<id>.md
├── handoffs/
│   ├── outbox/tmp/
│   ├── outbox/
│   ├── sent/
│   ├── failed/
│   ├── inbox/new/
│   ├── inbox/in_process/
│   └── inbox/completed/
├── logs/
│   ├── tui.log
│   └── plan-flow.log
├── toolchain/bin/
├── toolchain/PLATFORM
├── toolchain-check.txt
├── tui/swarm-tui[.exe]
└── backup-<fecha>/
```

Cada worktree tiene su propia bandeja `.swarmforge/handoffs`. El plan, en cambio, se resuelve al checkout principal: no hay cuatro copias independientes del plan.

Durante esta revisión no había un estado de ejecución de agentes que pudiera usarse para reconstruir una corrida real. Las pruebas de TUI generaron `.swarmforge/logs/tui.log`; ese archivo **no demuestra que se haya iniciado el swarm**.

## 4. Servicios, herramientas e integraciones reales

### 4.1 Herdr: terminales y control de procesos

Es una dependencia **obligatoria** del runtime.

El backend llama al ejecutable `herdr` mediante subprocesses. No hay aquí un servicio HTTP propio para controlar terminales.

Operaciones principales:

```text
herdr --session <nombre> server
herdr --session <nombre> workspace create ...
herdr --session <nombre> workspace list
herdr --session <nombre> pane run <pane> <texto>
herdr --session <nombre> pane list
herdr --session <nombre> pane process-info --pane <pane>
herdr --session <nombre> pane send-keys <pane> ...
```

[herdr_ops.py](../swarmforge/scripts/swarm_python/herdr_ops.py) encapsula estas llamadas. `session_name()` calcula un CRC32 de la ruta del proyecto para construir el nombre de sesión.

Herdr informa dos cosas diferentes: estado estimado del agente (`working`, `idle`, `blocked`, `done`, `unknown`) y proceso en primer plano. El scheduler usa ambas porque “parece ocioso” no prueba que el agente haya salido.

### 4.2 Engram: memoria compartida

Es otra dependencia **obligatoria** para arrancar.

La integración elegida es la **CLI**, no una instalación automática de servidor MCP. Cada rol recibe `ENGRAM_PROJECT`, pero los prompts exigen pasar además `--project` en todas las llamadas.

No es el canal de handoff ni el almacén de conversaciones completas. Es memoria curada: decisiones, descubrimientos, arquitectura y soluciones duraderas.

Referencias: [memory.prompt](../swarmforge/scripts/shared-articles/memory.prompt) y [launch.py](../swarmforge/scripts/swarm_python/launch.py).

### 4.3 Git y GitHub

**Git** es obligatorio para ramas, commits y worktrees.

**GitHub** interviene en dos superficies distintas:

1. Instalación de la TUI desde GitHub Releases.
2. Publicación y seguimiento de PRs de features mediante `gh`.

El runtime puede arrancar sin que `gh` publique correctamente, pero la entrega automática quedará limitada. `PlanFlow` registra el error y conserva el trabajo para recuperación.

La URL `origin` de esta copia apunta a `goncalodjay/swarm-local`. No se comprobó acceso de escritura ni autenticación de GitHub en esta revisión.

### 4.4 Backends de IA

Los ocho identificadores admitidos por `config.py` son:

```text
claude codex copilot grok hermes kiro-cli opencode pi
```

El swarm ejecuta sus CLIs; cada CLI administra su autenticación y sus conexiones a proveedores. El orquestador no implementa aquí clientes SDK directos para sus APIs de inferencia.

Tener soporte para un backend no significa que esté instalado, autenticado o contratado. Tampoco significa que los ocho estén ejecutándose.

### 4.5 Langfuse y OpenTelemetry

La TUI incorpora una integración **opcional**:

- `@langfuse/otel`
- `@langfuse/tracing`
- `@opentelemetry/sdk-node`

[instrumentation.ts](../tui/src/instrumentation.ts) inicia el SDK solamente si están presentes:

```text
LANGFUSE_PUBLIC_KEY
LANGFUSE_SECRET_KEY
LANGFUSE_BASE_URL
```

`main.ts` crea una observación `tui.session`.

En el entorno de esta revisión las tres variables estaban **no definidas**. Por lo tanto: el soporte existe en el código, pero no hay evidencia de una conexión activa a Langfuse en esta sesión.

Tampoco hay evidencia de trazado completo de todos los prompts, tokens, respuestas y costos de todos los agentes. La instrumentación observada es de la TUI.

### 4.6 Notificaciones del sistema operativo

La TUI puede avisar cuando un agente necesita intervención, termina o queda bloqueado. Usa adaptadores de plataforma: notificaciones del sistema y sonidos.

Variables relevantes:

```text
SWARMTUI_NOTIFY=0    Deshabilita notificaciones
SWARMTUI_SOUND=0     Deshabilita sonidos
NO_COLOR            Selecciona tema monocromo
LOG_LEVEL           Nivel del logger
```

Referencias: [notifications.ts](../tui/src/notifications.ts), [sound.ts](../tui/src/sound.ts), [theme.ts](../tui/src/theme.ts).

### 4.7 Qué no aparece como integración del orquestador

No encontré un broker Redis/RabbitMQ, Kubernetes, una base PostgreSQL del swarm, un servidor REST propio o una integración propia con Jira/Slack/AWS/Datadog como parte de este circuito.

Un agente podría tener esas herramientas configuradas por fuera, pero eso no se deduce de este repositorio. La frase del prompt del reviewer que pide probar APIs y bases reales es una responsabilidad general para el proyecto que implemente; no prueba que SwarmForge esté conectado a una API o base específica.

El daemon es un proceso local Python. No es un daemon del sistema instalado como servicio de Windows o unidad systemd en el código revisado.

## 5. La configuración concreta de este repositorio

El archivo leído fue [swarmforge/swarmforge.conf](../swarmforge/swarmforge.conf):

```text
window specifier opencode master --model opencode-go/kimi-k2.7-code
window coder opencode coder --model opencode-go/deepseek-v4-flash
window reviewer codex reviewer --model gpt-5.6-luna -c model_reasoning_effort=high
window architect opencode architect batch --model opencode-go/minimax-m3
```

Interpretación, campo por campo:

```text
window <rol> <backend> <worktree> [task|batch] [argumentos del CLI...]
```

- `window` es la directiva que reconoce el parser.
- El rol determina el archivo `roles/<rol>.prompt`.
- El backend debe pertenecer a los ocho admitidos.
- `master` y `none` significan carpeta principal.
- Si no aparece `task` o `batch`, el modo predeterminado es `task`.
- Los argumentos restantes se agregan al comando del backend.

Por lo tanto:

- **Specifier:** OpenCode, modelo configurado `opencode-go/kimi-k2.7-code`, checkout principal, una tarea por vez.
- **Coder:** OpenCode, `opencode-go/deepseek-v4-flash`, rama y carpeta del coder, una tarea por vez.
- **Reviewer:** Codex, `gpt-5.6-luna`, razonamiento configurado en `high`, una tarea por vez.
- **Architect:** OpenCode, `opencode-go/minimax-m3`, modo batch.

Los nombres se documentan como **valores de configuración**, no como modelos cuya disponibilidad se haya validado mediante una petición real.

Esta configuración no contiene los flags de autoaprobación de permisos que puede agregar el wizard. No se debe asumir que las herramientas se aprobarán automáticamente en estos agentes: también interviene la configuración propia de cada CLI.

### 5.1 Restricciones del parser

`parse_config()` comprueba duplicados, backends admitidos, prompts faltantes y nombres de worktree problemáticos.

La separación usa `line.split()`. Este archivo no es un parser shell completo que preserve cualquier combinación arbitraria de comillas y espacios en argumentos. Conviene mantener ids de modelos sin espacios y flags simples.

Otro detalle: el scheduler considera especial al rol de **índice cero**, no busca siempre el nombre literal `specifier`. Conservar el specifier como primera fila es una condición práctica del diseño.

## 6. Instalación y actualización

### 6.1 No confundir instalar la distribución con iniciar el swarm

Estos comandos hacen cosas diferentes:

```sh
./install_swarm.sh /ruta/al/proyecto   # obtiene TUI y delega al inicializador
./swarm-init /ruta/al/proyecto        # copia plantilla local y pregunta configuración
cd /ruta/al/proyecto
./swarm                              # arranca runtime y TUI
```

El destino debe existir para que `swarm-init` pueda resolverlo con `cd`.

En Windows, la distribución incluye wrappers `.cmd`; Bash sigue siendo necesario. “Sin WSL” no significa “sin Bash”.

### 6.2 Instalador de alto nivel

[install_swarm.sh](../install_swarm.sh):

1. Comprueba herramientas básicas.
2. Detecta plataforma.
3. Busca el binario de TUI para esa plataforma en Releases.
4. Intenta comprobar SHA-256.
5. Puede recurrir al manifest.
6. Si no consigue binario, reutiliza uno existente o compila con Node/npm/Bun.
7. Si falta Bun, puede instalarlo.
8. Delega a `swarm-init`, o a `swarm-init --update` si el destino ya tiene configuración.

El default efectivo de `RELEASE_REPO` en el script es **`goncalodjay/swarm-local`**, no `nousresearch/swarm-local` como todavía dicen partes de la documentación anterior.

Variables de selección efectivas:

```sh
SWARM_RELEASE_REPO=goncalodjay/swarm-local
SWARM_RELEASE_TAG=latest
```

No es correcto describir este instalador como completamente offline: puede descargar la TUI, Bun y dependencias npm.

### 6.3 Inicializador local

[swarm-init](../swarm-init) pregunta:

1. Lenguajes del proyecto.
2. Si se autoaprueban herramientas.
3. Backend de cada rol.
4. Proveedor y modelo.
5. Thinking/effort cuando aplica.

Después:

- Instala la TUI compilada.
- Copia `template/swarmforge`.
- Copia los launchers.
- Genera `swarmforge.conf`.
- Sustituye `{{LANGUAGES}}` en los prompts.
- Instala el toolchain de la plataforma.
- Agrega `.swarmforge/` y `.worktrees/` a `.gitignore`.

Ejemplo:

```sh
./swarm-init --languages "TypeScript, Python" /ruta/al/proyecto
```

Indicar lenguajes no evita el wizard de los roles.

### 6.4 Descubrimiento de modelos

No se depende únicamente de `provider-models.tsv`.

[agent_probe.py](../swarmforge/scripts/swarm_python/agent_probe.py) puede consultar, según backend:

- Claude: estado de autenticación; catálogo estático al no tener un listado vivo equivalente.
- Codex: `codex login status`, `codex debug models`.
- OpenCode: `opencode auth list`, `opencode models`.
- Pi: metadatos locales de autenticación y `pi --list-models`.
- Kiro: `kiro-cli whoami --format json`, `kiro-cli chat --list-models --format json`.
- Copilot/Grok: fallback estático en este diseño.

Hermes tiene un probe propio en [hermes_probe.py](../swarmforge/scripts/swarm_python/hermes_probe.py). Distingue credenciales, catálogos y verificación real opcional de proveedores.

El estado histórico de una credencial no siempre representa su salud actual. Por eso el probe de Hermes puede hacer una llamada real, si se elige verificar. Un error por modelo no necesariamente equivale a un error de autenticación.

### 6.5 Actualizar sin perder la instalación

```sh
./install_swarm.sh /ruta/al/proyecto
# o, si ya tenés la distribución local:
./swarm-init --update /ruta/al/proyecto
```

La actualización:

- Conserva elecciones de backend/modelo de roles vigentes.
- Reconcilia roles nuevos y obsoletos.
- Reemplaza scripts, prompts de plantilla, constitución, launchers y toolchain.
- Conserva bandejas, logs, sesiones y trabajo de los roles vigentes.
- Detecta cambios sin commit y swarm en ejecución.
- Pide confirmación ante riesgo.
- Hace backup bajo `.swarmforge/backup-<fecha>/`.
- No elimina automáticamente trabajo sin commit de un worktree.

Sin terminal, ante riesgo, necesita `--yes` para continuar. Ese flag no inventa respuestas para configurar un rol nuevo que requiera wizard.

**Advertencia operativa:** los prompts estándar locales pueden ser reemplazados. Si personalizás la constitución, versioná esa decisión y revisá el diff del update. La preservación de `swarmforge.conf` no significa preservación de todos los archivos de instrucciones.

## 7. Qué sucede al ejecutar swarm

### 7.1 Cadena de entrada

```text
./swarm
  └── swarmforge/scripts/swarmforge.sh
        └── lib/python.sh encuentra Python ejecutable
              └── swarm_python/entrypoints/swarm_cli.py
                    └── main()
                          └── run_main()
```

Los wrappers son pequeños. La lógica importante vive en Python.

### 7.2 Secuencia de `run_main()`

1. Exige `herdr`, `git` y `engram`.
2. Construye un `Context` con rutas del proyecto.
3. Arranca o comprueba el servidor de la sesión Herdr.
4. Inicializa Git si el destino todavía no tiene repositorio.
5. Asegura exclusiones para el estado de runtime.
6. Lee `swarmforge.conf`.
7. Verifica ejecutables de los backends configurados.
8. Detiene el daemon anterior y cierra workspaces previos del swarm.
9. Crea un workspace y pane por rol.
10. Escribe inventarios `roles.tsv`, `sessions.tsv` y `herdr-session`.
11. Prueba el toolchain Gherkin y deja un reporte.
12. Crea worktrees faltantes.
13. Prepara las bandejas de cada rol.
14. Copia scripts y toolchain a worktrees.
15. Recupera `task` y `session_id` del estado anterior.
16. Inicializa primer rol como `wanted`; demás como `parked`.
17. Arranca `handoffd.py` usando el mismo `sys.executable`.
18. Abre la TUI, salvo `--no-tui`.

No lanza los cuatro CLIs directamente. El daemon y su scheduler ejecutan el primer rol en el siguiente ciclo.

### 7.3 Preparación de Git

`init_repo_if_missing()` puede hacer `git init`, crear `master`, agregar archivos y realizar un commit inicial. Por eso iniciar el swarm no es una operación totalmente de solo lectura.

`prepare_worktrees()` crea ramas del estilo:

```text
swarmforge-coder
swarmforge-reviewer
swarmforge-architect
```

Si el worktree ya tiene `.git`, no se recrea automáticamente. El arranque no equivale a borrar y reconstruir las ramas de trabajo.

### 7.4 Rutas derivadas

[paths.py](../swarmforge/scripts/swarm_python/paths.py) centraliza el `Context`: raíz, scripts, worktrees, configuración, prompts, daemon y nombre de sesión.

No son rutas dispersas y adivinadas por cada comando; la mayoría se deriva de la raíz y de esa estructura estable.

## 8. Cómo entra la constitución inicial en cada agente

### 8.1 No tenés que pegarla manualmente en cada terminal

La constitución se ensambla y pasa al CLI desde código.

La función relevante es **`_write_agent_instruction()`**, en [launch.py](../swarmforge/scripts/swarm_python/launch.py).

El orden de ensamblado es:

1. `swarmforge/constitution.prompt`.
2. `swarmforge/constitution/articles/*.prompt`, ordenados por nombre.
3. `swarmforge/scripts/shared-articles/*.prompt`, ordenados por nombre.
4. `swarmforge/roles/<rol>.prompt`.
5. Una aclaración de que las instrucciones ya fueron cargadas.

Secciones separadas por `---` terminan en:

```text
.swarmforge/prompts/<rol>.md
```

La constitución base indica que tiene precedencia sobre los artículos. Esa precedencia es una **regla textual para el modelo**; no hay un motor que resuelva formalmente todas las contradicciones entre instrucciones.

### 8.2 Qué aportan los artículos

- **`project.prompt`**: forma del proyecto, lenguaje configurado, ciclo, reglas locales.
- **`engineering.prompt`**: pruebas, calidad, herramientas disponibles y límites.
- **`handoffs.prompt`**: formato, envío, recepción y cierre de mensajes.
- **`memory.prompt`**: protocolo de Engram.
- **`workflow.prompt`**: worktrees, commits, temporales y prohibición de iniciar otros launchers.
- **Prompt del rol**: responsabilidades específicas.

El pedido funcional de la persona se escribe después en la conversación con el specifier. No es lo mismo que la constitución: la constitución es estable; el pedido describe qué construir.

### 8.3 Cómo se inyecta según backend

La función **`build_launch_command()`** adapta el comando:

- Claude recibe `--append-system-prompt-file` y el primer mensaje; usa `--session-id` o `--resume` según corresponda.
- Pi recibe `--session-id`, `--no-context-files`, nombre, modelo/thinking y primer mensaje.
- OpenCode recibe `--prompt` con el contenido del archivo.
- Codex recibe `exec -C <worktree>` y el prompt.
- Hermes recibe `chat --in <worktree> --query-file <prompt>` en frío; su resume usa otro camino.
- Kiro usa `chat` con el prompt como argumento.
- Los demás tienen sus respectivos flags de instrucciones.

No todos reciben las reglas con la misma jerarquía interna de mensajes de su producto. Por ejemplo, Claude tiene un flag explícito de system prompt adicional; OpenCode recibe `--prompt`. El swarm homogeneiza contenido, no demuestra equivalencia absoluta de las semánticas de cada CLI.

### 8.4 Variables de entorno del agente

Antes de ejecutar el CLI se exporta:

```text
SWARMFORGE_ROLE=<rol>
ENGRAM_PROJECT=<nombre de la carpeta principal>
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
```

El `PATH` incluye:

1. Scripts de SwarmForge del worktree.
2. Toolchain del worktree.
3. PATH existente.
4. Raíz del proyecto al final, para encontrar `swarm` sin tapar comandos del sistema.

Luego hace `cd` al worktree asignado.

### 8.5 Por qué se genera un `.sh`

El launcher produce `.swarmforge/prompts/<rol>.sh` y manda a Herdr un comando que ejecuta **Bash explícitamente**.

En Windows el pane puede tener PowerShell. Mandar directamente `export ... && ...` a PowerShell no sirve. Por eso la invocación usa el operador `&` de PowerShell delante de la ruta a Bash cuando corresponde.

La lectura del prompt usa el builtin de Bash:

```sh
"$(<ruta/al/prompt.md)"
```

Esto evita depender de `cat` en el PATH del pane. Además, en un arranque fresco se comprueba que el archivo exista y no esté vacío.

### 8.6 Un límite importante del ensamblado

`_write_agent_instruction()` lee los prompts desde el worktree del rol. `sync_worktree_scripts()` copia scripts y toolchain, **no el árbol completo de constitución y roles**.

En este repositorio esos archivos están versionados y los nuevos worktrees nacen con ellos. En un destino con una instalación de `swarmforge/` todavía sin commit, o tras editar prompts solo en el principal, hay que comprobar que los worktrees efectivamente tengan la versión deseada. El ensamblador omite archivos que no existen en lugar de fallar por cada uno.

Por eso, “cambié el prompt en el checkout principal” no prueba que todos los agentes ya lo hayan recibido. Una conversación reanudada tampoco se reinicializa de la misma manera que una fresca.

## 9. Por qué empieza el specifier

[agent_state.py](../swarmforge/scripts/swarm_python/agent_state.py) inicializa:

```text
primer rol  → wanted
otros roles → parked
```

El scheduler convierte `wanted` en un lanzamiento mediante `start_role()`.

La decisión es posicional: por eso el specifier debe mantenerse primero en la configuración.

La TUI muestra una pantalla de arranque y bloquea la navegación hacia agentes mientras espera que el primero esté listo.

En [app.ts](../tui/src/app.ts), `updateStarting()` considera listos estados Herdr como `idle`, `done` o `blocked`: estar esperando una respuesta humana cuenta como listo.

Si Herdr no puede determinar el estado, hay una salida por timeout de **120 segundos**. Si el primer rol ya está `parked` o `failed`, el dashboard se abre igualmente. La pantalla no es una prueba infalible de salud del modelo; evita principalmente entrar en un pane a medio lanzar.

## 10. Entrevista, especificación y plan de features

### 10.1 El specifier no debería comenzar programando

Su trabajo es descubrir y fijar:

- Objetivo de negocio.
- Quién consume el resultado.
- Datos, propiedad y persistencia.
- Integraciones y contratos.
- Comportamiento ante fallos.
- Restricciones técnicas y no técnicas.
- Definición de terminado.

El prompt exige una pregunta por vez, recomendación concreta y espera de respuesta. También pide investigar el código antes de preguntar algo que el repositorio ya resuelve.

La entrevista cubre el **módulo completo antes de dividirlo**.

### 10.2 Registro de decisiones

El archivo es:

```text
.swarmforge/plan/decisions.md
```

Formato reconocido por código:

```markdown
## D1: ¿Cómo se identifica un cliente?
Decision: Se usará un UUID generado por el servidor.
Why: Evita depender del correo como clave estable.
```

`scan_decisions()` interpreta las secciones; ignora encabezados dentro de fences de código y rechaza respuestas vacías o de relleno.

El mínimo predeterminado es **5 decisiones válidas y distintas**. `min_decisions` puede modificarlo, incluso establecer cero.

No es una certificación de que realmente ocurrió una entrevista humana: el código verifica la estructura del registro. No puede comprobar por sí solo que cada respuesta haya sido dicha por la persona.

### 10.3 Los cuatro componentes del plan

**`plan.json`**: identidad, features, dependencias, criterios y estados.

**`decisions.md`**: evidencia escrita de decisiones.

**`general.md`**: contexto común a todas las features.

**`features/<id>.md`**: especificación detallada de una feature.

El plan se localiza desde cualquier worktree con `locate_plan_dir()`, usando el directorio común de Git y un fallback a `git worktree list --porcelain`.

### 10.4 Ejemplo mínimo de estructura

Este JSON es ilustrativo. Requiere los Markdown correspondientes para validarse:

```json
{
  "id": "clientes",
  "title": "Gestión de clientes",
  "status": "draft",
  "general_file": "general.md",
  "max_review_rounds": 3,
  "features": [
    {
      "id": "crear-cliente",
      "title": "Crear cliente",
      "summary": "Crear un cliente con validación de identidad.",
      "depends_on": [],
      "allowed_paths": ["src/clientes/**", "tests/clientes/**"],
      "acceptance": [
        "Una solicitud válida crea un cliente con UUID.",
        "Un correo repetido devuelve el error especificado sin duplicar datos."
      ],
      "est_lines": 350,
      "status": "pending"
    },
    {
      "id": "consultar-cliente",
      "title": "Consultar cliente",
      "summary": "Buscar un cliente por UUID.",
      "depends_on": ["crear-cliente"],
      "allowed_paths": ["src/clientes/**", "tests/clientes/**"],
      "acceptance": ["Un UUID inexistente devuelve el resultado acordado."],
      "est_lines": 250,
      "status": "pending"
    }
  ]
}
```

Cada feature Markdown debe tener contenido real bajo:

```markdown
## Behavior
## Edge cases
## Acceptance examples
## Out of scope
## Files
```

No basta con escribir esos cinco encabezados vacíos.

### 10.5 Qué significa el objetivo de 400 líneas

Es una guía para dimensionar **código de producción por feature**. No es un límite duro ni permiso para escribir una especificación incompleta.

La implementación avisa sobre tamaño y alcance, pero no rechaza automáticamente una entrega por superar 400 líneas.

`compute_metrics()` suma líneas agregadas y eliminadas del diff. No mide solamente crecimiento neto. Excluye ciertos tests, lockfiles y Markdown de `docs/`; sigue siendo una heurística.

`allowed_paths` es una guía de alcance y reporte, **no un sandbox de filesystem**.

### 10.6 Qué ve el coder

`ready_for_next.py` imprime la tarea y llama indirectamente a `coder_context()`.

`slice_for()` compone:

- Visión general.
- Nombre y posición de la feature.
- Resumen.
- Markdown de esa feature.
- Criterios de aceptación.
- Rutas permitidas.
- Recordatorio del tamaño orientativo.

No concatena las especificaciones de las otras features ni todo `decisions.md` por separado. Las decisiones comunes necesarias deben estar resumidas en `general.md`.

Esto reduce contexto, pero **no es una barrera de acceso**: los agentes corren como el mismo usuario y pueden leer archivos. “El coder solo ve su slice” describe la salida del helper, no una ACL.

## 11. Aprobación humana y controles de integridad

### 11.1 Comandos de aceptación de la especificación

Desde tu terminal, fuera del agente:

```sh
./swarm plan validate
./swarm plan show
./swarm plan status
./swarm plan approve
```

`validate` detecta campos faltantes, ids duplicados, dependencias inexistentes o cíclicas, criterios vacíos, Markdown faltante, decisiones insuficientes y secciones incompletas.

`approve` valida y registra la aprobación. No es un botón verbal: decirle al modelo “sí, aprobado” no produce por sí solo el registro de aprobación.

### 11.2 Cómo distingue persona y agente

`cmd_approve()` rechaza la ejecución si encuentra `SWARMFORGE_ROLE` definida.

```text
terminal de agente: SWARMFORGE_ROLE=specifier → rechazo
terminal propia: sin esa variable → puede aprobar
```

Los comandos humanos `publish`, `sync`, `rebase` y `set-status` tienen controles equivalentes.

Esto evita aprobaciones accidentales por el agente, pero no es una frontera de seguridad: un proceso con los mismos permisos puede quitar una variable o editar archivos. El propio código lo reconoce.

### 11.3 Qué se registra

`approve()` establece el plan como `approved`, registra fecha y fija la rama base si no estaba indicada.

Se calcula un SHA-256 de:

- Contenido funcional del JSON.
- Visión general.
- Markdown de features.
- `decisions.md`, cuando existe.

Se excluyen estados y campos de ejecución para que una transición normal no invalide el permiso.

El registro queda fuera del repo:

```text
~/.swarmforge/approvals/<hash-de-la-ruta-del-proyecto>.json
```

Además fija:

- Rama base aprobada.
- URL de `origin` aprobada.

Si cambia el contenido aprobado, el siguiente inicio de feature se rechaza. Si cambian los destinos de entrega, `PlanFlow` se niega a publicar hasta regularizar la aprobación.

### 11.4 Consecuencias operativas

- Copiar el proyecto a otra ruta no implica que conserve automáticamente su aprobación: el registro se identifica por ruta.
- Editar una especificación aprobada requiere nueva revisión y aprobación.
- Cambiar estados no cambia el hash de alcance.
- `base_branch`, `auto_publish` y `auto_next` se tratan como ajustes de entrega, aunque rama y remoto tienen pinning adicional.
- Los planes aprobados antes de la entrevista obligatoria conservan un camino de compatibilidad: el gate usa `interview=False`, pero sigue comprobando la integridad de la aprobación.

**Cuidado:** `approve()` también reinicia contadores y campos de ejecución de las features. No conviene usarlo como un “refresh” inocuo mientras hay una feature en plena publicación. Si el alcance cambia a mitad de trabajo, hay que coordinar la recuperación del estado, no ejecutar comandos a ciegas.

## 12. Handoff exclusivo del specifier al coder

### 12.1 No es simplemente una sugerencia del prompt

La tabla implementada en [handoff/validate.py](../swarmforge/scripts/swarm_python/handoff/validate.py) es:

```text
specifier → coder
coder     → reviewer
reviewer  → coder o architect
architect → coder o specifier
```

Para roles estándar:

- El specifier no puede enviar su trabajo al reviewer o architect por el helper.
- El coder no puede saltarse el reviewer.
- El reviewer con `pass` debe enviar al architect.
- El architect elige ajustes al coder o cierre al specifier, no ambos en un `git_handoff`.

La exclusividad real tiene un límite: la validación deja espacio a roles personalizados fuera del ciclo estándar. No es una política de permisos del sistema operativo. Es la regla aplicada al uso normal de `swarm_handoff.sh`.

### 12.2 Borrador de ejemplo

El specifier crea un archivo temporal en su worktree, por ejemplo `tmp/crear-cliente.handoff-draft`:

```text
type: git_handoff
to: coder
priority: 50
task: crear-cliente
commit: 0123456789

Implementar la feature crear-cliente del plan aprobado.
```

El hash `0123456789` es un marcador: **no debe copiarse como un commit real**.

El valor correcto se obtiene después de sincronizar la base:

```sh
git fetch origin --prune
git pull --ff-only
git rev-parse --short=10 HEAD
swarm_handoff.sh tmp/crear-cliente.handoff-draft
```

El script se ejecuta en la terminal del agente, donde `SWARMFORGE_ROLE` ya identifica al emisor.

### 12.3 Qué valida el helper

La cadena es:

```text
swarm_handoff.sh
  └── entrypoints/swarm_handoff.py::main()
        ├── parse_draft()
        ├── validate()
        ├── base_freshness()
        ├── _base_commit()
        ├── _coder_base_problem()
        ├── reserve_feature()
        └── write_handoff()
```

Comprueba, entre otras cosas:

- Tipo de mensaje admitido.
- Headers conocidos y no duplicados.
- Destinatarios existentes.
- Prioridad de dos dígitos entre `00` y `99`.
- `task` no vacío y con máximo de 80 caracteres.
- Commit de 10 caracteres hexadecimales que resuelva a un único objeto Git de tipo commit.
- Ruta permitida según rol.
- Plan aprobado e íntegro.
- Feature existente y `pending`.
- Dependencias `merged`.
- Ausencia de otra feature en curso.
- Frescura de la base, cuando se puede comprobar.
- Compatibilidad del worktree del coder con la base.

### 12.4 Reserva antes de encolar

La feature pasa a `coding` bajo un lock **antes** de escribir la outbox.

Así se cierra la ventana en la que dos envíos podrían aceptar la misma feature todavía `pending`.

Si falla normalmente la escritura de la outbox, se restaura un snapshot de la feature.

Si el proceso muere entre reserva y escritura, puede quedar una feature `coding` sin mensaje. Es un fallo visible y recuperable; no hay una transacción única que abarque JSON y todas las bandejas.

También hay ramas que, ante un error al actualizar estado, encolan el handoff con una advertencia muy visible para que la persona repare el estado. El sistema prioriza no perder la circulación del trabajo, pero eso permite divergencia entre correo y plan.

### 12.5 Archivo final y payload

El helper agrega campos reservados como `id`, `from` y timestamps. Los agentes no deben escribirlos manualmente en el borrador.

El cuerpo generado incluye:

```text
Re-read your role and constitution.

merge_and_process specifier <commit>
```

Luego agrega el texto libre del borrador, si existe.

El nombre contiene prioridad, timestamp, secuencia, emisor y destinatario:

```text
50_<fecha>_<secuencia>_from_specifier_to_coder.handoff
```

Se escribe primero en `outbox/tmp` y después se renombra a `outbox`. Al tener éxito, elimina el borrador original y muestra `HANDOFF QUEUED`.

**`HANDOFF QUEUED` no significa “el coder ya recibió, entendió y ejecutó”.** Significa que existe un mensaje listo para el daemon.

## 13. El daemon: correo en archivos y coordinación

### 13.1 Dónde vive y cómo se arranca

[entrypoints/handoffd.py](../swarmforge/scripts/swarm_python/entrypoints/handoffd.py) es el proceso persistente.

`start_handoff_daemon()` lo crea con `subprocess.Popen`, el intérprete Python actual y entorno UTF-8. Registra salida en:

```text
.swarmforge/daemon/handoffd.log
```

Escribe su PID en `handoffd.pid`.

El bucle nominal consulta cada **1 segundo**. Ese intervalo no garantiza latencia exacta de un segundo: las llamadas a procesos y verificaciones pueden alargar un ciclo.

### 13.2 Qué hace en cada poll

`poll_once()`:

1. Lee `roles.tsv` y la sesión Herdr.
2. Carga `agents.json`.
3. Recorre las outbox de todos los roles.
4. Entrega los archivos `.handoff`.
5. Solicita al flujo del plan que revise merges, sujeto a su intervalo.
6. Ejecuta un tick del scheduler.
7. Guarda el estado de agentes.

No consulta al modelo para decidir el destinatario. El destinatario ya está en el header validado.

### 13.3 Entrega de un mensaje

`deliver()`:

1. Parsea headers y cuerpo.
2. Obtiene los destinatarios.
3. Escribe una copia en `inbox/new` de cada receptor.
4. Agrega `recipient` y `enqueued_at`.
5. Si el receptor figura como running, manda un wake-up por Herdr.
6. Marca al emisor como que ya envió trabajo y asocia su `task`.
7. Mueve el original a `sent` del emisor.
8. Llama al hook de `PlanFlow`, si corresponde.

Si el receptor está parked, no se le teclea al shell una instrucción para un agente inexistente. El scheduler ve correo pendiente y lo lanza.

### 13.4 Qué se escribe en la terminal

El wake-up normal es un texto corto:

```text
You have new handoff mail. If idle, run ready_for_next.sh.
```

La especificación completa **no viaja tecleada a través de Herdr**. El agente ejecuta el helper y lee su tarea desde archivos.

Esto separa dos canales:

- **Persistencia y contenido:** filesystem.
- **Aviso y control de proceso:** Herdr.

### 13.5 Manejo de errores

El daemon tiene protección por mensaje, por etapa y por ciclo. Un error puntual se registra y el bucle intenta continuar.

`ErrorThrottle` escribe el primer traceback y luego resume errores consecutivos equivalentes cada 60 repeticiones. No evita que el fallo ocurra; evita inundar el log con el mismo traceback cada segundo.

Un mensaje que no puede entregarse puede ir a `failed` y dejar un archivo `.error`. No se debe asumir que todo fallo de entrega se reintenta automáticamente para siempre.

### 13.6 No es una cola transaccional de entrega exactamente una vez

Hay protección parcial contra duplicados por nombre de archivo y escrituras mediante temporales. Pero outbox, inbox, notificación y plan no están en una transacción atómica común.

Ejemplo: puede escribirse la inbox y fallar después la notificación. Hay que inspeccionar tanto el receptor como `failed` del emisor antes de reenviar manualmente.

Tampoco se debe colocar un `.handoff` arbitrario a mano en outbox para saltar validaciones. El daemon distribuye mensajes; la validación fuerte normal está en el helper de envío.

## 14. Cómo recibe y termina una tarea un agente

### 14.1 Modo task

[ready_for_next.py](../swarmforge/scripts/swarm_python/entrypoints/ready_for_next.py), `run_task_mode()`:

- Si ya hay una tarea en `in_process`, vuelve a imprimir esa tarea.
- Si hay estados ambiguos, falla en lugar de elegir silenciosamente.
- Si no hay trabajo, imprime `NO_TASK`.
- Si hay correo nuevo, mueve el primer archivo ordenado a `in_process`.
- Añade `dequeued_at`.
- Imprime `TASK`, `FROM`, `TYPE`, `PRIORITY`, `TASK_NAME`, contexto de plan cuando aplica y `PAYLOAD`.

El orden de nombres hace que las prioridades más bajas numéricamente aparezcan primero. El sistema no está interrumpiendo de manera preemptiva una tarea en proceso por otra de mayor prioridad.

### 14.2 Modo batch

El architect está configurado como `batch`.

`run_batch_mode()` toma los mensajes nuevos con la misma prioridad que el primero y crea:

```text
inbox/in_process/batch_<fecha>_<secuencia>/
```

Imprime un `BATCH` y sus `BATCH_ITEM` en orden.

“Batch” no significa que el plan ejecute múltiples features simultáneas. Es una modalidad de consumo de mensajes, no un permiso para violar la secuencialidad del plan.

### 14.3 Cerrar el mensaje actual

[done_with_current.py](../swarmforge/scripts/swarm_python/entrypoints/done_with_current.py):

1. Comprueba la tarea o batch actual.
2. Exige respuesta si corresponde.
3. Agrega `completed_at`.
4. Mueve a `inbox/completed`.
5. Ejecuta `ready_for_next.py` para ofrecer el siguiente trabajo.

Por eso el agente debe leer la salida de `done_with_current.sh`: puede haber recibido ya la siguiente tarea.

### 14.4 La protección `REPLY_REQUIRED`

Para coder, reviewer y architect, terminar una tarea `git_handoff` con `task` exige haber enviado un `git_handoff` propio para esa misma tarea desde que fue recogida.

El helper busca en `outbox` y `sent`, comparando nombre de tarea y timestamps. Una respuesta de una ronda anterior no debería contar.

Si falta:

```text
REPLY_REQUIRED
```

y salida con código 2. No completa la tarea.

Esto se agregó porque terminar la bandeja sin reenviar podía dejar una feature sin dueño siguiente.

Excepción explícita:

```sh
done_with_current.sh --no-reply
```

No es la salida normal del circuito. Es para un paso que legítimamente termina sin respuesta. El specifier y las notas no están sujetos a la misma exigencia.

### 14.5 `merge_and_process` es una instrucción, no un ejecutable de este repo

La búsqueda del código muestra el texto generado y su filtrado para reportes, pero no un programa propio llamado `merge_and_process` que haga el merge automáticamente.

El agente debe interpretar el payload, incorporar el commit recibido en su rama según el trabajo y continuar con su rol. El daemon **no hace el merge ordinario entre coder, reviewer y architect**.

Ejemplo conceptual, no receta universal:

```sh
# Dentro del worktree receptor, limpio y correcto:
git show <commit-recibido>
# Si corresponde y no hay conflictos:
git merge --no-edit <commit-recibido>
# Después, ejecutar la revisión o implementación asignada.
```

Conflictos, cambios locales y política de merge requieren evaluación. Un payload no garantiza por sí solo que el modelo haga esa integración correctamente.

## 15. Scheduler, parking y conservación de sesiones

### 15.1 Estado por rol

`agents.json` contiene aproximadamente:

```json
{
  "coder": {
    "status": "parked",
    "task": "crear-cliente",
    "session_id": "identidad-del-backend-si-aplica",
    "launched_at": null,
    "sent_since_launch": true,
    "idle_since": null,
    "wake_attempts": 0,
    "fast_exits": 0
  }
}
```

Es un ejemplo abreviado, no un archivo para reemplazar el estado real.

Estados:

- `wanted`: debe arrancar en el próximo tick.
- `running`: el swarm considera que el agente está vivo.
- `parked`: quedó solo el shell del pane.
- `failed`: falló repetidamente al arrancar y se suspendieron reintentos.

### 15.2 Cuándo se lanza un rol

`Scheduler._tick_role()` lanza cuando:

- Está `wanted`.
- O está `parked` y tiene correo en `new` o trabajo en `in_process`.

`next_task()` identifica el trabajo pendiente; `plan_session()` decide fresh/resume; `start_role()` arma y envía el launcher.

### 15.3 Cuándo se estaciona

Para estacionar, el scheduler requiere:

1. Que Herdr lo informe ocioso (`idle` o `done`).
2. Que no haya correo ni tarea en proceso.
3. Que haya pasado una gracia de 15 segundos por defecto.
4. Para el primer rol, que haya enviado trabajo desde su lanzamiento.
5. Que se pueda detener y verificar la salida.

El specifier no se estaciona simplemente por esperar una respuesta de la persona al principio de la entrevista.

### 15.4 Cómo se cierra el specifier sin perder lo retomable

Caso ideal con backend reanudable:

1. El specifier trabaja en una feature/conversación.
2. Envía el handoff al coder.
3. El daemon entrega el correo.
4. `on_sent()` marca `sent_since_launch=true` y asocia `task`.
5. Cuando queda ocioso y sin pendientes, el scheduler detiene su proceso.
6. Conserva `task` y `session_id` en `agents.json`.
7. Cuando llega el cierre del architect para la misma tarea, decide `resume=True`.
8. El backend recupera su conversación de su propio almacenamiento.

Eso es **parking**, no cerrar todo el swarm.

Si la persona sale del specifier, existe un camino de cleanup que pretende detener la corrida. Si sale de la TUI con `q`, también se detiene todo. Es distinto de estacionar un rol bajo control del scheduler.

### 15.5 Cuáles backends pueden reanudar

En [sessions.py](../swarmforge/scripts/swarm_python/sessions.py):

```python
RESUMABLE_AGENTS = {"claude", "pi", "hermes"}
```

- **Claude:** UUID al crear, `--resume` con ese UUID al continuar.
- **Pi:** usa `--session-id` para crear o continuar.
- **Hermes:** el id del swarm es un marcador; la identidad efectiva la maneja Hermes. Reanuda la última sesión limitada por `--in <worktree>`.
- **OpenCode/Codex/Copilot/Grok/Kiro:** este launcher inicia fresh. No se debe inferir que sus productos no soporten nunca resume; simplemente **no está cableado aquí**.

**Consecuencia para esta instalación:** specifier, coder y architect usan OpenCode, reviewer usa Codex. Ninguno de esos cuatro recupera conversación mediante `sessions.py` en la configuración actual. Sí recuperan trabajo desde plan, Git, inbox y Engram.

Si tu objetivo es “que el specifier retome exactamente su conversación”, hoy la elección de backend o una mejora del adaptador es determinante.

### 15.6 Misma feature y feature nueva

`plan_session()` reanuda si hay `session_id` y `task` coincide. Si cambia la tarea, genera identidad nueva.

No existe en este estado un historial completo de todas las sesiones de todas las features por rol; hay la referencia actual por rol. Retomar una feature antigua después de haber pasado a otra no equivale a buscar automáticamente su conversación histórica.

### 15.7 Reiniciar todo el swarm

`initial_state()` conserva solamente:

```text
task
session_id
```

Reinicia estados, timers y contadores. El specifier vuelve como `wanted`; los demás se relanzan si sus bandejas justifican trabajo.

`stop_swarm()` intenta detener primero el daemon, después agentes, workspaces y servidor Herdr. Marca roles `parked` y conserva la asociación de trabajo/sesión.

El JSON no copia conversaciones. Si el backend borró su sesión o falla el resume, el scheduler puede intentar una conversación fresca.

### 15.8 Evitar lanzar un agente dentro de otro

Antes del launcher, `_pane_ready_for_launch()` compara foreground y shell.

Si todavía vive un agente, intenta detenerlo antes de escribir el comando de arranque. Esto evita que el texto `bash .../coder.sh` termine como un prompt al modelo, que podría ejecutarlo e iniciar una segunda sesión.

Si Herdr no informa foreground, espera una ventana de aproximadamente 30 segundos y puede realizar un lanzamiento no verificado. Existe un trade-off explícito entre no quedarse bloqueado indefinidamente y la certeza de que el pane está libre.

### 15.9 Parking según plataforma

En POSIX intenta terminar el grupo de procesos con SIGTERM y luego SIGKILL.

En los **cambios locales sin commit** de Windows hay una escalera:

1. Dos Ctrl+C con pausas.
2. Enviar `/exit`.
3. `taskkill /PID <pid> /T /F`.
4. Verificar foreground antes de marcar `parked`.

`/exit` es un recurso práctico, no un comando universal demostrado para todos los CLIs. La verificación posterior importa más que asumir que el agente obedeció.

### 15.10 Correo que no se lee y tareas atascadas

El scheduler reenvía wake-ups con espera creciente:

```text
15 s → 30 s → 60 s → 120 s → 120 s ...
```

Tiene salvaguardas:

- No molestar mientras el agente todavía está arrancando.
- No teclear al primer rol cuando Herdr lo ve ocupado.
- No matar al primer rol por agotar wake-ups.
- Para relanzar otros por correo ignorado, exige espera larga: 30 minutos por defecto y varios ticks ociosos.
- Como máximo una operación de stop por tick.
- Si hay tarea en proceso, el correo nuevo se recoge al completar la actual.

Si coder/reviewer/architect queda ocioso con una tarea sin responder durante unos 5 minutos, recibe un recordatorio de continuar o cerrar correctamente. No se aplica del mismo modo al specifier, que puede estar esperando al humano.

## 16. Responsabilidades del coder, reviewer y architect

### 16.1 Coder

Fuente: [coder.prompt](../swarmforge/roles/coder.prompt).

- Implementa una feature completa.
- Usa el slice y los criterios como referencia.
- Hace TDD: prueba primero, implementación después.
- Ejecuta unitarias y aceptación disponibles.
- Informa bloqueos sin rediseñar silenciosamente la especificación.
- Hace commit.
- Envía siempre al reviewer, incluso cuando recibió ajustes del architect.

No debe truncar el comportamiento para cumplir una cifra de líneas.

### 16.2 Reviewer

Fuente: [reviewer.prompt](../swarmforge/roles/reviewer.prompt).

- Compara código y comportamiento con la especificación.
- Ejecuta pruebas y cobertura.
- Agrega pruebas faltantes.
- Revisa fallos, seguridad, rendimiento e integraciones.
- Debe decir expresamente si no pudo validar una API o base real.
- Puede hacer arreglos contenidos; el retrabajo de comportamiento vuelve al coder.

Header obligatorio:

```text
verdict: pass
```

O:

```text
verdict: fail
```

Sin verdict, el helper rechaza su `git_handoff`.

### 16.3 Tope de rondas

`max_review_rounds` vale 3 por defecto.

Cada retorno reviewer → coder con fail incrementa `review_rounds`. Cuando el contador ya alcanzó el tope, el siguiente fail debe ir al architect.

Ejemplo con el default:

```text
fail 1 → coder; contador 1
fail 2 → coder; contador 2
fail 3 → coder; contador 3
siguiente fail → architect; escalación
```

Este detalle evita interpretar que “el tercer fail necesariamente ya salta al architect”: el gate comprueba el contador antes de aceptar esa nueva ruta.

La escalación agrega `ESCALATION` al cuerpo y `escalated=true` al estado. El architect debe resolver o aceptar explícitamente los defectos restantes e informarlos.

### 16.4 Architect

Fuente: [architect.prompt](../swarmforge/roles/architect.prompt).

- Revisa arquitectura de toda la base, no solo líneas del diff.
- Evalúa límites de módulos, dirección de dependencias, código muerto y ubicación de archivos.
- Hace ajustes estructurales conservando comportamiento.
- Es dueño de la verificación de mutación y DRY cuando las herramientas están disponibles y la tarea lo requiere.
- Envía comportamiento pendiente al coder, que vuelve por reviewer.
- Si no quedan pendientes, envía al specifier el commit final.

El handoff de cierre debe llegar aunque no haya hecho nuevos commits propios. Sin ese mensaje no se dispara la entrega automática de la feature.

### 16.5 Prompts de calidad no son ejecución automática de herramientas

El daemon no contiene un motor que obligue a pasar cobertura, CRAP, DRY y mutación antes de cada transición. Gran parte de esa disciplina está en instrucciones al agente.

El ruteo y estados sí se controlan en código; demostrar que las herramientas realmente corrieron requiere resultados de pruebas, logs y reportes.

## 17. Publicación del PR, merge humano y siguiente feature

### 17.1 Qué dispara la publicación

Un `git_handoff` del architect dirigido únicamente al specifier, con `task` de feature, produce la transición:

```text
arch → awaiting_merge
```

Después de entregar, `PlanFlow.on_delivered()` resuelve el commit final, guarda el reporte y publica si `auto_publish` no está en false.

La publicación corre en un **worker thread** con cola, no directamente bloqueando el bucle de reparto del daemon.

### 17.2 Qué hace `publish()`

1. Comprueba feature y estado.
2. Resuelve el commit final.
3. Comprueba rama base y URL origin fijadas al aprobar.
4. Exige origin.
5. Comprueba que el commit final descienda de `base_commit`, si está registrado.
6. Construye `swarm/<plan>/<feature>`.
7. Hace push del commit exacto a esa rama, sin force.
8. Crea PR con `gh pr create`.
9. Guarda URL, fecha y rama publicada.

Ejemplo conceptual del push:

```text
git push origin <commit-final>:refs/heads/swarm/clientes/crear-cliente
```

No publica usando “lo que esté sin commit en el checkout del usuario”. Publica el objeto commit que llegó al cierre.

### 17.3 Qué contiene el PR

- Resumen.
- Reporte del architect.
- Criterios de aceptación como checklist.
- Métricas orientativas de producción y alcance.
- Rondas de revisión y escalación.
- PR previo del plan, si existe.

`redact()` intenta quitar patrones comunes de secretos y rutas personales del cuerpo del PR. No reemplaza una auditoría de secretos, ni examina todo el contenido del commit.

El plan bajo `.swarmforge/plan/` normalmente no se publica porque está ignorado por Git. Los `.feature` y otros documentos que sí estén versionados pueden estar en el commit final. `PlanFlow` no reescribe ese commit para borrar documentación.

### 17.4 El swarm no mergea PRs

Aunque un commit histórico se titule “publish, merge and advance”, el merge remoto sigue siendo una decisión humana.

El código no hace `gh pr merge` ni push a la rama base. Observa el merge y avanza.

`awaiting_merge` puede existir también cuando hubo un fallo al abrir el PR: la transición ocurre antes de publicar. Mirar el estado por sí solo no garantiza que exista una URL válida de PR.

### 17.5 Cómo detecta el merge

Cada 30 segundos por defecto, `poll_merges()` puede pedir:

```text
gh pr view ... --json state,baseRefName,headRefName,headRefOid,mergeCommit
gh pr list --state merged --head ... --base ...
```

Comprueba que rama base, rama de feature y commit coincidan con lo publicado. No acepta cualquier PR devuelto por `gh`.

También puede comprobar si el commit final ya es ancestro de la rama base remota.

Con squash/rebase, el hash final puede no quedar alcanzable desde la base. Por eso `gh` es importante para reconocer esos merges.

### 17.6 Sincronización después del merge

Al detectarlo:

1. Marca la feature `merged`.
2. Hace fetch.
3. Intenta fast-forward del checkout principal, solo si está en la rama base y sin cambios versionados.
4. Evalúa cada worktree por separado.
5. Mueve los que sean seguros a la nueva base.
6. Conserva y reporta los bloqueados.
7. Si el coder no puede quedar listo, difiere el despacho.
8. Si el coder está listo y el plan lo permite, reserva la próxima feature y escribe el handoff automático.

El despacho siguiente usa el commit actualizado de la base, no el HEAD potencialmente viejo del specifier.

### 17.7 Qué puede bloquear un worktree

- Trabajo en inbox/new o in_process.
- Outbox con envíos pendientes.
- Merge, rebase, cherry-pick o revert sin terminar.
- HEAD detached.
- Rama distinta de la esperada.
- Cambios versionados sin commit.
- Archivos no versionados ajenos al swarm.
- Colisiones con archivos ignorados o copias de runtime.
- No poder preservar commits locales en una referencia de backup.

Los directorios propios del swarm no deben generar falsos positivos de suciedad solo por ser copias no versionadas.

### 17.8 Próxima feature automática

`_advance()`:

- Marca `done` si todas quedaron `merged`.
- Respeta `auto_next=false`.
- Busca la próxima feature lista.
- Pasa por `reserve_feature()` y el mismo control de aprobación/dependencias.
- Encola un handoff con emisor lógico specifier y destinatario coder.
- Envía una nota informativa al specifier.

La primera feature se entrega desde el specifier. Las siguientes pueden salir por código después de cada merge sin tener que mantener al specifier pensando y enviando manualmente.

### 17.9 Fallos de publicación y recuperación

Si falla Git/gh/red/autenticación:

```sh
./swarm plan status
./swarm plan publish crear-cliente
./swarm plan sync
```

Son comandos para la persona cuando interviene manualmente.

Si el PR se cerró sin merge, se registra esa condición; republicar puede usar una rama de revisión `-r2`, `-r3`, etc. Nunca hace force-push para tapar una rama remota conflictiva.

`set-status <id> merged` es una herramienta de reparación, no una prueba de que un merge haya ocurrido. El camino manual sin evidencia de PR/merge/detección es conservador y no siempre reinicia worktrees ni despacha automáticamente.

## 18. Git, worktrees y el problema de la base vieja

### 18.1 El caso que motivó los cambios locales

Supongamos:

```text
base original:       A
coder implementa:    A──B──C
PR se mergea squash: A──S
```

`S` puede contener los cambios de B y C, pero no tener esos commits como ancestros.

Si el coder empieza la feature siguiente desde C:

```text
coder: A──B──C──D
base:  A──S
```

`D` no desciende de `S`. Puede terminarse todo el circuito de código/review/architect y recién descubrir al publicar que la historia está mal.

### 18.2 Solución: comprobar la base al comenzar

Los cambios locales agregan [worktree_ops.py](../swarmforge/scripts/swarm_python/worktree_ops.py) y controles en `swarm_handoff.py`.

La base de una feature se toma del **commit del handoff**, resuelto a hash completo, no del HEAD incidental del checkout del specifier.

`ensure_descends()` contempla:

- HEAD ya contiene la base: no hace falta moverlo.
- Worktree atrasado: fast-forward.
- Historia divergente, pero árbol idéntico: conserva referencia y lo coloca sobre la base.
- Commits propios diferentes: rechaza en vez de descartarlos.
- Cambios sin commit cuando hay que mover: rechaza y explica.

### 18.3 Backups de commits

Antes de mover una historia que dejaría commits locales sin referencia, se crean refs como:

```text
refs/swarmforge/backup/coder/<fecha>
```

No es lo mismo que un backup de archivos sin commit. Una referencia Git protege commits; no captura automáticamente cualquier archivo modificado o no versionado.

### 18.4 Advertencia al entregar al reviewer

Si el commit del coder no desciende de la base registrada, `_descent_warning()` agrega una advertencia al handoff y stderr.

Es orientativa en ese paso: la revisión puede seguir. El objetivo es corregir la base antes del final, no descubrirlo únicamente al publicar.

### 18.5 Recuperación soportada

```sh
./swarm plan rebase crear-cliente
```

**Advertencia:** el nombre puede llevar a confusión. La implementación no es simplemente `git rebase` que reaplica los commits. Coloca el worktree del coder sobre la base, preserva commits anteriores en refs cuando hace falta y vuelve la feature a `pending`.

Es un reinicio controlado del intento, no una promesa de conservar el trabajo aplicado en el árbol actual.

- No debe usarse con agentes trabajando sin coordinar.
- No descarta cambios sin commit para resolver un worktree sucio.
- Hay que leer las advertencias sobre worktrees que no pudo mover.
- Un PR viejo puede seguir apuntando al intento anterior y requerir cierre manual.
- Después el specifier debe enviar de nuevo la feature por el camino normal.

### 18.6 Frescura no es igualdad estricta

`base_freshness()` rechaza commits estrictamente atrasados respecto a la base consultada. Permite commits por delante y **advierte** en divergencias.

También falla abierto con advertencia si no puede comprobarlo por fetch fallido, refs faltantes u otros problemas. Sin remoto puede comparar la base local si la resuelve.

Por lo tanto no es exacto decir “siempre exige que el commit sea idéntico al tip remoto”. Su garantía es más matizada.

`SWARMFORGE_SKIP_BASE_CHECK=1` desactiva este chequeo. No debería ser la reparación normal de una historia incorrecta.

## 19. Memoria compartida con Engram

### 19.1 Qué problema resuelve

Las conversaciones de los roles son distintas. Si el reviewer descubre una restricción de integración, el architect necesita poder encontrarla sin recibir una transcripción gigantesca.

Engram conserva conocimiento curado del proyecto.

### 19.2 Cómo comparten proyecto aunque estén en worktrees diferentes

El launcher exporta el mismo valor para todos:

```text
ENGRAM_PROJECT=<nombre de la raíz>
```

Los prompts exigen:

```sh
engram search "clientes validación" --project "$ENGRAM_PROJECT"
engram context --project "$ENGRAM_PROJECT"
```

No confiar solamente en el cwd del worktree ni en la variable sin flag.

**Límite:** usar solo el nombre del directorio puede colisionar entre proyectos distintos que se llamen igual. Es una convención compartida, no una identidad global criptográfica.

### 19.3 Bootstrap del specifier

Después de aprobar la primera especificación, el specifier debería sembrar:

```sh
engram save "Project overview" \
  "Objetivo, stack, convenciones y decisiones comunes" \
  --type architecture \
  --topic architecture/overview \
  --project "$ENGRAM_PROJECT"
```

El protocolo pide que el specifier haga ese primer guardado; los demás no deberían inventar el overview si falta.

### 19.4 Qué guardar

Después de trabajo significativo:

```text
What: qué se resolvió
Why: por qué
Where: archivos afectados
Learned: descubrimientos útiles
```

Decisiones evolutivas llevan topic estable, como `architecture/clientes` o `pattern/handoff-recovery`.

La memoria no reemplaza Git, el plan ni las bandejas. Tampoco el daemon guarda automáticamente todos esos recuerdos: los agentes deben ejecutar la CLI según instrucciones.

## 20. TUI, observabilidad y notificaciones

### 20.1 Arquitectura de la TUI

- [main.ts](../tui/main.ts): renderer, loop, teclado, señales e instrumentación.
- [app.ts](../tui/src/app.ts): estado y transiciones de la aplicación.
- [io.ts](../tui/src/io.ts): filesystem, procesos Herdr, attach y stop.
- [status.ts](../tui/src/status.ts): estado visible derivado.
- [handoffs.ts](../tui/src/handoffs.ts): lectura de bandejas.
- [herdr.ts](../tui/src/herdr.ts): consulta de agentes Herdr.
- [ui/dashboard.ts](../tui/src/ui/dashboard.ts): composición visual.
- [theme.ts](../tui/src/theme.ts): tokens de color.

Separa lógica testeable de IO y renderizado.

### 20.2 Cómo sabe qué mostrar

Lee `roles.tsv`, bandejas y `agents.json`, y consulta Herdr.

No mantiene una segunda base de datos de tareas independiente. El dashboard es principalmente una vista del estado del runtime.

Actualiza nominalmente cada segundo. El renderer apunta a 30 FPS, sin necesitar redibujar continuamente todo por actividad inexistente.

### 20.3 Entrar a un agente

Con Enter, la TUI:

1. Comprueba si el rol está disponible.
2. Suspende el renderer y entrega la terminal.
3. Enfoca el workspace de Herdr.
4. Abre el cliente Herdr heredando stdio.
5. Al salir del attach, recupera el renderer.

Suspender no es lo mismo que pausar solamente el loop: hay que salir del buffer alternativo y del modo raw para que Herdr controle bien la terminal.

Un rol parked no se ofrece como una conversación viva a la que adjuntarse. El mensaje de indisponibilidad evita confundir eso con una caída de Herdr.

### 20.4 Tamaño mínimo y teclado

Mínimo del dashboard: **80 columnas por 24 filas**.

Soporta navegación por flechas, movimientos tipo Vim, prefijo `Ctrl+k`, ayuda y salida. El menú contiene entradas como logs/costos que actualmente pueden estar deshabilitadas: no hay que confundir un rótulo del menú con una funcionalidad completa de contabilidad.

En Windows `useMouse:false` evita que ciertos reportes SGR de mouse entren como teclas imprimibles y rompan flechas/Enter.

### 20.5 Herdr dentro de Herdr

El cliente anidado puede estar bloqueado por Herdr. La TUI comprueba `HERDR_ENV=1` y muestra una explicación para configurar `allow_nested = true` bajo `[experimental]` o iniciar desde terminal normal.

Eso no se arregla cambiando el prompt del agente: es una restricción del multiplexor.

### 20.6 Salir de la TUI detiene el swarm

`q`, el camino de quit y señales como SIGINT/SIGTERM/SIGHUP intentan ejecutar `swarm stop`.

No es una TUI puramente observadora que al cerrarse deje siempre todo corriendo. Incluso `./swarm tui`, al reabrirla, usa el mismo comportamiento de salida.

Los manejadores de excepción no son idénticos al shutdown por señal: no conviene asumir que cualquier caída imaginable garantiza que no quede un proceso. Se debe comprobar estado ante fallos abruptos.

### 20.7 Dónde mirar logs

```text
.swarmforge/daemon/handoffd.log   Daemon, entregas, scheduler, tracebacks
.swarmforge/logs/plan-flow.log   Publish, detección de merge, sync y dispatch
.swarmforge/logs/tui.log         Eventos de TUI y diagnóstico de attach
```

Pino produce logging estructurado. También hay helpers de log de texto. No conviene asumir que cada línea de `tui.log` tenga siempre un único esquema JSON homogéneo.

`main.ts` conserva logging diagnóstico `key_raw`: registra secuencias de teclado recibidas por el renderer de la TUI. El log ocurre antes de comprobar la vista `attached`; aunque el renderer se suspende al entregar la terminal a Herdr, no conviene tomar eso como una garantía formal de privacidad de toda entrada. Revisar esos registros antes de compartirlos y considerar retirar el diagnóstico temporal.

## 21. Pruebas y herramientas Gherkin

### 21.1 Tres capas principales de verificación

**Backend Python:** planes, gates, scheduler, resiliencia, probes, worktrees y base de features.

**Instalación/update:** conservación de archivos, backups, roles, toolchain y detección de plataforma.

**TUI TypeScript:** aplicación, IO, attach, estado, render, UI y propiedades.

### 21.2 Comandos ejecutados durante esta revisión

```sh
python -m unittest discover -s tests/swarm_python -p 'test_*.py'
python -m unittest discover -s tests/swarm_update -p 'test_*.py'
./node_modules/.bin/bun test tui/test/
```

Resultados observados:

- **Backend Python: 401 tests, OK**, aproximadamente 155,5 segundos.
- **Instalación/update: 38 tests, OK**, aproximadamente 54,6 segundos.
- **TUI: 184 pass y 2 fail**, 186 pruebas en 17 archivos, aproximadamente 5,6 segundos.

En la suite Python aparece deliberadamente un mensaje de rechazo por falta de `decisions.md` como parte de una prueba negativa; no fue un fallo de la suite.

La suite de update emitió un `ResourceWarning` sobre un subprocess todavía en ejecución al ser liberado. La suite terminó OK; no se investigó ese warning como un incidente separado.

No se ejecutaron aceptación completa, build de release, mutación, CRAP ni un ciclo real de agentes contra proveedores. Estos resultados no demuestran que todas las integraciones externas estén disponibles.

### 21.3 Las dos fallas reproducidas de TUI en Windows

Archivo: [tui/test/install.test.ts](../tui/test/install.test.ts).

1. **`launchInstalledTui executes the installed bundle`**: crea un stub `#!/bin/sh` y el helper intenta ejecutarlo directamente como si fuera un binario. Windows devuelve `ENOENT` en esta ejecución. No prueba que el launcher Python de la TUI real falle; el fixture es Unix.
2. **`launchInstalledTui rejects when the installed bundle is missing`**: construye `new RegExp(...)` con una ruta Windows sin escapar las barras invertidas. El error existe, pero el patrón no coincide correctamente.

Son problemas reproducidos y **no fueron corregidos en esta tarea documental**.

### 21.4 Herramientas Gherkin empaquetadas

La distribución incluye:

```text
gherkin-parser
gherkin-ir-dry-checker
gherkin-mutator
```

Precompilados presentes:

```text
template/toolchain/linux-x64/bin/
template/toolchain/windows-x64/bin/
```

En Windows terminan en `.exe`.

Para otras plataformas, `swarm-init` puede compilar desde `vendor/acceptance-pipeline-specification` usando `scripts/build_toolchain.sh`, si Go está disponible.

El build usa `CGO_ENABLED=0` y selecciona GOOS/GOARCH. Puede haber descargas de toolchain/dependencias de Go si no están disponibles localmente; tener código vendorizado no garantiza que cualquier primer build ocurra sin red.

### 21.5 Validación de herramientas al arrancar

`deps.py` no se conforma con que exista un archivo. Ejecuta cada herramienta con `--version`, aplica timeout y clasifica problemas de plataforma/ejecución.

El reporte queda en `.swarmforge/toolchain-check.txt`.

Un toolchain inutilizable no es fatal para proyectos sin Gherkin. El specifier recibe la instrucción de no generar `.feature` si ese reporte indica que no puede ejecutarlos.

### 21.6 Pipeline de aceptación del proyecto

[acceptance/run-acceptance.sh](../acceptance/run-acceptance.sh):

```text
features/*.feature
   │ gherkin-parser
   ▼
build/acceptance/ir/*.json
   │ acceptance/generator.ts
   ▼
build/acceptance/generated/*.test.ts
   │ node --test
   ▼
Resultados de aceptación
```

- `runtime.ts` expande escenarios y despacha steps.
- `steps.ts` mapea pasos a comportamiento del proyecto.
- `world.ts` crea entorno de prueba y adaptadores.
- `runner-adapter.ts` acepta jobs JSON por stdin y responde `test_success`, `test_failure` o `infrastructure_error` para integración con mutación.

Los tests de aceptación de UI usan adaptadores/fixtures; no equivalen a poner cuatro modelos comerciales a trabajar en un repositorio real.

Los scripts ejecutan `.ts` con `node`; la versión/configuración de Node debe soportar ese uso. El requisito genérico de una documentación no sustituye probar los comandos en la versión instalada.

### 21.7 CI y distribución

[ci.yml](../.github/workflows/ci.yml) hace sintaxis Bash, compile-check de Python y build TUI en Linux.

Limitaciones actuales visibles:

- No ejecuta las suites Python de 401 y 38 tests.
- No ejecuta la suite de 186 pruebas TUI.
- El compile-check Python termina con `|| true`, lo que puede ocultar fallos.
- No hay verificación equivalente de todo el runtime Windows dentro de ese workflow de CI.

[release.yml](../.github/workflows/release.yml) pretende compilar TUI para Linux x64/arm64, macOS x64/arm64 y Windows x64, y publicar assets con hashes/manifest.

Hay una inconsistencia estática a revisar: `download-artifact` descarga por defecto en subdirectorios, pero los pasos de publish iteran `dist/*` como si fueran archivos planos. Sin `merge-multiple` o aplanado explícito, la generación de hashes/manifest parece tratar directorios como archivos. No se ejecutó una release para confirmar su resultado remoto.

## 22. Problemas encontrados y soluciones en el historial

Esta cronología usa mensajes y cambios de Git como evidencia. No pretende recordar conversaciones privadas que no están en el repositorio. “Resuelto en historial” significa que existe una implementación/commit correspondiente, no que se haya vuelto a probar cada integración externa hoy.

### 22.1 Mantener cuatro agentes vivos consumía recursos

**Evidencia:** `89fcdec` — `run role agents on demand instead of keeping all four alive`.

**Solución:** scheduler con estados wanted/running/parked, lanzamiento por correo y estacionamiento al quedar libre.

**Archivos:** `scheduler.py`, `agent_state.py`, `handoffd.py`.

**Resultado:** menor cantidad de procesos de IA ociosos. No garantiza exclusión global absoluta.

### 22.2 Reiniciar perdía la referencia de conversación

**Evidencia:** `31b3b8e` — `resume sessions across restarts and re-wake agents with unread mail`.

El mensaje del commit explica que se reescribía `agents.json` desde cero.

**Solución:** conservar `task` y `session_id`, reiniciar timers y estado de proceso.

**Resultado:** continuidad para backends cuyo resume está implementado y cuya conversación sigue existiendo.

### 22.3 Wake-ups llegaban mientras el agente estaba ocupado y se perdían

**Evidencia:** `31b3b8e`, endurecido en `3210131`.

**Solución:** detectar correo sin leer y reenviar recordatorios. La versión actual agrega backoff, protección de arranque, respeto del pane humano y relanzamiento cuidadosamente condicionado.

**Resultado:** que exista correo sin leer deja de depender de una única notificación efímera.

### 22.4 Entrar a roles estacionados parecía una falla de terminal

**Evidencia:** `a41be37` — `show parked roles and refuse to attach to them`.

**Solución:** mostrar lifecycle en TUI y rechazar attach a agentes no disponibles.

**Resultado:** se distingue pane existente de proceso de agente vivo.

### 22.5 Cerrar el dashboard dejaba agentes consumiendo memoria

**Evidencia:** `b4a0c48` — `one command to run the swarm, and quitting the TUI stops it`.

**Solución:** `./swarm` abre TUI y salir llama stop; señales también intentan apagar la corrida.

**Resultado:** operación más simple, con la advertencia de los caminos abruptos de fallo.

### 22.6 tmux bloqueaba la portabilidad nativa a Windows

**Evidencia:** `45994d8` — `replace tmux with herdr for cross-platform terminal sessions`.

**Solución:** reemplazar control de multiplexor por Herdr y aislar sesiones por proyecto.

**Resultado:** runtime sin necesidad de WSL, aunque todavía requiere Bash y bordes de compatibilidad.

### 22.7 PowerShell no entendía los launchers POSIX

**Evidencia:** `56f0292`, después `e806ca2`.

**Solución:** generar `.sh`, ejecutar Bash explícitamente y usar invocación adecuada desde PowerShell.

**Archivos:** `launch.py`, wrappers Windows.

### 22.8 `python3` podía ser un alias no funcional de Microsoft Store

**Evidencia:** cambios de soporte Windows en instaladores y `lib/python.sh`.

**Solución:** probar que `python3 -c ...` funcione y hacer fallback a `python` real; al lanzar el daemon usar `sys.executable` (`b65f550`).

### 22.9 Locks POSIX fallaban en Windows

**Evidencia:** `0a62c34` — `replace POSIX-only fcntl lock with a cross-platform one`.

**Solución:** secuencia de handoff con `msvcrt` en Windows y `fcntl` en POSIX; planes con lock por creación exclusiva de archivo.

### 22.10 Cada agente gastaba el primer turno leyendo las mismas instrucciones

**Evidencia:** `be6d316` — `inline the constitution and role prompt instead of pointing at them`.

**Solución:** concatenar constitución, artículos y rol en el prompt inicial.

**Resultado:** las reglas ya están en contexto al comenzar, sin una cadena repetitiva de lecturas iniciales.

### 22.11 Leer el prompt con `cat` fallaba por PATH del pane

**Evidencia:** `ca0b1e6`.

**Solución:** builtin `$(<archivo)` y comprobación explícita de archivo no vacío.

### 22.12 Los borradores no transportaban suficiente contexto libre

**Evidencia:** `d739899` — `let git_handoff and note drafts carry a free-text body`.

**Solución:** aceptar cuerpo tras línea vacía y anexarlo al payload generado.

**Resultado:** defectos, contexto y reporte pueden acompañar headers estructurados. Algunos prompts todavía dicen “headers-only”, contradicción pendiente de armonizar.

### 22.13 Reviewer saltaba al specifier y omitía arquitectura

**Evidencia:** `17fecd6`.

**Solución:** reviewer no puede enviar al specifier por validación; toda revisión pass va al architect.

**Evolución:** la parte histórica de merge local del specifier fue reemplazada después por `PlanFlow` y PRs automáticos. No hay que leer el diseño actual solo desde ese commit.

### 22.14 La aprobación era demasiado dependiente del comportamiento del modelo

**Evidencia:** `e4f02d6` — plan secuencial con gate humano; `7a505de` — lifecycle y review loop en código.

**Solución:** plan persistido, comando humano, hash de contenido, reserva con lock y ruteo por verdict.

**Resultado:** el helper rechaza iniciar features sin autorización registrada o con alcance modificado.

### 22.15 Se salteaba entrevista o se escribían specs superficiales

**Evidencia:** `fd734af`.

**Solución:** `decisions.md`, mínimo de decisiones, cinco secciones obligatorias y rechazo de placeholders.

**Resultado:** se impone un mínimo estructural verificable. La calidad y veracidad semántica todavía necesitan revisión humana.

### 22.16 El avance de entrega dependía de que un agente hiciera Git/GitHub correctamente

**Evidencia:** `a85d784`.

**Solución:** publicación por código, rama por feature, targets fijados, verificación de PR, detección de merge y siguiente despacho.

**Resultado:** menos pasos críticos de entrega delegados al criterio de un LLM. El usuario conserva el merge remoto.

### 22.17 Binarios Gherkin de Linux fallaban en Windows

**Evidencia:** `55dcd46`.

**Solución:** toolchain por plataforma, `.exe`, build local desde vendor cuando corresponde y prueba efectiva de ejecución.

**Resultado:** ya no se asume que cualquier archivo llamado `gherkin-parser` corre en cualquier SO.

### 22.18 El daemon moría por decodificación de salida en Windows

**Evidencia:** `3210131`.

**Solución:** subprocesses de texto en UTF-8 con `errors="replace"`, entorno UTF-8 y bucle protegido con tracebacks limitados.

**Resultado:** bytes incompatibles con cp1252 no deberían tumbar la coordinación completa.

### 22.19 La copia de scripts hacía parecer sucios a todos los worktrees

**Evidencia:** `3210131` y mejoras locales posteriores.

**Solución:** reconocer paths propios de runtime y excluirlos de Git cuando no están versionados deliberadamente.

**Resultado:** la sincronización no queda diferida eternamente por archivos que el propio swarm creó.

### 22.20 Un agente completaba inbox sin reenviar y cortaba la cadena

**Evidencia:** `3210131`.

**Solución:** `REPLY_REQUIRED` antes de completar tareas de coder/reviewer/architect.

**Resultado:** el sistema detecta una clase concreta de “feature sin siguiente receptor”.

### 22.21 Parking declarado sin que el proceso realmente terminara

**Evidencia:** `3210131`; escalera de stop Windows en los cambios locales actuales.

**Solución:** comprobar foreground, diferir launcher si sigue un agente vivo y mejorar stop con interrupt/exit/kill tree.

**Resultado:** se reduce el riesgo de iniciar conversaciones anidadas y dejar un agente fantasma que recibe launchers como prompts.

### 22.22 Feature nueva sobre historia anterior al squash

**Evidencia:** cambios locales en `worktree_ops.py`, `swarm_handoff.py`, `plan_flow.py` y `test_feature_base.py`.

**Solución:** base tomada del header, comprobación antes de reserva, fast-forward/reset protegido, warning de descendencia y comando humano de recuperación.

**Estado:** existe en el working tree y las pruebas locales pasaron; **no estaba en HEAD como commit publicado** al revisar.

### 22.23 Un worktree bloqueado dejaba atrasados a todos

**Evidencia:** cambios locales de `plan_flow.py` y pruebas asociadas.

**Solución:** sincronización por rol, registro de bloqueados, reintentos y bloqueo especial del despacho si el coder no está listo.

**Estado:** implementación local pendiente de commit. La intención principal es que un reviewer/architect bloqueado no impida actualizar a los demás.

## 23. Hallazgos, contradicciones y limitaciones vigentes

Estos puntos no fueron corregidos: la tarea fue inspeccionar y documentar, preservando los cambios existentes.

### 23.1 La promesa de resume no coincide con los backends elegidos

**Verificado en código y configuración.** `sessions.py` solo reanuda Claude/Pi/Hermes; esta instalación usa OpenCode/Codex.

**Impacto:** preservar `agents.json` no recupera automáticamente la conversación anterior del specifier actual.

**Acción aconsejada:** documentar esa limitación al operar; si se quiere continuidad conversacional, elegir un backend soportado o implementar y probar el adaptador de resume correspondiente.

### 23.2 El cleanup del specifier todavía usa zsh

[swarm-cleanup.sh](../swarmforge/scripts/swarm-cleanup.sh) tiene shebang `#!/usr/bin/env zsh` y sintaxis específica como `<->`.

El launcher se genera para Bash y agrega sintaxis heredada como `&!` al camino de cleanup del primer rol.

**Hallazgo estático:** esto no está alineado con los requisitos de Bash/Windows nativo y merece prueba dedicada. No se ejecutó el camino destructivo de cerrar un specifier real para confirmar su comportamiento en esta máquina.

**Impacto posible:** apagar incompletamente al salir del specifier. El camino explícito `swarm stop` es distinto, aunque tiene su propia advertencia siguiente.

### 23.3 La parada del daemon usa `kill` de forma no portable

`stop_handoff_daemon.py::process_alive()` consulta `kill -0` y el stop usa `kill -TERM/-KILL` con el PID guardado por Python.

El propio inicializador tiene una implementación distinta que reconoce que PIDs nativos Windows no siempre son visibles para Git Bash `kill` y usa `tasklist`.

**Hallazgo estático:** la estrategia no es uniforme. Además, el stop puede crear y retirar muy rápido el archivo `stop` si considera muerto un PID vivo.

**Riesgo:** un daemon viejo podría no cerrarse correctamente y una nueva corrida podría superponerse. No se reprodujo con un swarm real durante esta revisión.

### 23.4 Hay instrucciones antiguas de merge del specifier

`constitution/articles/project.prompt` todavía incluye en un diagrama que el specifier hace merge de la rama de trabajo, mientras párrafos posteriores y el prompt del rol le prohíben hacerlo y delegan a `PlanFlow`.

**Interpretación actual basada en código:** el specifier informa; `PlanFlow` publica y la persona mergea el PR.

### 23.5 El prompt del specifier pide un comando que el CLI le prohíbe

En recuperación de publicación, el prompt indica que ejecute `swarm plan publish <id>`, pero `plan_cli.py` lo restringe a una persona si `SWARMFORGE_ROLE` está definida.

**Operación correcta hoy:** el specifier comunica causa y comando; la persona lo ejecuta en su terminal.

### 23.6 `headers-only` y cuerpos libres no están armonizados

El código permite cuerpo libre; artículos de memoria/handoff conservan afirmaciones de borradores solo con headers o cuerpos muy cortos. El cierre del architect necesita además reporte que se copia al PR.

**Impacto:** agentes pueden omitir información necesaria o interpretar mal qué texto se admite.

### 23.7 Herramientas externas: reglas de instalación contradictorias

El artículo de proyecto permite instalar ciertas dependencias con permiso, mientras artículos compartidos y architect son más restrictivos.

**Acción aconsejada:** resolver la política de forma explícita en la constitución y adaptar artículos/roles. No asumir que todos los backends resolverán la contradicción igual.

### 23.8 Aprobación e instrucciones no equivalen a aislamiento

Agentes con autoaprobación corren como tu usuario. Los worktrees separan archivos de código, no permisos de red o de sistema.

- `SWARMFORGE_ROLE` es una convención, no identidad segura.
- Los registros de aprobación son editables por el usuario del proceso.
- `allowed_paths` no impide escribir fuera de esas rutas.
- El filtro de secretos del PR no inspecciona todo el repositorio.

Usar un entorno de confianza y permisos acordes sigue siendo responsabilidad operativa.

### 23.9 El helper no demuestra la calidad real del trabajo

Puede demostrar que se envió al siguiente rol y que el commit existe. No puede probar que un reviewer efectivamente leyó todos los archivos o ejecutó cada prueba declarada en lenguaje natural.

La confianza debe combinar pruebas reproducibles, reportes, revisión humana y señales de ejecución.

### 23.10 La siguiente feature manual no exige siempre el primer elemento de la lista

`next_ready_feature()` elige el primero listo para el camino automático. El gate manual comprueba feature pending, dependencias y ausencia de otra en curso, pero no compara siempre que el id sea idéntico al primer elemento que esa función devolvería.

Si se necesita orden total estricto, declarar dependencias secuenciales explícitas. “Features secuenciales” garantiza principalmente una en curso, no necesariamente un orden arbitrario no expresado en dependencias.

### 23.11 Parte del estado y la entrega tiene tolerancia deliberada a inconsistencias

`apply_handoff_transition()` puede dejar un estado desfasado sin rechazar todo el correo. Fallos de escritura pueden producir advertencias y requerir reparación humana.

`agents.json` tiene escritura atómica simple, pero no el mismo sistema de reintentos de `plan.json` para locks de Windows.

El diseño es resiliente y recuperable, no una máquina distribuida transaccional perfectamente consistente.

### 23.12 Verificación incompleta en CI y release

Los resultados locales no están todos protegidos por el workflow actual. Las dos fallas de TUI Windows y la inconsistencia de empaquetado de release deben tratarse como trabajo pendiente, no como arreglos ya confirmados.

### 23.13 SHA opcional en el instalador

`fetch_release_bundle()` puede continuar sin checksum si no consigue el `.sha256`: imprime que omite integridad. No es correcto prometer verificación obligatoria en todos los caminos.

El fallback de manifest construye la URL de descarga con `/releases/download/${RELEASE_TAG}/...`; con `latest` merece revisión porque no usa el mismo formato especial de la búsqueda inicial.

Tampoco vi uso efectivo de `SWARM_RELEASE_BASE_URL`, aunque documentación anterior la menciona.

### 23.14 Documentación archivada no es fuente de verdad actual

`docs/_archive` conserva diseños antiguos. `docs/project-overview.md` también contiene advertencias sobre carpetas que supuestamente no existen, pero hoy **sí existe** `vendor/acceptance-pipeline-specification`.

Para resolver divergencias: código actual → pruebas → configuración actual → historial contextual → documentación antigua.

## 24. Recorrido completo con un ejemplo

Este ejemplo usa el módulo ficticio “Gestión de clientes”. Los nombres son ilustrativos; no afirma que el repositorio ya implemente ese módulo.

### Paso 1: instalar en un proyecto destino

```sh
cd /ruta/a/swarm-local
./install_swarm.sh /ruta/a/mi-proyecto
```

Elegís lenguajes, permisos y backend/modelo por rol.

### Paso 2: arrancar

```sh
cd /ruta/a/mi-proyecto
./swarm
```

Aparece el dashboard. Solo el specifier arranca inicialmente.

### Paso 3: hablar con el specifier

Entrás al workspace del specifier y explicás el objetivo. El agente pregunta una cuestión por vez y registra cada decisión.

Ejemplo de respuesta humana:

```text
Los clientes se identifican por UUID. El correo debe ser único sin distinguir mayúsculas.
```

Esa decisión queda en `decisions.md`. Después de completar entrevista, escribe `general.md`, features y `plan.json` draft.

### Paso 4: revisar y aceptar

En tu propia terminal:

```sh
./swarm plan validate
./swarm plan show
./swarm plan approve
```

Si falta una decisión válida o una sección, approve falla. Corregir estructura y volver a revisar es parte del flujo, no un error que se deba esquivar.

### Paso 5: primer handoff

El specifier sincroniza la base y obtiene hash real. Envía `crear-cliente` al coder.

Resultado observable:

```text
plan: crear-cliente pasa pending → coding
outbox del specifier: aparece .handoff
```

### Paso 6: reparto y lanzamiento

El daemon copia a inbox/new del coder, archiva el enviado y el scheduler lanza al coder.

El coder ejecuta:

```sh
ready_for_next.sh
```

Recibe visión general, slice de crear-cliente y commit de partida.

### Paso 7: estacionar el specifier

Cuando queda ocioso y sin bandeja pendiente, el scheduler lo estaciona. La conversación solo se retomará como tal si el backend está soportado para resume. Con el OpenCode de la configuración actual será un arranque fresh posterior, orientado por archivos y memoria.

### Paso 8: implementar y revisar

Coder hace TDD, commit y handoff a reviewer conservando `task: crear-cliente`.

Después ejecuta `done_with_current.sh`.

Reviewer detecta que el correo se compara con sensibilidad a mayúsculas, contrario a la spec. Envía:

```text
type: git_handoff
to: coder
priority: 50
task: crear-cliente
commit: <hash-real-de-10-caracteres>
verdict: fail

La especificación exige unicidad sin distinguir mayúsculas. Agregar el caso de colisión.
```

El coder corrige, hace commit y vuelve al reviewer. Cuando pasa, reviewer envía `verdict: pass` al architect.

### Paso 9: arquitectura y cierre

Architect comprueba estructura, límites y verificaciones que pueda ejecutar. Si necesita comportamiento adicional, lo devuelve al coder.

Cuando está conforme, manda su commit final al specifier y completa su batch.

### Paso 10: PR automático

El código de `PlanFlow`, no el specifier, hace push a:

```text
swarm/clientes/crear-cliente
```

y abre un PR hacia la rama base aprobada.

El specifier informa URL, riesgos y verificación. Limpia su mensaje de cierre con `done_with_current.sh`.

### Paso 11: merge humano

La persona revisa el PR y lo mergea en GitHub. El swarm no toma esa decisión.

### Paso 12: avance

El daemon detecta el merge; el worker sincroniza base y worktrees seguros. Si el coder está listo, reserva `consultar-cliente` y encola el siguiente handoff.

Si el coder está sucio, no se destruye su trabajo para avanzar: se difiere el despacho y se registra el motivo.

### Paso 13: fin del plan

Tras el último merge y procesamiento del flujo, el plan pasa a `done` y el specifier recibe nota informativa.

Cerrar la TUI detiene la corrida; el plan y correo permanecen en disco.

## 25. Manual de operación y diagnóstico

### 25.1 Comandos principales de la persona

```sh
./swarm                       # arranca y abre dashboard
./swarm --no-tui               # arranca sin dashboard
./swarm tui                    # abre dashboard sobre runtime existente
./swarm stop                   # intenta detener toda la corrida

./swarm plan validate
./swarm plan show
./swarm plan status
./swarm plan approve
./swarm plan publish <id>
./swarm plan sync
./swarm plan rebase <id>
./swarm plan set-status <id> <estado>
```

`--force` en set-status es reparación excepcional, no uso cotidiano.

### 25.2 Comandos ordinarios del agente

```sh
ready_for_next.sh
swarm_handoff.sh <borrador>
done_with_current.sh
```

No ejecutar launchers de `.swarmforge/prompts/` para comprobar correo. Esos scripts crean procesos y conversaciones.

No iniciar otro `./swarm` desde un worktree para “arreglar” helpers faltantes.

### 25.3 Si el coder nunca arranca

Revisar en este orden:

1. ¿`swarm_handoff.sh` devolvió `HANDOFF QUEUED` o un error?
2. ¿El plan está approved y el hash sigue coincidiendo?
3. ¿La feature está pending o ya fue reservada?
4. ¿Existe `.handoff` en outbox del specifier?
5. ¿Llegó a `inbox/new` del coder?
6. ¿Hay error en `failed`?
7. ¿`agents.json` muestra parked, running o failed?
8. ¿Herdr puede informar foreground?
9. ¿Está el backend instalado y autenticado?
10. ¿Se gastaron los reintentos de salida rápida?

No reenviar cinco veces la misma feature sin mirar su reserva y las bandejas.

### 25.4 Si hay correo pero no lo procesa

- Si está en `new`, revisar wake-ups y estado del agente.
- Si ya está en `in_process`, repetir ready debe devolver la tarea actual.
- Si hay tarea actual, no se espera que abandone esa tarea para recoger otra automáticamente.
- Si quedó ocioso sin responder, buscar `stale-task-nudge`.
- Si el agente muestra un launcher como texto, investigar parking/foreground; no aceptar la ejecución de otra sesión como arreglo.

### 25.5 Si `done_with_current.sh` devuelve `REPLY_REQUIRED`

Primero enviar el handoff correcto de esa tarea; después completar.

No usar `--no-reply` solo para silenciar el error. Eso puede reproducir exactamente el corte de cadena que la protección intenta evitar.

### 25.6 Si el PR no aparece

```sh
./swarm plan status
```

Comprobar:

- ¿La feature llegó a awaiting_merge?
- ¿Hay `final_commit`?
- ¿Existe origin?
- ¿`gh` está instalado y autenticado?
- ¿El commit desciende de base_commit?
- ¿Cambió base_branch u origin desde la aprobación?
- ¿Hubo rechazo de push o ya existe una rama remota con otra historia?

Mirar `plan-flow.log`; corregir la causa y ejecutar como persona:

```sh
./swarm plan publish <id>
```

### 25.7 Si mergeaste y no empieza la siguiente

```sh
./swarm plan sync
```

Buscar:

- `pr-mismatch`: los datos de GitHub no coinciden con lo publicado.
- `sync-deferred`: hay un bloqueo concreto.
- `worktree-blocked`: rol y razón.
- `auto_next=false`.
- Aprobación invalidada.
- Otra feature en curso.
- Dependencias incompletas.
- Inbox/outbox pendiente que impide mover un worktree.

Limpiar una bandeja significa completar o recuperar el trabajo por protocolo, no borrar todos los archivos manualmente.

### 25.8 Inspección Git no destructiva

```sh
git status --short
git worktree list
git branch --show-current
git log --oneline --decorate -15
git for-each-ref refs/swarmforge/backup/
git -C .worktrees/coder status --short
```

Antes de reset, rebase o forzar estado: detener/coordinar agentes, guardar trabajo y entender qué refs se preservan.

### 25.9 Logs para una incidencia

Conservar:

- Estado del plan y feature.
- `agents.json` sin datos innecesarios.
- Headers del handoff relevante.
- Motivo del `.error` si existe.
- Fragmento de `handoffd.log`.
- Fragmento de `plan-flow.log`.
- SO, versión de Python, Herdr y CLI del rol.

Revisar secretos y rutas personales antes de compartir. No pegar credenciales ni conversaciones completas si no son necesarias.

### 25.10 Variables de ajuste más relevantes

- `SWARMFORGE_PARK_GRACE_MS`: gracia de parking; default 15000 ms.
- `SWARMFORGE_WAKE_GIVEUP_MS`: espera antes de relanzar por correo ignorado; default 1800000 ms.
- `SWARMFORGE_STALE_TASK_MS`: umbral orientativo de tarea atascada; default 300000 ms.
- `SWARMFORGE_NUDGE_AFTER_MS`: intervalo de recordatorio de tarea atascada; default 60000 ms.
- `SWARMFORGE_PLAN_POLL_S`: frecuencia base de consulta de merges; default 30 s.
- `SWARMFORGE_SKIP_BASE_CHECK=1`: omite chequeo de frescura de base.
- `SWARMFORGE_PREVENT_SLEEP=0`: deshabilita prevención de suspensión.
- `SWARMFORGE_PLAN_DIR`: override de ubicación del plan, útil sobre todo en pruebas/layouts especiales.
- `SWARMFORGE_APPROVALS_DIR`: override del registro de aprobación.
- `SWARMFORGE_PLATFORM`: selección de triple de instalación de toolchain.

La prevención de suspensión usa `caffeinate` en macOS o `systemd-inhibit` cuando procede en Linux. No hay implementación equivalente para Windows en `sleep_inhibit.py`.

## 26. Mapa de archivos y funciones para seguir leyendo

Las referencias se dan por archivo y símbolo para que sigan siendo útiles aunque cambien números de línea.

### Arranque y configuración

- [swarm](../swarm): shim de entrada.
- [swarmforge.sh](../swarmforge/scripts/swarmforge.sh): selección de Python y delegación.
- [lib/python.sh](../swarmforge/scripts/lib/python.sh): intérprete disponible.
- [swarm_cli.py](../swarmforge/scripts/swarm_python/entrypoints/swarm_cli.py): `run_main`, `stop_swarm`, `start_handoff_daemon`, `run_tui`.
- [paths.py](../swarmforge/scripts/swarm_python/paths.py): `Context`, `build_context`.
- [config.py](../swarmforge/scripts/swarm_python/config.py): `RoleRow`, `parse_config`.
- [deps.py](../swarmforge/scripts/swarm_python/deps.py): `require`, `check_backends`, `check_toolchain`.
- [git_ops.py](../swarmforge/scripts/swarm_python/git_ops.py): `prepare_worktrees`, `prepare_handoff_dirs`, `sync_worktree_scripts`, `ensure_runtime_git_excludes`.

### Prompts, procesos y sesiones

- [launch.py](../swarmforge/scripts/swarm_python/launch.py): `_write_agent_instruction`, `build_launch_command`, `write_launch_script`, `start_role`.
- [sessions.py](../swarmforge/scripts/swarm_python/sessions.py): `RESUMABLE_AGENTS`, `plan_session`, `RESUME_PROMPT`.
- [agent_state.py](../swarmforge/scripts/swarm_python/agent_state.py): `initial_state`, `load`, `save`.
- [scheduler.py](../swarmforge/scripts/swarm_python/scheduler.py): `Scheduler.tick`, `_check_running`, `_launch`, `_stop_and_verify`, `HerdrPanes`.
- [herdr_ops.py](../swarmforge/scripts/swarm_python/herdr_ops.py): `session_name`, `workspace_create`, `pane_run`, `pane_foreground`.
- [sleep_inhibit.py](../swarmforge/scripts/swarm_python/sleep_inhibit.py): `prefix`.

### Mensajes y bandejas

- [swarm_handoff.py](../swarmforge/scripts/swarm_python/entrypoints/swarm_handoff.py): envío, reserva, transición y escritura final.
- [handoff/format.py](../swarmforge/scripts/swarm_python/handoff/format.py): parser de borradores y headers reservados.
- [handoff/validate.py](../swarmforge/scripts/swarm_python/handoff/validate.py): `ROLE_ROUTES`, `_verdict_errors`, `validate`.
- [handoff/sequence.py](../swarmforge/scripts/swarm_python/handoff/sequence.py): contador y locking por plataforma.
- [handoff/project.py](../swarmforge/scripts/swarm_python/handoff/project.py): descubrimiento de raíz y bandeja actual.
- [handoff/inbox.py](../swarmforge/scripts/swarm_python/handoff/inbox.py): orden y formato de salida.
- [handoffd.py](../swarmforge/scripts/swarm_python/entrypoints/handoffd.py): `deliver`, `poll_once`, `ErrorThrottle`.
- [ready_for_next.py](../swarmforge/scripts/swarm_python/entrypoints/ready_for_next.py): `run_task_mode`, `run_batch_mode`.
- [done_with_current.py](../swarmforge/scripts/swarm_python/entrypoints/done_with_current.py): `require_reply`, cierre de tarea y siguiente recepción.
- [stop_handoff_daemon.py](../swarmforge/scripts/swarm_python/entrypoints/stop_handoff_daemon.py): parada y limitaciones de PID descritas.

### Plan y entrega

- [plan.py](../swarmforge/scripts/swarm_python/plan.py): `validate`, `scan_decisions`, `slice_for`, `content_hash`, `approve`, `reserve_feature`, `check_specifier_to_coder`, `apply_handoff_transition`, `base_freshness`.
- [plan_cli.py](../swarmforge/scripts/swarm_python/plan_cli.py): comandos humanos y restricciones por entorno.
- [plan_flow.py](../swarmforge/scripts/swarm_python/plan_flow.py): `publish`, `_detect`, `_sync_locked`, `_worktree_blocker`, `_advance`, `rebase_feature`.
- [worktree_ops.py](../swarmforge/scripts/swarm_python/worktree_ops.py): `ensure_descends`, `cleanliness`, `backup_ref`.

### Instrucciones de los agentes

- [constitution.prompt](../swarmforge/constitution.prompt).
- [project.prompt](../swarmforge/constitution/articles/project.prompt).
- [specifier.prompt](../swarmforge/roles/specifier.prompt).
- [coder.prompt](../swarmforge/roles/coder.prompt).
- [reviewer.prompt](../swarmforge/roles/reviewer.prompt).
- [architect.prompt](../swarmforge/roles/architect.prompt).
- [handoffs.prompt](../swarmforge/scripts/shared-articles/handoffs.prompt).
- [workflow.prompt](../swarmforge/scripts/shared-articles/workflow.prompt).
- [memory.prompt](../swarmforge/scripts/shared-articles/memory.prompt).
- [engineering.prompt](../swarmforge/scripts/shared-articles/engineering.prompt).

### Instalación, distribución y verificación

- [swarm-init](../swarm-init): wizard, reconciliación de roles, backups, toolchain y copia.
- [install_swarm.sh](../install_swarm.sh): descarga/build de TUI y delegación.
- [provider-models.tsv](../provider-models.tsv): fallback de catálogo.
- [agent_probe.py](../swarmforge/scripts/swarm_python/agent_probe.py): descubrimiento por backend.
- [hermes_probe.py](../swarmforge/scripts/swarm_python/hermes_probe.py): credenciales y catálogo Hermes.
- [build_toolchain.sh](../scripts/build_toolchain.sh): cross-build Go.
- [tui/build.ts](../tui/build.ts): binario Bun autocontenido y stubs de targets nativos ausentes.
- [test_scheduler.py](../tests/swarm_python/test_scheduler.py): sesiones, parking y wake-ups.
- [test_plan.py](../tests/swarm_python/test_plan.py): estructura, entrevista y aprobación.
- [test_plan_flow.py](../tests/swarm_python/test_plan_flow.py): publicación, detección y sincronización.
- [test_resilience.py](../tests/swarm_python/test_resilience.py): tolerancia y validaciones de fallo.
- [test_feature_base.py](../tests/swarm_python/test_feature_base.py): reproducción con Git real y origin bare local del problema de base.
- [test_update.py](../tests/swarm_update/test_update.py): actualización y toolchain.
- [CI](../.github/workflows/ci.yml) y [release](../.github/workflows/release.yml).

## 27. Conclusiones y prioridades

### Qué funciona como diseño implementado

Este swarm coordina cuatro responsabilidades sobre un repositorio único, con worktrees, prompts ensamblados, mensajes persistentes, agentes bajo demanda, gates de plan y publicación automática por feature.

La separación más valiosa es:

```text
Modelo: entender, programar, revisar y reportar.
Código: validar protocolo, conservar estado, entregar, lanzar, estacionar y publicar.
Persona: definir intención, aprobar alcance, resolver excepciones y mergear PRs.
```

### Qué no conviene dar por supuesto

- Guardar sesión de swarm no equivale siempre a guardar/reanudar conversación del modelo.
- Los cuatro roles no necesariamente están vivos a la vez.
- `HANDOFF QUEUED` no equivale a tarea ejecutada.
- `done_with_current` no equivale a feature mergeada.
- `awaiting_merge` no demuestra que se abrió un PR sin errores.
- Worktrees no son un sandbox.
- Prompts de calidad no prueban que las verificaciones corrieron.
- Soporte opcional de Langfuse no implica una conexión activa.
- Código local con pruebas pasando no equivale a cambio publicado en Git.

### Prioridades razonables de mantenimiento

1. Resolver la brecha de resume de OpenCode/Codex si la continuidad conversacional es requisito.
2. Unificar cleanup y parada del daemon para Windows/Bash, con pruebas de procesos reales controlados.
3. Armonizar constitución y prompts con `PlanFlow`, permisos humanos y cuerpos libres.
4. Corregir las dos pruebas de TUI que fallan en Windows.
5. Ejecutar suites relevantes en CI y quitar el ocultamiento de errores de compile-check.
6. Revisar aplanado de artifacts y checksums obligatorios en release/instalación.
7. Versionar y publicar, después de revisión, los cambios locales de base/worktrees y stop Windows.
8. Verificar en una corrida controlada la cadena real completa con los CLIs/modelos elegidos, incluyendo salida, parking, reanudación disponible y recuperación de fallos.

Esta guía describe lo observado y permite seguir el flujo desde el comando inicial hasta el siguiente PR sin depender de que el chat conserve todo el contexto.
