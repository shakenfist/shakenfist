#!/bin/bash

# Simple node lifecycle tests. These need to exist outside of the normal CI
# tests because they disturb the underlying fabric of the cloud and would
# cause spurious failures in other tests.

function log {
    echo -e "$(date) $1"
    }

# Install dependencies
sudo apt-get install -y jq

# Active the venv which has the client
. /srv/shakenfist/venv/bin/activate

# Log nodes
echo
sf-client node list
echo

# Sleep for a little to let nodes start
echo
log "=== Watch nodes boot ==="
started=$(date +%s)
finished=0
while [ ${finished} -lt 1 ]; do
    log "Status check..."
    potential=0
    for status in $(sf-client --json node list | jq --raw-output '.[] | select(.state != "created") | "\(.name)!\(.state)"'); do
        name=$(echo ${status} | cut -f 1 -d "!")
        state=$(echo ${status} | cut -f 2 -d "!")
        log "Node ${name} is in state ${state}"

        if [ "${state}" == "error" ]; then
            log "... error state, aborting"
            finished=1
        else
            potential=$(( $potential + 1 ))
        fi
    done

    if [ ${potential} -lt 1 ]; then
        log "No more nodes!"
        finished=1
    fi

    # This timeout is way too long, and often doesn't need to be this
    # generous, but then again sometimes it does. Its yet another thing I
    # should clamp down one day when CI is a bit more under control.
    now=$(date +%s)
    elapsed=$(( ${now} - ${started} ))
    log "Time elapsed: ${elapsed} seconds"
    if [ ${elapsed} -gt 1200 ]; then
        log "Timed out!"
        finished=1
    fi

    if [ ${finished} -lt 1 ]; then
        sleep 30
    fi
done

# Determine hypervisor nodes
hypervisors=$(sf-client --json node list | jq --raw-output ".[] | select(.is_hypervisor) | .name")

# Remove any instances or networks we might have from previous runs (this should
# only happen during manual testing)
echo
for uuid in $(sf-client --json instance list | jq --raw-output ".instances | .[] | .uuid"); do
    log "Removing stray instance ${uuid}"
    sf-client instance delete ${uuid}
done

for uuid in $(sf-client --json network list | jq --raw-output ".[] | .uuid"); do
    log "Removing stray network ${uuid}"
    sf-client network delete ${uuid}
done

# Launch two instances on each hypervisor, each on its own network
echo
for hypervisor in ${hypervisors}; do
    for i in $(seq 2); do
        sf-client network create ${hypervisor}-${i} 10.0.0.0/24 > /dev/null
	    log "Created network ${hypervisor}-${i}"

        sf-client instance create ${hypervisor}-${i} 1 1024 \
            -d 20@sf://upload/system/debian-12 -f ${hypervisor}-${i} \
            -p ${hypervisor}
	    log "Created instance ${hypervisor}-${i}"
    done
done

# Sleep a tiny bit
echo
sleep 30

# Fail fast if the create commands didn't actually produce the expected
# number of instances (e.g. a missing artifact reference, or a partial
# failure where only some of the creates succeeded). Without this, a
# silent create failure only surfaces later as "No instances in created
# state". We expect two instances per hypervisor.
hypervisor_count=$(echo ${hypervisors} | wc -w)
expected_count=$((hypervisor_count * 2))
created_count=$(sf-client --json instance list | jq '.instances | length')
if [ "${created_count}" -lt "${expected_count}" ]; then
    log "Expected ${expected_count} instances but only ${created_count} were created -- aborting"
    sf-client instance list
    exit 1
fi
log "Created ${created_count} instances"

# List artifacts
echo
log "=== Listing artifacts ==="
sf-client artifact list

# List blobs
echo
log "=== Listing blobs ==="
sf-client blob list

# Sleep for a little to let instances start
echo
log "=== Watch instances boot ==="
started=$(date +%s)
finished=0
while [ ${finished} -lt 1 ]; do
    log "Status check..."
    potential=0
    for status in $(sf-client --json instance list | jq --raw-output '.instances | .[] | select(.state != "created") | "\(.uuid)!\(.name)!\(.state)"'); do
        uuid=$(echo ${status} | cut -f 1 -d "!")
        name=$(echo ${status} | cut -f 2 -d "!")
        state=$(echo ${status} | cut -f 3 -d "!")
        log "Instance ${name} (${uuid}) is in state ${state}"

        if [ "${state}" == "error" ]; then
            log "... error state, aborting"
            finished=1
        else
            potential=$(( $potential + 1 ))
        fi
    done

    if [ ${potential} -lt 1 ]; then
        log "No more instances!"
        finished=1
    fi

    # This timeout is way too long, and often doesn't need to be this
    # generous, but then again sometimes it does. Its yet another thing I
    # should clamp down one day when CI is a bit more under control.
    now=$(date +%s)
    elapsed=$(( ${now} - ${started} ))
    log "Time elapsed: ${elapsed} seconds"
    if [ ${elapsed} -gt 1200 ]; then
        log "Timed out!"
        finished=1
    fi

    if [ ${finished} -lt 1 ]; then
        sleep 30
    fi
