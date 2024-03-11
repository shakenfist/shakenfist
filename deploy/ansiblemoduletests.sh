#!/bin/bash -e

# Simple node ansible module tests.

function log {
    echo -e "$(date) $1"
    }

# Install dependencies
sudo apt-get install -y pwgen

cd /home/debian/shakenfist/deploy/ansible_module_ci
for scenario in *.yml; do
    echo
    echo
    log "=== Scenario ${scenario} ==="
    echo
    ansible-playbook ${scenario}
    echo
done

# Done
echo
log "Test complete"