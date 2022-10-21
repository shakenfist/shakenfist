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
sf-client node list

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

# Ensure we made any instances
if [ $(sf-client instance list | grep -c created) -lt 1 ]; then
    log "No instances in created state..."
    echo
    sf-client instance list
    exit 1
fi

# Ensure all instances are now created
echo
sf-client instance list
echo
failed=$(sf-client --json instance list | jq --raw-output '.instances | .[] | select(.state != "created") | "\(.uuid), \(.name), \(.state)"')

# Terminate the node uncleanly for sf-2, with extra flags so we don't hang
sudo ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=1 \
    -o ServerAliveCountMax=1 debian@sf-2 "sudo halt --force --force"

# Stop SF on sf-3
sudo ssh -o StrictHostKeyChecking=no debian@sf-3 "sudo systemctl stop sf"

# Ensure SF really stopped on sf-3
running=$(sudo ssh -o StrictHostKeyChecking=no debian@sf-3 "sudo ps -ef | grep sf | egrep -v '(ata_sff|kvm|agent|grep)'" | wc -l)
if [ $running -gt 0 ]; then
    log "SF failed to stop on sf-3"
    exit 1
fi

# Wait a bit
log "Pausing so node can be noticed as gone..."
sleep 420

echo
sf-client node list
echo

# Delete node
echo
log "Deleting sf-2"
sf-client node delete sf-2
log "Deleting sf-3"
sf-client node delete sf-3

# Wait a bit
log "Pausing so node can be noticed as deleted..."
sleep 420

# Ensure the instances on sf-2 and sf-3 are now absent
echo
sf-client instance list

echo
log "Ensure there are no instances from sf-2 present any more"
if [ $(sf-client instance list | grep -c sf-2) -gt 0 ]; then
    log "Instances remain from sf-2"
    exit 1
fi
log "Ensure there are no instances from sf-3 present any more"
if [ $(sf-client instance list | grep -c sf-3) -gt 0 ]; then
    log "Instances remain from sf-3"
    exit 1
fi

# Ensure there are no queued jobs for sf-2 and sf-3
echo
log "Ensure there node queues have been cleared for sf-2"
if [ $(etcdctl get --prefix /sf/queue/sf-2 | wc -l) -gt 0 ]; then
    log "Queue jobs remain for sf-2"
fi
log "Ensure there node queues have been cleared for sf-2"
if [ $(etcdctl get --prefix /sf/queue/sf-2 | wc -l) -gt 0 ]; then
    log "Queue jobs remain for sf-2"
fi

# Done
echo
log "Test complete"