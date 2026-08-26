#!/bin/bash
# Copyright 2019 Michael Still and contributors
#
# ci-install-promtool.sh -- make a pinned promtool available, and print
# the directory it is in on stdout.
#
# promtool is what validates examples/prometheus-database-load-rules.yaml
# as PromQL rather than merely as YAML. Without it
# test_the_generated_promql_parses skips, and a skip which is the normal
# case is not coverage -- a mismatched paren in the rule generator would
# then ship and fail at rule load time on an operator's Prometheus.
#
# It is only distributed inside the whole Prometheus tarball, which is
# around 100MB, so the extracted binary is cached and re-downloaded only
# when it is missing. The static CI runners keep /tmp between jobs, so in
# practice this downloads once per runner.
#
# Usage:
#
#   echo "$(tools/ci-install-promtool.sh)" >> "$GITHUB_PATH"
#
# Takes an optional install directory; everything except the directory
# name goes to stderr so the caller can use the output directly.

set -euo pipefail

VERSION=3.14.0
SHA256=f665c6da19eb7ba399c915d30c7d9793c9b417bf8a749b504bc470678631478d
TARBALL="prometheus-${VERSION}.linux-amd64.tar.gz"
URL="https://github.com/prometheus/prometheus/releases/download/v${VERSION}/${TARBALL}"

DEST="${1:-/tmp/promtool-${VERSION}}"

if [ ! -x "${DEST}/promtool" ]; then
    echo "Installing promtool ${VERSION} into ${DEST}..." 1>&2
    work=$(mktemp -d)
    trap 'rm -rf "${work}"' EXIT

    curl -sSLo "${work}/${TARBALL}" "${URL}" 1>&2
    echo "${SHA256}  ${work}/${TARBALL}" | sha256sum -c - 1>&2
    tar xzf "${work}/${TARBALL}" -C "${work}" --strip-components=1 \
        "prometheus-${VERSION}.linux-amd64/promtool" 1>&2

    mkdir -p "${DEST}"
    mv "${work}/promtool" "${DEST}/promtool"
fi

"${DEST}/promtool" --version 1>&2
echo "${DEST}"
