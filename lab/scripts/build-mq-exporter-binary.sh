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
# The lab's guests are x86 (X64 MQ), so this always builds linux/amd64 — the caller
# runs the container with --platform=linux/amd64.
set -euo pipefail

REF="${MQ_EXPORTER_REF:?MQ_EXPORTER_REF must be set}"
SDK=/tmp/mqsdk
SRC=/tmp/mq-metric-samples
export PATH="/usr/local/go/bin:${PATH}"

MQTAR="$(ls /cache/mq/*-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz 2>/dev/null | head -1)"
[ -n "$MQTAR" ] || { echo "ERROR: no UbuntuLinuxX64 MQ tarball under /cache/mq" >&2; exit 1; }
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
go build -o /out/mq_prometheus-x64 ./cmd/mq_prometheus

test -s /out/mq_prometheus-x64 || { echo "ERROR: build produced no binary" >&2; exit 1; }
echo "mq-exporter build: wrote /out/mq_prometheus-x64 ($(stat -c %s /out/mq_prometheus-x64) bytes)"