done

# List blobs again
echo
log "=== Listing blobs ==="
sf-client blob list

# Ensure we made instances and they started ok
echo
log "=== Boot checks ==="
echo
sf-client instance list
echo
if [ $(sf-client instance list | grep -c created) -lt 1 ]; then
    log "No instances in created state"
    exit 1
fi

# Ensure all instances are now created
echo
failed=$(sf-client --json instance list | jq --raw-output '.instances | .[] | select(.state != "created") | "\(.uuid), \(.name), \(.state)"')
if [ "$failed" != "" ]; then
    log "Some instances failed to start"
    exit 1
fi
log "Instances are in correct state"

# Determine which node is the current cluster maintenance node
echo
log "=== Terminate cluster maintenance node, stop another node ==="

# Two nodes are off limits as victims:
#
# The script's own host -- the script dies when its host halts,
# leaving the CI runner's outer SSH waiting on a connection that will
# never close (no keepalive) until the 60-minute job budget burns out.
#
# The network node -- the cluster-wide networknode-* work queues are
# drained only by the net-worker on the node with the network role
# (see the single-worker safety invariant in
# shakenfist/daemons/network/workitem.py), and there is no failover
# for that role. Stopping and deleting the network node strands every
# subsequently enqueued remove_dhcp_lease cleanup operation, and the
# resulting OperationTimeout tracebacks from sf-cluster and sf-net
# fail the forbidden-strings log check.
#
# Use SHAKENFIST_NODE_NAME -- the SF-level node name registered via
# `--node-name` -- rather than `hostname`: the kernel hostname is
# whatever cloud-init wrote at provision time and does not have to
# match. The two diverged in the smoke CI environment, the guard
# silently never fired, and the script halted its own host.
#
# SHAKENFIST_NODE_NAME lives in /etc/sf/config (the systemd
# EnvironmentFile read by the SF daemons). The workflow sources
# /etc/sf/sfrc before invoking this script, but sfrc only exports
# client-auth variables; the node name is not in it. Source the
# config file directly here so the lookup is self-contained.
if [ ! -r /etc/sf/config ]; then
    log "/etc/sf/config not readable -- cannot determine SF node name. Aborting."
    exit 1
fi
. /etc/sf/config
script_host="${SHAKENFIST_NODE_NAME}"
if [ -z "${script_host}" ]; then
    log "SHAKENFIST_NODE_NAME not set in /etc/sf/config. Aborting."
    exit 1
fi

network_node=$(sf-client --json node list | jq --raw-output \
    '.[] | select(.is_network_node == true) | .name' | head -1)
log "Script host is ${script_host}; network node is ${network_node}"

# The maintainer is hard-halted below, so it must not be the script
# host or the network node. Election is first-acquire-wins on a
# cluster lock so the same node holds the role for the lifetime of a
# fresh cluster, but a `sudo systemctl restart sf-cluster` on the
# holder releases the lock and lets another candidate acquire it. The
# lease is 60 s, so we sleep 90 s -- comfortably past expiry plus a
# couple of refresh cycles -- and then re-read the role. sf-cluster
# runs on every node, so an ineligible node can win again; retry a
# few times before giving up.
maintainer=$(sf-client --json node list | jq --raw-output '.[] | select(.is_cluster_maintainer) | .name')
attempts=0
while [[ "${maintainer}" == "${script_host}" || "${maintainer}" == "${network_node}" ]]; do
    attempts=$((attempts + 1))
    if [ ${attempts} -gt 5 ]; then
        log "No eligible maintainer after ${attempts} re-elections; aborting"
        exit 1
    fi
    log "Maintainer ${maintainer} is the script host or network node; forcing re-election"
    if [ "${maintainer}" == "${script_host}" ]; then
        sudo systemctl restart sf-cluster
    else
        sudo ssh -o StrictHostKeyChecking=no debian@${maintainer} \
            "sudo systemctl restart sf-cluster"
    fi
    log "Pausing for cluster lock to expire and be re-acquired..."
    sleep 90
    maintainer=$(sf-client --json node list | jq --raw-output '.[] | select(.is_cluster_maintainer) | .name')
    log "New maintainer: ${maintainer}"
