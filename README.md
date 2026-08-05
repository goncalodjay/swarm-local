# Swarm local

Plantilla local y autónoma para iniciar SwarmForge en otros proyectos con agentes Pi. No descarga `swarm-forge` ni consulta GitHub al ejecutar `swarm-init` o `./swarm`.

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

`./swarm` requiere `bb`, `tmux`, `git` y `pi`. Las instrucciones de los roles prohíben descargar herramientas: si el proyecto requiere una herramienta de lenguaje o aceptación, debe estar ya disponible localmente o el agente pedirá indicaciones.

`template/` es la fuente de verdad de las configuraciones compartidas. Los cambios de un proyecto ya inicializado no modifican esta plantilla.
