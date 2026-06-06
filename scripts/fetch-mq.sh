#!/usr/bin/env bash
# scripts/fetch-mq.sh - download IBM MQ Advanced for Developers (no-charge)
# arm64 Ubuntu debs into gitignored build/mq/. Never commit these binaries.
set -euo pipefail
VER="9.4.5.0"
TAR="${VER}-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
URL="https://public.dhe.ibm.com/ibmdl/export/pub/software/websphere/messaging/mqadv/${TAR}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/build/mq"
mkdir -p "$DEST"
if [ ! -f "$DEST/$TAR" ]; then
  curl -fL --retry 3 -o "$DEST/$TAR.part" "$URL"
  mv "$DEST/$TAR.part" "$DEST/$TAR"
fi
# Record the checksum on first download; verify on every later run.
if [ -f "$DEST/$TAR.sha256" ]; then
  (cd "$DEST" && sha256sum -c "$TAR.sha256")
else
  (cd "$DEST" && sha256sum "$TAR" > "$TAR.sha256")
  echo "recorded checksum: $(cat "$DEST/$TAR.sha256")"
fi
# awk reads all input - avoids SIGPIPE under pipefail (head closes early).
tar -tzf "$DEST/$TAR" | awk 'NR<=8'
echo "ok: $DEST/$TAR"
