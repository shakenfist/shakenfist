#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-cluster-nodes.sh -- print the test cluster's nodes, one per line,
# as "<name> <under-cloud instance uuid> <address>".
#
# Two CI steps need to reach the cluster's nodes over ssh: the node
# lifecycle lane's cached-image upload, which wants a hypervisor rather
# than the primary, and tools/ci-restart-cluster-nodes.sh, which power
# cycles every node afterwards. Both used to carry the same hand-written
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
# So ask the cluster. The sf<N>_uuid variables ci-environment.sh exports
# are the node set -- the same variables both callers already depend on,
# so this adds no new coupling -- and the under-cloud knows each of
# those instances' address, which removes the sf<N> to 10.0.0.(19+N)
# convention that was the actual link to the other repository. A node
# added there appears here on the next run with no edit, and a node
# removed or renumbered fails loudly, naming the topology.
#
# Usage:
#
#   tools/ci-cluster-nodes.sh
#
# Requires the sf<N>_uuid variables and the under-cloud sf-client
# credentials from ci-environment.sh, which the caller must have
# sourced. Exits non-zero, with the reason on stderr, rather than
# printing a short or guessed list: a caller which silently got fewer
# nodes than the cluster has is the failure this replaces.

set -u
set -o pipefail

fail() {
    echo "ci-cluster-nodes.sh: $*" >&2
    exit 1
}

# compgen -v over the environment rather than a loop to a fixed count,
# which is the assumption this script exists to remove. sort -V so sf10
# sorts after sf9 rather than after sf1.
names=$(compgen -v | sed -n 's/^\(sf[0-9][0-9]*\)_uuid$/\1/p' | sort -V -u)
if [ -z "${names}" ]; then
    fail "no sf<N>_uuid variables in the environment. ci-environment.sh" \
         "must be sourced before this runs, and the cluster build must" \
         "have exported a uuid per node."
fi

for name in ${names}; do
    var="${name}_uuid"
    uuid="${!var}"
    [ -n "${uuid}" ] || fail "${var} is set but empty"

    show=$(sf-client --json instance show "${uuid}") || \
        fail "could not read under-cloud instance ${uuid} (${name}) --" \
             "are the under-cloud credentials in place?"

    # Every non-empty fixed address on the instance. A floating address
    # lives in its own field and is deliberately not considered: these
    # are under-cloud instances on the under-cloud's own network, which
    # is the path the runner reaches them by.
    addresses=$(echo "${show}" | jq --raw-output \
        '[.interfaces[]? | .ipv4 | select(. != null and . != "")] | .[]')
    count=$(echo "${addresses}" | grep -c . || true)

    if [ "${count}" -eq 0 ]; then
        fail "under-cloud instance ${uuid} (${name}) has no IPv4 address," \
             "so it cannot be reached over ssh"
    fi
    if [ "${count}" -ne 1 ]; then
        fail "under-cloud instance ${uuid} (${name}) has ${count} IPv4" \
             "addresses ($(echo ${addresses} | tr '\n' ' ')); this script" \
             "cannot tell which one the runner should use. The topology in" \
             "shakenfist/actions gave a node more than one network -- pick" \
             "the management one here deliberately."
    fi

    echo "${name} ${uuid} ${addresses}"
done
