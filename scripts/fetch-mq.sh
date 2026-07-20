#!/usr/bin/env bash
# scripts/fetch-mq.sh - download IBM MQ Advanced for Developers (no-charge) into
# gitignored build/cache/mq/. Fetches the host-arch Ubuntu deb tarball AND the x86_64
# RHEL/RDQM tarball (LinuxX64), so a fresh clone has both arms' artifacts (#276).
# Never commit these binaries.
set -euo pipefail
VER="9.4.5.0"
BASE="https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv"
DEST="$(cd "$(dirname "$0")/.." && pwd)/build/cache/mq"
mkdir -p "$DEST"

# The Ubuntu suffix tracks the host arch (= the native Ubuntu guest arch); LinuxX64
# (x86_64 RHEL/RDQM) is fetched on every host. This mirrors manifest.tarball_name's
# host-arch resolution for the un-pinned Ubuntu fat boxes (#103 D10).
case "$(uname -m)" in
  aarch64 | arm64) UBU="UbuntuLinuxARM64" ;;
  x86_64 | amd64) UBU="UbuntuLinuxX64" ;;
  *)
    echo "unsupported host arch: $(uname -m)" >&2
    exit 1
    ;;
esac

for SUFFIX in "$UBU" "LinuxX64"; do
  TAR="${VER}-IBM-MQ-Advanced-for-Developers-${SUFFIX}.tar.gz"
  if [ ! -f "$DEST/$TAR" ]; then
    curl -fL --retry 3 -o "$DEST/$TAR.part" "$BASE/$TAR"
    mv "$DEST/$TAR.part" "$DEST/$TAR"
  fi
  # Record the checksum on first download; verify on every later run.
  if [ -f "$DEST/$TAR.sha256" ]; then
    (cd "$DEST" && sha256sum -c "$TAR.sha256")
  else
    (cd "$DEST" && sha256sum "$TAR" >"$TAR.sha256")
    echo "recorded checksum: $(cat "$DEST/$TAR.sha256")"
  fi
  echo "ok: $DEST/$TAR"
done
