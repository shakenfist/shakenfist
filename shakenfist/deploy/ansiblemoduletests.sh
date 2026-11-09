#!/bin/bash -e

# Simple node ansible module tests.

function log {
    echo -e "$(date) $1"
    }

# Install dependencies
sudo apt-get install -y pwgen

# Where the scenarios boot debian:11 from. The bytes are already on the
# /srv/ci cache disk attached to the cluster -- shakenfist/actions caches them
# there on every dependencies build -- but nothing routed the cluster at them,
# so the first create to touch debian:11 fetched ~407 MiB from
# images.shakenfist.com inside its await budget. That fetch was measured at
# 578 seconds against a 600 second default, which is no headroom at all
# (issues 4000 and 4461). Import the cached copy as a shared artifact instead
# and override the scenarios' debian_11_base with it.
#
# Shared because scenario 004 boots it from a namespace of its own while 005
# and 007 boot it as system, and an unshared upload in the system namespace is
# invisible to the former. The import is deliberately fail-open: if the cache
# disk is not mounted, or sf-client cannot upload, the scenarios keep their own
# default of debian:11 and the internet fetch, which is what they did before.
cached_debian_11="/srv/ci/debian:11"
debian_11_base="debian:11"

if [ -r "${cached_debian_11}" ]; then
    log "Importing ${cached_debian_11} into the cluster"
    if sf-client artifact upload debian-11 "${cached_debian_11}" \
            --shared --no-checksum; then
        debian_11_base="sf://upload/system/debian-11"
    else
        log "Import of ${cached_debian_11} failed"
    fi
else
    log "${cached_debian_11} is not readable"
fi

log "Scenarios will boot debian:11 from ${debian_11_base}"

cd /home/debian/shakenfist/shakenfist/deploy/ansible_module_ci
for scenario in *.yml; do
    echo
    echo
    log "=== Scenario ${scenario} ==="
    echo
    ansible-playbook -e "debian_11_base=${debian_11_base}" ${scenario}
    if [ $? -gt 0 ]; then
       echo "TESTS FAILED."
       exit 1
    fi
    echo
done

# Done
echo
log "Test complete"