done

# The graceful-stop victim must equally not be the maintainer (it is
# already being halted), the script host, or the network node.
other_victim=$(sf-client --json node list | jq --raw-output \
    --arg script_host "${script_host}" --arg network_node "${network_node}" \
    '.[] | select(.is_cluster_maintainer != true and .is_network_node != true and .name != $script_host and .name != $network_node) | .name' | head -1)
if [ -z "${other_victim}" ]; then
    log "No eligible node to gracefully stop. Aborting."
    exit 1
fi

log "Will hard stop the cluster maintainer, ${maintainer}"

# Capture node UUIDs while sf-client can still resolve the names; we need
# these later to query the work_queue table, which is keyed on node UUID.
maintainer_uuid=$(sf-client --json node show ${maintainer} | jq --raw-output ".uuid")
other_victim_uuid=$(sf-client --json node show ${other_victim} | jq --raw-output ".uuid")

# Terminate the node uncleanly for ${maintainer}. Use `poweroff`, not
# `halt`: a halted SMP guest is still a running QEMU domain and can
# spontaneously reset and reboot (observed in merge run 27324192652,
# where the halted maintainer came back 63 seconds after
# `halt --force --force` -- the under-cloud reported the domain
# "running" throughout, so the reset was guest-internal -- and the
# resurrected node re-registered and failed the missing-state check).
# `poweroff --force --force` exits the domain entirely, so the node
# deterministically stays down; the workflow's post-test revival step
# powers it back on for log collection.
#
# Wrap in `timeout` so a stuck SSH cannot consume the entire
# 60-minute step budget; observed in CI when the keepalive failed to
# fire after the remote went down. A clean disconnect (rc 255) or the
# timeout itself (rc 124) are both fine -- the point is that the node
# is going away.
timeout 300 sudo ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=1 \
    -o ServerAliveCountMax=1 debian@${maintainer} "sudo poweroff --force --force"
rc=$?
if [ ${rc} -ne 0 ]; then
    log "Poweroff SSH exited ${rc} (124=timeout, 255=connection lost are expected)"
fi
echo

# Stop SF on ${other_victim}
log "Will gracefully stop another node, ${other_victim}"
sudo ssh -o StrictHostKeyChecking=no debian@${other_victim} \
    "sudo systemctl stop sf.target"
echo

# Wait for SF to actually stop
echo "Copy a helper script to ${other_victim}"
cat - > /tmp/other-target-script << EOF
echo "Target:"
systemctl list-units -all sf.target
echo
echo "Services:"
systemctl list-units -all | grep sf | grep -v sf-agent | grep service || true
EOF
chmod ugo+rx /tmp/other-target-script
sudo scp -o StrictHostKeyChecking=no /tmp/other-target-script \
    debian@${other_victim}:/tmp/other-target-script
echo

log "=== Wait for ${other_victim} to stop ==="
started=$(date +%s)
finished=0
while [ ${finished} -lt 1 ]; do
    log "Status check..."
    sudo ssh -o StrictHostKeyChecking=no debian@${other_victim} \
        sudo /tmp/other-target-script | tee /tmp/other-target-script.out
    active=$( egrep -c "( active|deactivating)" /tmp/other-target-script.out || true )
    failed=$( grep -c "failed" /tmp/other-target-script.out || true )

    if [ ${failed} -gt 0 ]; then
        log "A unit failed to gracefully stop!"
        exit 1
    fi

    if [ ${active} -lt 1 ]; then
        log "No more running services!"
        finished=1
    fi

    now=$(date +%s)
    elapsed=$(( ${now} - ${started} ))
    log "Time elapsed: ${elapsed} seconds"
    if [ ${elapsed} -gt 300 ]; then
        log "Timed out!"
        finished=1
    fi

    if [ ${finished} -lt 1 ]; then
        sleep 30
    fi
done

# Ensure SF really stopped on ${other_victim}
running_count=$(sudo ssh -o StrictHostKeyChecking=no debian@${other_victim} \
    "sudo ps -ef | grep sf | egrep -v '(ata_sff|kvm|agent|grep)'" | wc -l)
