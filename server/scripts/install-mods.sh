#!/usr/bin/env bash
# Install BepInEx mods for the Valheim dedicated server.
#
# Mods are downloaded from Thunderstore using their stable versioned
# URLs. Each mod's .dll files land in either
# /opt/valheim/data/bepinex/plugins/ or /opt/valheim/data/bepinex/patchers/
# depending on its type -- BepInEx loads "plugins" at runtime and
# "patchers" before assembly resolution, so the distinction matters.
#
# Idempotent: a marker file at <target_dir>/.installed-<name>-<version>
# short-circuits the download on subsequent boots. To upgrade a mod,
# bump its version below; the old marker (different version) won't
# match and we'll re-download.
#
# Runs as a oneshot systemd unit (install-mods.service) BEFORE
# valheim.service starts, so the mod files are already on disk by the
# time `docker compose up` mounts /opt/valheim/data into the container
# at /config.
#
# lloesche's image auto-installs the BepInEx pack itself when
# BEPINEX=true is set in docker-compose.yml -- we don't need to handle
# the loader here, only the additional plugins/patchers on top of it.
#
# Why Thunderstore rather than bundled-in-repo zips:
#   - No binaries committed to git
#   - Stable versioned URLs (e.g.
#     https://thunderstore.io/package/download/<author>/<name>/<version>/)
#   - Version bumps are a one-line edit + terraform apply

set -euo pipefail

BEPINEX_DIR="/opt/valheim/data/bepinex"
PLUGINS_DIR="${BEPINEX_DIR}/plugins"
PATCHERS_DIR="${BEPINEX_DIR}/patchers"

mkdir -p "${PLUGINS_DIR}" "${PATCHERS_DIR}"

# Pinned mod list. Entries are pipe-delimited:
#   <author>|<name>|<version>|<target subdir under bepinex/>
# Version bumps: change the version string here and re-apply Terraform;
# the next boot's install-mods.sh run will detect the missing marker
# and re-download.
MODS=(
  # Modding framework. Plugins (incl. PlanBuild) link against this.
  # 2026-05-03: bumped 2.28.0 -> 2.29.0 as an experiment. Jotunn 2.29's
  # Thunderstore manifest dropped the explicit HookGenPatcher dep
  # (only BepInExPack remains), so PlanBuild's hook-dependent paths
  # may now resolve without a separately-loaded patcher -- which we
  # can't load anyway due to lloesche's merge_mod orphaning the
  # bind mount (see PRD 2026-05-03 decision log).
  "ValheimModding|Jotunn|2.29.0|plugins"

  # MonoMod runtime hook generator. Required by Jotunn for IL patching.
  # Goes in patchers/ (loaded before assembly resolution).
  "ValheimModding|HookGenPatcher|0.0.4|patchers"

  # PlanBuild itself. Server-side install enables blueprint marketplace
  # + admin-enforced terrain restrictions. Clients must install their
  # own client-side copy of the same version to actually use the
  # planning hammer / blueprint rune.
  "MathiasDecrock|PlanBuild|0.18.4|plugins"
)

for entry in "${MODS[@]}"; do
  IFS='|' read -r AUTHOR NAME VERSION TARGET <<<"$entry"
  TARGET_DIR="${BEPINEX_DIR}/${TARGET}"
  MARKER="${TARGET_DIR}/.installed-${NAME}-${VERSION}"

  if [ -f "${MARKER}" ]; then
    echo "[install-mods] ${AUTHOR}/${NAME} ${VERSION} already installed, skipping"
    continue
  fi

  # Drop any previous-version marker for this mod so we don't accumulate
  # stale ones over time. The .dll files themselves get overwritten by
  # the cp -f below; if a mod renames its .dll between versions, manual
  # cleanup is required (out of scope for this script).
  rm -f "${TARGET_DIR}/.installed-${NAME}-"*

  echo "[install-mods] downloading ${AUTHOR}/${NAME} ${VERSION}"
  TMP="$(mktemp -d)"
  # shellcheck disable=SC2064 -- intentional: TMP captured by value at trap-set time
  trap "rm -rf '${TMP}'" EXIT

  curl -fsSL --retry 3 --retry-delay 2 \
    "https://thunderstore.io/package/download/${AUTHOR}/${NAME}/${VERSION}/" \
    -o "${TMP}/mod.zip"
  unzip -q "${TMP}/mod.zip" -d "${TMP}/extracted"

  # Thunderstore zips have varied internal layouts: some put .dll files
  # at root, some under plugins/, some under patchers/. We don't care
  # about the source layout -- we just take every .dll in the archive
  # and copy it to TARGET_DIR. The mod's manifest determines whether
  # it's a plugin or a patcher, which we encode in the MODS list above
  # (the TARGET column).
  found=$(find "${TMP}/extracted" -name '*.dll' -type f | wc -l)
  if [ "$found" -eq 0 ]; then
    echo "[install-mods] ERROR: no .dll files found in ${NAME} ${VERSION} zip" >&2
    exit 1
  fi
  find "${TMP}/extracted" -name '*.dll' -type f \
    -exec cp -fv {} "${TARGET_DIR}/" \;

  touch "${MARKER}"
  rm -rf "${TMP}"
  trap - EXIT
done

# Permissions: lloesche's image runs the Valheim process as a non-root
# user inside the container, but our /opt/valheim/data mount is owned
# by root with 0755 directories. The image's bootstrap chowns
# /config/bepinex/* on startup (BEPINEX=true triggers this), so we
# don't need to touch ownership here -- root-owned files at 0644 are
# readable by the container's user via the chown step.

echo "[install-mods] all mods installed"
