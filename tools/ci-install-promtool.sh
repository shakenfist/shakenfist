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
# That cache is why the binary has its own pinned checksum rather than
# only the tarball having one. The cache lives at a predictable path
# under a world-writable /tmp, on runners which also execute untrusted
# pull request code, and it survives the job that created it. Checking
# only the download leaves the path that actually runs on most jobs --
# the one where the file is already there -- unverified. So the check is
# on the artifact that gets executed, and it runs every time rather than
# only after a download. A stamp file recording "this was verified once"
# would be no use: whoever can write the binary can write the stamp.
#
# Usage:
#
#   echo "$(tools/ci-install-promtool.sh)" >> "$GITHUB_PATH"
#
# Takes an optional install directory; everything except the directory
# name goes to stderr so the caller can use the output directly.

set -euo pipefail

VERSION=3.14.0
TARBALL_SHA256=f665c6da19eb7ba399c915d30c7d9793c9b417bf8a749b504bc470678631478d
PROMTOOL_SHA256=9c752bb87eec945b2d7797d20815e2dc54b0d3fed2d2f17df019dbd74560f743
TARBALL="prometheus-${VERSION}.linux-amd64.tar.gz"
URL="https://github.com/prometheus/prometheus/releases/download/v${VERSION}/${TARBALL}"

DEST="${1:-/tmp/promtool-${VERSION}}"

# True only for an executable at ${DEST}/promtool which is the pinned
# build. Anything else -- absent, truncated, or replaced -- is a miss, so
# a tampered cache is re-downloaded over rather than trusted or reported
# as a failure.
cached_binary_is_pinned() {
    [ -x "${DEST}/promtool" ] &&
        echo "${PROMTOOL_SHA256}  ${DEST}/promtool" |
            sha256sum -c --status -
}

if ! cached_binary_is_pinned; then
    if [ -e "${DEST}/promtool" ]; then
        echo "Cached promtool at ${DEST} is not the pinned build;" \
             "reinstalling." 1>&2
    fi

    echo "Installing promtool ${VERSION} into ${DEST}..." 1>&2
    work=$(mktemp -d)
    trap 'rm -rf "${work}"' EXIT

    curl -sSLo "${work}/${TARBALL}" "${URL}" 1>&2
    echo "${TARBALL_SHA256}  ${work}/${TARBALL}" | sha256sum -c - 1>&2
    tar xzf "${work}/${TARBALL}" -C "${work}" --strip-components=1 \
        "prometheus-${VERSION}.linux-amd64/promtool" 1>&2

    mkdir -p "${DEST}"
    mv "${work}/promtool" "${DEST}/promtool"

    # The tarball was the pinned one, so this can only fail if
    # PROMTOOL_SHA256 itself is wrong -- which would otherwise show up as
    # an unexplained re-download on every single job.
    if ! cached_binary_is_pinned; then
        echo "Installed promtool does not match PROMTOOL_SHA256." 1>&2
        exit 1
    fi
fi

"${DEST}/promtool" --version 1>&2
echo "${DEST}"
