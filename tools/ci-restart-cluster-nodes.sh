#!/bin/bash

# Restart every under-cloud test instance after the node lifecycle test,
# and wait for ssh to return on each. The lifecycle test deliberately
# powers off or kills nodes, so every node is power cycled here before
# CI log gathering fans out over them.
#
# We have repeatedly observed nodes fail to return from `reboot --hard`
# when every instance cycles at once. The failure signature is a VM
# whose network comes up (ssh connections are refused, not unroutable)
# but where sshd never starts -- a guest-side boot wedge between
# network-up and ssh.service. When that happens, dump diagnostics
# including the guest console, and try a full power cycle (poweroff,
# pause, poweron) before failing.
#
# Requires the under-cloud sf-client credentials and the sf{N}_uuid
# variables from ci-environment.sh, which the caller must have sourced.
#
# Arguments (positional):
#   $1  ssh user for the test nodes (default: debian)

set -x
SSH_USER="${1:-debian}"

# The nodes, their under-cloud instances and their addresses, asked of
# the cluster rather than written down here. The pair of hand-written
# arrays this replaces was indexed positionally against a hardcoded
# address list for a topology in another repository (issue #4320); now
# each node's address arrives attached to its own uuid, so the pairing
# cannot drift.
addresses=()
uuids=()
while read -r name uuid address; do
    addresses+=("${address}")
    uuids+=("${uuid}")
done < <(bash "$(dirname "$0")/ci-cluster-nodes.sh")

# This script does not run under "set -e", and a process substitution
# cannot fail the read loop, so the helper's refusal has to be checked.
# Power cycling nothing and reporting success is the quiet failure the
# helper exists to prevent.
if [ "${#uuids[@]}" -eq 0 ]; then
    echo "ci-restart-cluster-nodes.sh: ci-cluster-nodes.sh listed no" \
         "cluster nodes, so there is nothing to restart. Its error is" \
         "above." >&2
    exit 1
fi

# The reboot has to be issued against the under-cloud's API. Don't let
# one failed reboot abort the rest of the loop -- we want every node
# attempted before we start waiting. The lifecycle test powers its
# victim off entirely (the QEMU domain exits), and reboot is not valid
# for a powered-off instance, so fall back to poweron.
for uuid in "${uuids[@]}"; do
    sf-client instance reboot --hard "${uuid}" || \
        sf-client instance poweron "${uuid}" || true
done

wait_for_ssh() {
    local address="$1"
    local attempts="$2"
    local count=0

    until ssh -i /srv/github/id_ci -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 \
        "${SSH_USER}@${address}" true; do
        count=$(( count + 1 ))
        if [ "${count}" -gt "${attempts}" ]; then
            return 1
        fi
        sleep 5
    done
    return 0
}

failed=0
for i in "${!addresses[@]}"; do
    address="${addresses[$i]}"
    uuid="${uuids[$i]}"

    # 180 attempts at 5 s spacing is a 15 minute budget. A cluster's
    # instances hard-rebooting at once on a loaded under-cloud hypervisor
    # routinely take longer than the old 5 minute budget to reach sshd
    # (merge runs 28787986916, 28819782726 and 28827810722 all lost
    # otherwise-green jobs to it). The waits are sequential but the boots
    # are concurrent, so the budget is really shared across every node --
    # later nodes are usually up by the time we ask.
    if wait_for_ssh "${address}" 180; then
        echo "Node ${address} is back."
        continue
    fi

    echo "Node ${address} did not return within 900 seconds, power cycling..."
    sf-client instance show "${uuid}" || true
    echo "Guest console tail for ${uuid} before power cycle:"
    sf-client instance consoledata "${uuid}" 20480 || true
    sf-client instance poweroff "${uuid}" || true
    sleep 10
    sf-client instance poweron "${uuid}" || true

    if wait_for_ssh "${address}" 60; then
        echo "Node ${address} is back after a power cycle."
        continue
    fi

    echo "Node ${address} did not return after a power cycle either."
    sf-client instance show "${uuid}" || true
    echo "Guest console tail for ${uuid} after failed power cycle:"
    sf-client instance consoledata "${uuid}" 20480 || true
    failed=1
done

exit "${failed}"
