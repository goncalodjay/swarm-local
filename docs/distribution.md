# Distribution

Cómo se empaqueta y publica SwarmForge para que `install.sh` (el one-liner) y `swarm-kura update` lo instalen sin clonar el repo.

## Layout instalado

```
~/.swarm-kura/
  versions/<versión>/     # vX.Y.Z (release), <rama>-<sha7> (rama) o local-... (--from)
    bin/swarm-kura        # el CLI (bash 3.2 compatible)
    swarm-init  install.sh  template/  provider-models.tsv  scripts/  vendor/
    tui/dist/swarm-tui    # binario de la TUI para esta plataforma
    INSTALL_INFO          # canal, ref, repo, triple, fecha
  current                 # nombre de la versión activa (archivo de texto, no symlink: funciona en Windows)
  config                  # repo y rama elegida con --branch (canal dev)
~/.local/bin/swarm-kura   # lanzador: lee current y ejecuta esa versión (+ swarm-kura.cmd en Windows)
```

Actualizar = instalar la versión nueva al lado y cambiar `current` (atómico; si falla, la anterior sigue activa). Se conservan las últimas 3.

## Formato de release

Un tag `vX.Y.Z` igual a `VERSION` dispara `.github/workflows/release.yml`, que publica:

| Asset | Contenido |
| --- | --- |
| `swarm-kura-<os>-<arch>.tar.gz` (+ `.sha256`) | lo que instala `install.sh`: archivos de runtime del repo + `tui/dist/swarm-tui[.exe]` + `template/toolchain/<os>-<arch>` |
| `swarm-tui-<os>-<arch>.bin` (+ `.sha256`) | la TUI sola (para `install_swarm.sh` y para instalaciones de rama) |
| `manifest.json`, `VERSION` | índice con hashes |

Plataformas: `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64`, `windows-x64`. El workflow compila el toolchain Go para todas en un job (cross-compile) y la TUI con Bun en un runner nativo por plataforma (OpenTUI necesita su paquete nativo).

Sin releases publicadas, `install.sh` instala la rama por defecto desde `codeload.github.com` y compila la TUI con Bun en la máquina (una vez por versión).

## Cómo publicar

```sh
echo 0.3.0 > VERSION && git commit -am "release 0.3.0"
git tag v0.3.0 && git push origin master v0.3.0
```

Los usuarios lo reciben con `swarm-kura update` (que además actualiza el proyecto en el que están parados).

## Toolchain Gherkin por plataforma

`template/toolchain/<triple>/bin/` guarda los binarios estáticos de Go (`gherkin-parser`, `gherkin-mutator`, `gherkin-ir-dry-checker`) por plataforma (`<linux|darwin|windows>-<x64|arm64>`; en Windows con `.exe`). Están precompilados `linux-x64`, `linux-arm64`, `darwin-arm64`, `darwin-x64` y `windows-x64`. `install_swarm.sh` detecta la plataforma y la pasa a `swarm-init` por `SWARMFORGE_PLATFORM`; `swarm-init` (sin esa variable usa su propia detección) copia solo el build del equipo a `.swarmforge/toolchain/bin` y escribe `.swarmforge/toolchain/PLATFORM`. Para una plataforma sin precompilado compila desde `vendor/acceptance-pipeline-specification` con `scripts/build_toolchain.sh` si hay Go; si no, avisa y continúa. `scripts/build_toolchain.sh` y `vendor/` deben permanecer junto a `swarm-init` (copia completa del repositorio) para que la compilación bajo demanda funcione. `PLATFORM` solo se escribe tras una instalación completa; si no se puede instalar nada, un toolchain existente se conserva sin tocar y en una instalación nueva el directorio no se crea. El respaldo `.swarmforge/backup-<timestamp>/` de `--update` incluye `.swarmforge/toolchain` completo (`bin/` y `PLATFORM`) tal como estaba antes de reemplazarlo.

Para refrescar los precompilados antes de un release (tras cambiar el código Go de `vendor/`): `scripts/build_toolchain.sh --all`, y commitear `template/toolchain/`. `scripts/build_toolchain.sh --dry-run <triple>` imprime los comandos `go build` sin ejecutarlos.

## Variables de entorno

| Variable | Default | Función |
| --- | --- | --- |
| `SWARM_KURA_REPO` | `goncalodjay/swarm-local` | repo de GitHub de donde instalar |
| `SWARM_KURA_HOME` | `~/.swarm-kura` | dónde viven las versiones |
| `SWARM_KURA_BIN_DIR` | `~/.local/bin` | dónde se crea el lanzador |
| `GITHUB_TOKEN` | — | evita el rate limit de la API de GitHub |
| `SWARM_RELEASE_REPO` / `SWARM_RELEASE_TAG` | — | solo para `install_swarm.sh` (instalación desde un clon) |

## Versionado

Sigue semver. Subir minor por cada cambio de comportamiento en `install_swarm.sh`, `swarm-init` o el flujo del swarm. Patch por fixes que no rompen compatibilidad.

Si cambia el contrato de la TUI (por ejemplo, agrega flags nuevos a `swarm_cli.py`), el `VERSION` debe subir minor — los binarios viejos van a quedarse sin soporte.
