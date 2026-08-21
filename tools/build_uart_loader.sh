#!/bin/sh
# Builds the read-only SC14452 UART dump loader (452dump.bin) used by
# `gigaset_uart_dump.py --loader`.
#
# The loader's source is not part of this project: it is cross-compiled from
# Gigaset's own published SC14452 opensource package, mirrored by the
# Osmocom DECT project at
#   https://gitea.osmocom.org/dect/gigaset_elements_bl26_opensource
#
# This script clones that package into a temporary directory, applies
# patches/452dump-flash-read-only.patch (this project's own contribution - it
# adds a read-only dump mode to Gigaset's stock flash programmer) and builds
# the result inside a throwaway Debian container with the cr16 toolchain the
# vendor package ships. Nothing from the vendor package is stored in this
# repository - see the "Ownership of the UART loader" section in README.md.
set -eu

OPENSOURCE_REPO="https://gitea.osmocom.org/dect/gigaset_elements_bl26_opensource.git"
OUTPUT="${1:-452dump.bin}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH="$SCRIPT_DIR/patches/452dump-flash-read-only.patch"

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 1; }
command -v patch >/dev/null 2>&1 || { echo "patch is required" >&2; exit 1; }
[ -f "$PATCH" ] || { echo "missing $PATCH" >&2; exit 1; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
REPO_DIR="$WORKDIR/gigaset_elements_bl26_opensource"

echo "Fetching $OPENSOURCE_REPO ..."
# Sparse checkout: the full tree includes a Linux source path with a literal
# '*' in the filename. We only need two directories out of it anyway, and on
# Windows git refuses to build an index containing that path at all (even one
# it would never write to disk) unless core.protectNTFS is disabled for these
# two commands - it is not touched anywhere else, so this stays local to them.
git -c core.protectNTFS=false clone --no-checkout --depth 1 "$OPENSOURCE_REPO" "$REPO_DIR"
(
  cd "$REPO_DIR"
  git sparse-checkout init --no-cone
  printf '%s\n' '/tools/cr16-tools/*' '/src/dialog/uart/target/*' \
    > .git/info/sparse-checkout
  git -c core.protectNTFS=false checkout
)

TARGET_DIR="$REPO_DIR/src/dialog/uart/target"
echo "Applying $PATCH ..."
# The vendor files mix CRLF/CR/LF line endings, which makes a line-oriented
# patch fail on unrelated context lines. Normalizing to LF first is safe -
# the cr16 compiler does not care - and does not change any code semantics.
sed -i 's/\r$//' "$TARGET_DIR/Makefile" "$TARGET_DIR/flprogr.c"
patch -p1 -d "$REPO_DIR" < "$PATCH"

echo "Cross-compiling the loader (cr16-elf, linux/386 container) ..."
# On Git Bash for Windows, the shell auto-converts arguments that look like
# absolute paths before they reach docker.exe - which mangles the
# container-side paths below (e.g. /tmp) unless MSYS_NO_PATHCONV=1 disables
# that conversion. With it disabled the host-side mount path has to be
# pre-converted to native Windows form instead. Both cygpath and
# MSYS_NO_PATHCONV are no-ops outside Git Bash, so this is harmless on
# Linux/macOS.
if command -v cygpath >/dev/null 2>&1; then
  DOCKER_REPO_DIR="$(cygpath -m "$REPO_DIR")"
else
  DOCKER_REPO_DIR="$REPO_DIR"
fi
MSYS_NO_PATHCONV=1 docker run --rm --platform linux/386 \
  -v "$DOCKER_REPO_DIR:/src" \
  -w /tmp \
  debian:bookworm-slim \
  sh -lc '
    set -e
    apt-get update
    apt-get install -y --no-install-recommends make libc6 libstdc++6 zlib1g
    cp -a /src/tools/cr16-tools /tmp/cr16-tools
    cp -a /src/src/dialog/uart/target /tmp/target
    chmod -R a+rx /tmp/cr16-tools
    export PATH=/tmp/cr16-tools/bin:$PATH
    cd /tmp/target
    touch -t 202601010000 flprogr.c start.s Makefile config.mk link.lds
    make LD=cr16-elf-ld.real 452dump
    cp 452dump.bin /src/src/dialog/uart/target/
  '

cp "$REPO_DIR/src/dialog/uart/target/452dump.bin" "$OUTPUT"
echo "Built: $OUTPUT"