if [ ${running_count} -gt 0 ]; then
    log "SF failed to stop on ${other_victim}, there are ${running_count} processes still running."
    log ""
    sudo ssh -o StrictHostKeyChecking=no debian@${other_victim} \
        "sudo ps -ef | grep sf | egrep -v '(ata_sff|kvm|agent|grep)'"
    exit 1
fi

# Ensure another node is now the maintenance node. The cluster
# maintenance lock has a 60s lease, so we wait 90s here -- comfortably
# past the lease expiry plus a couple of refresh cycles -- before
# expecting a candidate to have stolen the dead maintainer's lock.
echo
log "=== Cluster maintenance failover check ==="
log "Pausing for maintenance node failover..."
sleep 90
new_maintainer=$(sf-client --json node list | jq --raw-output '.[] | select(.is_cluster_maintainer) | .name')
if [ "${maintainer}" == "${new_maintainer}" ]; then
    log "SF failed to select a new maintenance node"
    exit 1
fi

# Wait a bit
log "Pausing so nodes can be noticed as gone..."
sleep 480

echo
log "=== Node state checks ==="
echo
sf-client node list
echo

# Ensure ${maintainer} is missing and ${other_victim} is stopped
echo
log "Check node state"
if [ $(sf-client --json node show ${maintainer} | jq --raw-output ".state") != "missing" ]; then
    echo "${maintainer} not in missing state"
    exit 1
fi
if [ $(sf-client --json node show ${other_victim} | jq --raw-output ".state") != "stopped" ]; then
    echo "${other_victim} not in stopped state"
    exit 1
fi
log "Nodes are in expected state"

# Delete node
echo
log "=== Delete nodes ==="
log "Deleting ${maintainer}"
sf-client node delete ${maintainer}
log "Deleting ${other_victim}"
sf-client node delete ${other_victim}

# Wait a bit
log "Pausing so node can be noticed as deleted..."
sleep 420

# Ensure ${maintainer} and ${other_victim} are now deleted
echo
log "=== Node state checks ==="
echo
sf-client node list
echo

log "Check node state"
if [ $(sf-client --json node show ${maintainer} | jq --raw-output ".state") != "deleted" ]; then
    echo "${maintainer} not in deleted state"
    exit 1
fi
if [ $(sf-client --json node show ${other_victim} | jq --raw-output ".state") != "deleted" ]; then
    echo "${other_victim} not in deleted state"
    exit 1
fi
log "Nodes are in expected state"

# Ensure the instances on ${maintainer} and ${other_victim} are now absent
echo
log "=== Instance state checks checks ==="
echo
sf-client instance list
echo

log "Ensure there are no instances from ${maintainer} present any more"
if [ $(sf-client instance list | grep -c ${maintainer}) -gt 0 ]; then
    log "Instances remain from ${maintainer}"
    exit 1
fi
log "Ensure there are no instances from ${other_victim} present any more"
if [ $(sf-client instance list | grep -c ${other_victim}) -gt 0 ]; then
    log "Instances remain from ${other_victim}"
    exit 1
fi
log "Instances in expected state"

# Ensure there are no queued jobs for ${maintainer} and ${other_victim}.
# Node-scoped queue names are '{node_uuid}-clusteroperation-{priority}' so
# we match by the UUID prefix we captured before the nodes were deleted.
# Cluster operation headers live in cluster_operations, keyed by node_uuid.
echo
log "=== Queue checks ==="
log "Ensure node queues have been cleared for ${maintainer}"
if [ $(sudo mysql -N shakenfist -e \
        "SELECT COUNT(*) FROM work_queue WHERE queue_name LIKE '${maintainer_uuid}-%'") -gt 0 ]; then
    log "Queue jobs remain for ${maintainer}"
fi
if [ $(sudo mysql -N shakenfist -e \
        "SELECT COUNT(*) FROM cluster_operations WHERE node_uuid='${maintainer_uuid}'") -gt 0 ]; then
    log "Cluster operations remain for ${maintainer}"
fi
log "Ensure node queues have been cleared for ${other_victim}"
if [ $(sudo mysql -N shakenfist -e \
        "SELECT COUNT(*) FROM work_queue WHERE queue_name LIKE '${other_victim_uuid}-%'") -gt 0 ]; then
    log "Queue jobs remain for ${other_victim}"
fi
if [ $(sudo mysql -N shakenfist -e \
        "SELECT COUNT(*) FROM cluster_operations WHERE node_uuid='${other_victim_uuid}'") -gt 0 ]; then
    log "Cluster operations remain for ${other_victim}"
fi
log "Queue jobs in expected state"

# Done
echo
log "=== Test complete ==="