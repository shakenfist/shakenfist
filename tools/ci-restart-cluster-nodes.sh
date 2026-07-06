#!/bin/bash

# Restart every under-cloud test instance after the node lifecycle test,
# and wait for ssh to return on each. The lifecycle test deliberately
# powers off or kills nodes, so all five are power cycled here before CI
# log gathering fans out over them.
#
# We have twice observed a node fail to return from `reboot --hard` when
# all five instances cycle at once (no route to host for five minutes,
# i.e. the VM never booted). When that happens, dump some diagnostics
# and try a full power cycle (poweroff, pause, poweron) before failing.
#
# Requires the under-cloud sf-client credentials and the sf{N}_uuid
# variables from ci-environment.sh, which the caller must have sourced.
#
# Arguments (positional):
#   $1  ssh user for the test nodes (default: debian)

set -x
SSH_USER="${1:-debian}"

addresses=(10.0.0.20 10.0.0.21 10.0.0.22 10.0.0.23 10.0.0.24)
uuids=("${sf1_uuid}" "${sf2_uuid}" "${sf3_uuid}" "${sf4_uuid}" "${sf5_uuid}")

# The reboot has to be issued against the under-cloud's API. Don't let
# one failed reboot abort the rest of the loop -- we want all five
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

    if wait_for_ssh "${address}" 60; then
        echo "Node ${address} is back."
        continue
    fi

    echo "Node ${address} did not return within 300 seconds, power cycling..."
    sf-client instance show "${uuid}" || true
    sf-client instance poweroff "${uuid}" || true
    sleep 10
    sf-client instance poweron "${uuid}" || true

    if wait_for_ssh "${address}" 60; then
        echo "Node ${address} is back after a power cycle."
        continue
    fi

    echo "Node ${address} did not return after a power cycle either."
    sf-client instance show "${uuid}" || true
    failed=1
done

exit "${failed}"
