# Distribution

Cómo se empaqueta y publica SwarmForge para que `install_swarm.sh` lo baje en cualquier máquina.

## Formato de release

Cada GitHub Release sube, además del tag y los notes, los siguientes assets:

| Asset | Contenido |
| --- | --- |
| `swarm-tui-<os>-<arch>.bin` | binario ELF/Mach-O compilado por `tui/build.ts` para esa plataforma |
| `swarm-tui-<os>-<arch>.bin.sha256` | sha256 del anterior (una sola línea: `<hex>  `) |
| `VERSION` | versión semántica (`MAJOR.MINOR.PATCH`) del release |
| `manifest.json` | índice opcional con todas las URLs y hashes, por si se prefiere descarga atómica |

`<os>` ∈ { `linux`, `darwin`, `windows` }, `<arch>` ∈ { `x64`, `arm64` }.

`install_swarm.sh` arma el nombre a partir de `uname -s` y `uname -m`, descarga el binario, verifica contra `.sha256`, y si cualquiera de las dos cosas falla cae al build local.

## Cómo se publica un release

Localmente, antes de taggear:

```sh
cd tui
npm install
npm run build                  # produce dist/swarm-tui para esta plataforma
```

El workflow de GitHub Actions (`.github/workflows/release.yml`) corre esa misma build en una matriz:

```yaml
strategy:
  matrix:
    include:
      - os: ubuntu-latest
        artifact: swarm-tui-linux-x64.bin
      - os: ubuntu-22.04-arm
        artifact: swarm-tui-linux-arm64.bin
      - os: macos-latest
        artifact: swarm-tui-darwin-arm64.bin
      - os: macos-13
        artifact: swarm-tui-darwin-x64.bin
      - os: windows-latest
        artifact: swarm-tui-windows-x64.bin
```

En cada corrida: `npm ci && npm run build`, sube el binario como `artifact`, y en el job de release los re-sube con el nombre correcto + su `.sha256`.

Para cortarlo a mano:

```sh
VERSION="v$(cat VERSION)"
gh release create "$VERSION" \
    swarm-tui-linux-x64.bin \
    swarm-tui-linux-arm64.bin \
    swarm-tui-darwin-arm64.bin \
    swarm-tui-darwin-x64.bin \
    swarm-tui-windows-x64.bin \
    "swarm-tui-*.bin.sha256" \
    VERSION \
    --generate-notes
```

## Toolchain Gherkin por plataforma

`template/toolchain/<triple>/bin/` guarda los binarios estáticos de Go (`gherkin-parser`, `gherkin-mutator`, `gherkin-ir-dry-checker`) por plataforma (`<linux|darwin|windows>-<x64|arm64>`; en Windows con `.exe`). Hoy están precompilados `linux-x64` y `windows-x64`. `install_swarm.sh` detecta la plataforma y la pasa a `swarm-init` por `SWARMFORGE_PLATFORM`; `swarm-init` (sin esa variable usa su propia detección) copia solo el build del equipo a `.swarmforge/toolchain/bin` y escribe `.swarmforge/toolchain/PLATFORM`. Para una plataforma sin precompilado compila desde `vendor/acceptance-pipeline-specification` con `scripts/build_toolchain.sh` si hay Go; si no, avisa y continúa. `scripts/build_toolchain.sh` y `vendor/` deben permanecer junto a `swarm-init` (copia completa del repositorio) para que la compilación bajo demanda funcione. `PLATFORM` solo se escribe tras una instalación completa; si no se puede instalar nada, un toolchain existente se conserva sin tocar y en una instalación nueva el directorio no se crea. El respaldo `.swarmforge/backup-<timestamp>/` de `--update` incluye `.swarmforge/toolchain` completo (`bin/` y `PLATFORM`) tal como estaba antes de reemplazarlo.

Para refrescar los precompilados antes de un release (tras cambiar el código Go de `vendor/`): `scripts/build_toolchain.sh linux-x64` y `scripts/build_toolchain.sh windows-x64`, y commitear `template/toolchain/`. `scripts/build_toolchain.sh --dry-run <triple>` imprime los comandos `go build` sin ejecutarlos.

## Variables de entorno de `install_swarm.sh`

| Variable | Default | Función |
| --- | --- | --- |
| `SWARM_RELEASE_REPO` | `nousresearch/swarm-local` | repo de GitHub para buscar el release |
| `SWARM_RELEASE_TAG` | `latest` | tag concreto o `latest` |
| `SWARM_RELEASE_BASE_URL` | derivado | si se quiere usar un mirror distinto de `https://github.com/<repo>/releases/...` |

## Versionado

Sigue semver. Subir minor por cada cambio de comportamiento en `install_swarm.sh`, `swarm-init` o el flujo del swarm. Patch por fixes que no rompen compatibilidad.

Si cambia el contrato de la TUI (por ejemplo, agrega flags nuevos a `swarm_cli.py`), el `VERSION` debe subir minor — los binarios viejos van a quedarse sin soporte.
