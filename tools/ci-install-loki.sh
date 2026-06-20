#!/bin/bash
# Copyright 2019 Michael Still and contributors
#
# ci-install-loki.sh -- download a pinned single-binary Loki release,
# drop in a minimal filesystem-backed config, install a systemd unit,
# and wait for Loki to report ready. Runs on the target box (not over
# SSH from a runner).
#
# This is the functional-CI Loki used to prove the phase-2 log shipper
# end to end (see docs/plans/PLAN-remove-syslog-forwarding-phase-03-ci-loki.md).
# It is intentionally single-tenant and unauthenticated -- CI only.
#
# Usage:
#
#   sudo bash tools/ci-install-loki.sh
#
# Takes no arguments. Loki always runs on the primary node and listens
# on 0.0.0.0:3100 so other inner-cluster nodes can push to it.

set -euo pipefail

# Pinned Loki release. Any 3.x is fine for our purposes -- phase 2 puts
# all identifiers in the JSON line body, not in structured metadata, so
# there is no schema-version floor.
LOKI_VERSION="3.4.2"
LOKI_ZIP="loki-linux-amd64.zip"
LOKI_URL="https://github.com/grafana/loki/releases/download/v${LOKI_VERSION}/${LOKI_ZIP}"

# Optional integrity check. Set to the upstream-published sha256 of
# loki-linux-amd64.zip for ${LOKI_VERSION} to harden the download against
# tampering/truncation. Left empty by default because this runs in a
# network-isolated authoring environment where the checksum could not be
# fetched; when populated, a mismatch aborts the install loudly. To find
# it: see the SHA256SUMS asset on the v${LOKI_VERSION} release page.
LOKI_SHA256=""

LOKI_BIN="/usr/local/bin/loki"
LOKI_CONFIG_DIR="/etc/loki"
LOKI_CONFIG="${LOKI_CONFIG_DIR}/config.yaml"
LOKI_DATA_DIR="/var/lib/loki"
LOKI_UNIT="/etc/systemd/system/loki.service"

echo "Installing prerequisites (curl, unzip)..."
# Retry-on-apt-lock loop: cloud-init or unattended-upgrades may hold the
# lock briefly during VM startup. Bound the loop so we eventually give up
# rather than spin forever.
attempt=0
max_attempts=60
until apt-get update -y && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y curl unzip; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge "${max_attempts}" ]; then
        echo "apt-get install failed after ${max_attempts} retries" >&2
        exit 1
    fi
    echo "apt locked or transient failure, retrying" \
        "(${attempt}/${max_attempts})..."
    sleep 5
done

echo "Downloading Loki v${LOKI_VERSION}..."
tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

attempt=0
until curl -fsSL -o "${tmpdir}/${LOKI_ZIP}" "${LOKI_URL}"; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge 10 ]; then
        echo "Failed to download Loki after 10 retries" >&2
        exit 1
    fi
    echo "Download failed, retrying (${attempt}/10)..."
    sleep 5
done

# Verify the download when a checksum is configured. A mismatch aborts
# loudly rather than installing something unexpected. Skipped (with a
# notice) when LOKI_SHA256 is empty.
if [ -n "${LOKI_SHA256}" ]; then
    echo "Verifying checksum..."
    if ! echo "${LOKI_SHA256}  ${tmpdir}/${LOKI_ZIP}" | sha256sum -c -; then
        echo "Loki checksum verification failed" >&2
        echo "Expected ${LOKI_SHA256}, got:" >&2
        sha256sum "${tmpdir}/${LOKI_ZIP}" >&2
        exit 1
    fi
else
    echo "No LOKI_SHA256 configured, skipping checksum verification."
fi

echo "Installing Loki binary to ${LOKI_BIN}..."
unzip -o "${tmpdir}/${LOKI_ZIP}" -d "${tmpdir}"
install -m 0755 "${tmpdir}/loki-linux-amd64" "${LOKI_BIN}"

echo "Creating data directories under ${LOKI_DATA_DIR}..."
mkdir -p "${LOKI_DATA_DIR}/chunks" \
         "${LOKI_DATA_DIR}/rules" \
         "${LOKI_DATA_DIR}/tsdb-index" \
         "${LOKI_DATA_DIR}/tsdb-cache" \
         "${LOKI_DATA_DIR}/compactor" \
         "${LOKI_DATA_DIR}/wal"

echo "Writing Loki config to ${LOKI_CONFIG}..."
mkdir -p "${LOKI_CONFIG_DIR}"
cat > "${LOKI_CONFIG}" <<'EOF'
# Minimal single-binary Loki config for functional CI. Single-tenant,
# unauthenticated, filesystem-backed storage, tsdb schema v13.
auth_enabled: false

server:
  http_listen_address: 0.0.0.0
  http_listen_port: 3100
  grpc_listen_port: 9096

common:
  instance_addr: 127.0.0.1
  path_prefix: /var/lib/loki
  storage:
    filesystem:
      chunks_directory: /var/lib/loki/chunks
      rules_directory: /var/lib/loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

storage_config:
  tsdb_shipper:
    active_index_directory: /var/lib/loki/tsdb-index
    cache_location: /var/lib/loki/tsdb-cache
  filesystem:
    directory: /var/lib/loki/chunks

compactor:
  working_directory: /var/lib/loki/compactor

limits_config:
  # CI Loki is fed by a fresh cluster; accept anything to avoid spurious
  # rejections during the functional run.
  reject_old_samples: false
  allow_structured_metadata: true

ruler:
  storage:
    type: local
    local:
      directory: /var/lib/loki/rules

analytics:
  reporting_enabled: false
EOF

echo "Installing systemd unit ${LOKI_UNIT}..."
cat > "${LOKI_UNIT}" <<EOF
[Unit]
Description=Grafana Loki (functional CI)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=${LOKI_BIN} -config.file=${LOKI_CONFIG}
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

echo "Enabling and starting loki..."
systemctl daemon-reload
systemctl enable --now loki

echo "Waiting for loki to become ready..."
attempt=0
until curl -fsS http://localhost:3100/ready 2>/dev/null | grep -q "ready"; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge 60 ]; then
        echo "loki did not become ready within 60 attempts" >&2
        systemctl status loki || true
        journalctl -u loki --no-pager -n 50 || true
        exit 1
    fi
    sleep 2
done

echo "Loki ready."
