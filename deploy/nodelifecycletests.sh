#!/bin/bash

# Simple node lifecycle tests. These need to exist outside of the normal CI
# tests because they disturb the underlying fabric of the cloud and would
# cause spurious failures in other tests.

function log {
    echo -e "$(date) $1"
    }

# Install dependencies
sudo apt-get install -y jq

# Log nodes
echo
sf-client node list
echo

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
            -d 20@sf://upload/system/debian-11 -f ${hypervisor}-${i} \
            -p ${hypervisor} > /dev/null
	log "Created instance ${hypervisor}-${i}"
    done
done

# Sleep for a little to let instances start
echo
log "Pausing to let instances boot"
sleep 60

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
failed=$(sf-client --json instance list | jq --raw-output '.instances | .[] | select(.state != "created") | "\(.uuid), \(.name), \(.state)"')
if [ "$failed" != "" ]; then
    log "Some instances failed to start"
    exit 1
fi
log "Instances are in correct state"

# Determine which node is the current cluster maintenance node
echo
log "=== Terminate cluster maintenance node, stop another node ==="
maintainer=$(sf-client --json node list | jq --raw-output '.[] | select(.is_cluster_maintainer) | .name')
other_victim=$(sf-client --json node list | jq --raw-output '.[] | select(.is_cluster_maintainer != true) | .name' | head -1)

echo
log "Will hard stop the cluster maintainer, ${maintainer}"
log "Will gracefully stop another node, ${other_victim}"

# Terminate the node uncleanly for ${maintainer}, with extra flags so we don't hang
sudo ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=1 \
    -o ServerAliveCountMax=1 debian@${maintainer} "sudo halt --force --force"

# Stop SF on ${other_victim}
sudo ssh -o StrictHostKeyChecking=no debian@${other_victim} "sudo systemctl stop sf"

# Ensure SF really stopped on ${other_victim}
running=$(sudo ssh -o StrictHostKeyChecking=no debian@${other_victim} "sudo ps -ef | grep sf | egrep -v '(ata_sff|kvm|agent|grep)'" | wc -l)
if [ $running -gt 0 ]; then
    log "SF failed to stop on ${other_victim}"
    exit 1
fi

# Ensure another node is now the maintenance node
echo
log "=== Cluster maintenance failover check ==="
log "Pausing for maintenance node failover..."
sleep 60
new_maintainer=$(sf-client --json node list | jq --raw-output '.[] | select(.is_cluster_maintainer) | .name')
if [ "${maintainer}" == "{new_maintainer}" ]; then
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

# Ensure there are no queued jobs for ${maintainer} and ${other_victim}
echo
log "=== Queue checks ==="
log "Ensure there node queues have been cleared for ${maintainer}"
if [ $(etcdctl get --prefix /sf/queue/${maintainer} | wc -l) -gt 0 ]; then
    log "Queue jobs remain for ${maintainer}"
fi
log "Ensure there node queues have been cleared for ${other_victim}"
if [ $(etcdctl get --prefix /sf/queue/${other_victim} | wc -l) -gt 0 ]; then
    log "Queue jobs remain for ${other_victim}"
fi
log "Queue jobs in expected state"

# Done
echo
log "=== Test complete ==="