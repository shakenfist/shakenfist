#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# lab-down.sh -- stop the identity provider lab and remove everything
# it created: the four containers, the database volume and the network.
#
# The volume goes with it deliberately. A lab database which survives
# is a lab whose next run starts from a configuration nobody can see,
# and these providers are quick to reconfigure from the two configure
# scripts. Nothing here is worth keeping.
#
#   tools/idp-lab/lab-down.sh [--purge]
#
# --purge also removes the state directory, which is where the
# generated credentials and the lab's certificate live. Without it
# they are kept, so a lab brought up again has the same administrator
# password and the same certificate.

set -euo pipefail

# shellcheck source=tools/idp-lab/lab-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"

PURGE='no'
if [ "${1:-}" = '--purge' ]; then
    PURGE='yes'
    shift
fi
[ $# -eq 0 ] || lab_fail "Unrecognised argument: $1"

# docker compose needs the environment file to parse its own file, so a
# lab whose credentials have already been deleted is torn down by name
# instead. Either way nothing is left behind.
if [ -r "${IDP_LAB_ENV_FILE}" ]; then
    lab_compose down --volumes --remove-orphans
else
    lab_log 'No environment file; removing the lab by project name.'
    docker compose --project-name sf-idp-lab down --volumes --remove-orphans
fi

if [ "${PURGE}" = 'yes' ]; then
    lab_log "Removing ${IDP_LAB_STATE_DIR}."
    rm -rf "${IDP_LAB_STATE_DIR}"
fi

# Say what is left, rather than claiming nothing is: a container which
# survived a teardown is worth seeing here rather than discovering a
# week later.
remaining=$(docker ps --all --filter 'name=sf-idp-lab' --format '{{.Names}}' | tr '\n' ' ')
if [ -n "${remaining}" ]; then
    lab_fail "These containers are still here: ${remaining}"
fi
lab_log 'The lab is down and nothing of it is running.'
