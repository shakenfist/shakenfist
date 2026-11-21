#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-cluster-nodes.sh -- print the test cluster's nodes other than the
# primary, one per line, as "<name> <under-cloud instance uuid> <address>".
#
# Two CI steps need to reach the cluster's nodes over ssh: the node
# lifecycle lane's cached-image upload, which wants a hypervisor rather
# than the primary, and tools/ci-restart-cluster-nodes.sh, which power
# cycles every node the test may have killed afterwards. Both used to carry the same hand-written
# list, "addresses=(10.0.0.20 10.0.0.21 10.0.0.22 10.0.0.23 10.0.0.24)",
# paired positionally with sf1_uuid..sf5_uuid. That list is the
# slim-primary topology, which is not in this repository: it is
# ansible/ci-topology-slim-primary.yml in shakenfist/actions, reached
# through build-smoke-cluster@main, unpinned. The list was correct and
# nothing made it stay correct, and both of its failure modes were quiet
# (issue #4320):
#
# * a node removed or renumbered there left a step uploading to an
#   address which no longer answers, failing for a reason that said
#   nothing about topology;
# * a node *added* there was never chosen, because RANDOM % 5 keeps
#   picking from the first five, so the coverage the upload step exists
#   to provide silently narrowed. The step's own comment said its
#   purpose was exercising non-primary uploads; it could not notice that
#   it had stopped covering one.
#
# So ask the cluster build, which already says. ci-environment.sh is
# written by shakenfist/actions' ci-include-common-localhost.yml from one
# loop over the topology's nodes: "nodes" lists them, and for each node
# it exports "<node>" as the address the runner reaches it by (the same
# ansible_ssh_host the deploy used) and "<node>_uuid" as its under-cloud
# instance. tools/ci-jwks-ca.sh already reads it this way. Using that
# list rather than a pattern over variable names means no naming
# convention stands between the topology and this script: a node added
# there appears here on the next run with no edit, whatever it is
# called, and a node the list names but the file does not describe
# fails loudly, naming it.
#
# The primary is left out, deliberately, and by name: build-smoke-cluster
# exports its address as "${primary}", so "primary" is the node name that
# action already depends on. Neither caller wants it. The node lifecycle
# test runs on the primary, so it is never the test's victim and never
# needs power cycling, and the upload step exists to exercise an upload
# to a node which is *not* the primary.
#
# Usage:
#
#   tools/ci-cluster-nodes.sh
#
# Requires the "nodes" list and the per-node variables from
# ci-environment.sh, which the caller must have sourced. Exits non-zero,
# with the reason on stderr and nothing on stdout, rather than printing
# a short or guessed list: a caller which silently got fewer nodes than
# the cluster has is the failure this replaces. Output is buffered and
# printed only once every node has resolved, so a caller reading
# line by line cannot act on the first few nodes of a list which then
# failed.

set -u
set -o pipefail

fail() {
    echo "ci-cluster-nodes.sh: $*" >&2
    exit 1
}

[ -n "${nodes:-}" ] || \
    fail "\${nodes} is unset or empty. ci-environment.sh must be sourced" \
         "before this runs."

saw_primary=0
out=''
for node in ${nodes}; do
    # ci-environment.sh replaces '-' with '_' in node names so each one
    # is a shell variable name; anything else cannot be dereferenced.
    if ! [[ "${node}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        fail "\${nodes} names '${node}', which is not a shell variable" \
             "name, so its address cannot be looked up"
    fi
    if [ "${node}" = 'primary' ]; then
        saw_primary=1
        continue
    fi

    address="${!node:-}"
    [ -n "${address}" ] || \
        fail "\${nodes} names ${node} but \${${node}} (its address) is" \
             "unset or empty"
    uuid_var="${node}_uuid"
    uuid="${!uuid_var:-}"
    [ -n "${uuid}" ] || \
        fail "\${nodes} names ${node} but \${${uuid_var}} (its under-cloud" \
             "instance) is unset or empty"

    out="${out}${node} ${uuid} ${address}"$'\n'
done

# Without a node called primary this script cannot tell which node to
# leave out, and guessing would hand a caller the primary.
[ "${saw_primary}" -eq 1 ] || \
    fail "\${nodes} (${nodes}) has no node called primary, so this" \
         "script cannot tell which node to leave out. The topology in" \
         "shakenfist/actions has changed shape."
[ -n "${out}" ] || \
    fail "\${nodes} (${nodes}) has no node other than the primary"

printf '%s' "${out}"
