#!/usr/bin/env bash
# Build the mq_prometheus exporter binary INSIDE the Go container (#1065, epic
# .github#198 fallout). Runs in ghcr.io/vergil-project/dev-go — which already ships
# the required Go, so nothing auto-downloads a toolchain (the old in-guest
# `golang-go` + `go build` pulled the full go1.25 toolchain and overflowed the
# fatbox build guest's disk). Invoked by `mqlab.mqexporter.ensure_mq_exporter_binary`
# with these mounts:
#   /cache  <- the host-durable build cache root (read: /cache/mq/<MQ tarball>)
#   /out    <- build/cache/mq-exporter (write: the produced binary)
#   /build.sh (this file, read-only)
# Env: MQ_EXPORTER_REF (mq-metric-samples git ref to build).
#
# mq_prometheus is cgo against the IBM MQ SDK and links the server binding
# libmqm_r (mq-golang has no client-only build tag), so we extract the SDK from the
# already-cached MQ tarball and give ld -rpath-link to resolve libmqm_r's transitive
# NEED on libmqe_r from the SDK prefix. The binary links dynamically and finds
# /opt/mqm/lib64 on the node at runtime (the unit sets LD_LIBRARY_PATH).
#
# Builds for the arch the caller selects via TARGET_ARCH (arm64 | x64), matching the
# host/guest arch: the caller runs the container with the matching --platform and the
# right-arch MQ media is chosen below. (#1100 — was x86-only, which broke arm64 bakes
# with `exec format error` on Apple Silicon.)
set -euo pipefail

REF="${MQ_EXPORTER_REF:?MQ_EXPORTER_REF must be set}"
SDK=/tmp/mqsdk
SRC=/tmp/mq-metric-samples
export PATH="/usr/local/go/bin:${PATH}"

# Target arch: the caller sets TARGET_ARCH; fall back to the container's own arch.
ARCH="${TARGET_ARCH:-$(case "$(uname -m)" in aarch64 | arm64) echo arm64 ;; *) echo x64 ;; esac)}"
case "$ARCH" in
  arm64) MQSUFFIX="UbuntuLinuxARM64" ;;
  x64) MQSUFFIX="UbuntuLinuxX64" ;;
  *) echo "ERROR: unsupported TARGET_ARCH=$ARCH (want arm64|x64)" >&2; exit 1 ;;
esac

MQTAR="$(ls /cache/mq/*-IBM-MQ-Advanced-for-Developers-${MQSUFFIX}.tar.gz 2>/dev/null | head -1)"
[ -n "$MQTAR" ] || { echo "ERROR: no ${MQSUFFIX} MQ tarball under /cache/mq" >&2; exit 1; }
echo "mq-exporter build: go $(go version | awk '{print $3}'), ref ${REF}, MQ media $(basename "$MQTAR")"

# 1. Extract just the MQ debs cgo needs (headers + server/runtime/client libs).
mkdir -p /tmp/mqx "$SDK"
tar xzf "$MQTAR" -C /tmp/mqx --wildcards \
  'MQServer/ibmmq-sdk_*.deb' 'MQServer/ibmmq-runtime_*.deb' 'MQServer/ibmmq-server_*.deb' \
  'MQServer/ibmmq-client_*.deb' 'MQServer/ibmmq-gskit_*.deb'
for d in /tmp/mqx/MQServer/ibmmq-*.deb; do dpkg-deb -x "$d" "$SDK"; done
test -f "$SDK/opt/mqm/inc/cmqc.h" || { echo "ERROR: MQ SDK headers missing after extract" >&2; exit 1; }

# 2. mq-metric-samples at the pinned ref.
git clone --depth 1 --branch "$REF" https://github.com/ibm-messaging/mq-metric-samples.git "$SRC"

# 3. cgo build. GOTOOLCHAIN=local: never auto-download — dev-go's Go must satisfy go.mod.
cd "$SRC"
export CGO_CFLAGS="-I$SDK/opt/mqm/inc"
export CGO_LDFLAGS="-L$SDK/opt/mqm/lib64 -Wl,-rpath-link,$SDK/opt/mqm/lib64"
export GOFLAGS=-mod=mod
export GOTOOLCHAIN=local
mkdir -p /out
OUT="/out/mq_prometheus-${ARCH}"
go build -o "$OUT" ./cmd/mq_prometheus

test -s "$OUT" || { echo "ERROR: build produced no binary" >&2; exit 1; }
echo "mq-exporter build: wrote $OUT ($(stat -c %s "$OUT") bytes)"
