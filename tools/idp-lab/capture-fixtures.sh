#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# capture-fixtures.sh -- regenerate the captured provider documents
# under shakenfist/tests/files/idp/ from the running lab.
#
# Four files, two per provider: the discovery document the provider
# serves, and the decoded claim set of the identity token a real device
# login produces. The unit tests which read them assert that the
# discovery code accepts each document and that a group-bound mapping
# rule matches each claim set, so they are the committed evidence for
# what these two providers actually emit.
#
# A regenerated capture is never byte-identical to the committed one.
# Signing keys, timestamps, token identifiers, session identifiers and
# the subject are per-login or per-container. What should be identical
# is the set of endpoint names in each discovery document, the issuer
# strings, the set of claim names, and the shape and value of the group
# claim; a difference in any of those is a change in the providers or
# in the lab's configuration, and is worth reading rather than
# committing.
#
# Everything it needs is on loopback and started by lab-up.sh, and
# both providers must have been configured by the two configure
# scripts first.
#
#   tools/idp-lab/capture-fixtures.sh [--output-dir DIR] [--provider NAME]
#
# With no arguments it overwrites the committed captures in place, so
# that the change is a diff in the working tree rather than a file
# somewhere else to compare by hand. --output-dir writes them
# elsewhere.

set -euo pipefail

# shellcheck source=tools/idp-lab/lab-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"

lab_load_env

OUTPUT_DIR="${IDP_LAB_REPO_ROOT}/shakenfist/tests/files/idp"
PROVIDERS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --output-dir)
            [ -n "${2:-}" ] || lab_fail '--output-dir needs a directory.'
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --provider)
            [ -n "${2:-}" ] || lab_fail '--provider needs keycloak or authentik.'
            PROVIDERS+=(--provider "$2")
            shift 2
            ;;
        *)
            # Refuse rather than ignore: a silently discarded argument
            # would leave the caller believing they had written their
            # captures somewhere they have not.
            lab_fail "Unrecognised argument: $1"
            ;;
    esac
done

[ -d "${OUTPUT_DIR}" ] || lab_fail "${OUTPUT_DIR} is not a directory."

# The capture tool reads its settings from the environment rather than
# from a long argument list, because the person's password is one of
# them and a command line is world readable.
export IDP_LAB_CAPTURE_KEYCLOAK_URL="${KEYCLOAK_URL}"
export IDP_LAB_CAPTURE_KEYCLOAK_ISSUER="${KEYCLOAK_ISSUER}"
export IDP_LAB_CAPTURE_AUTHENTIK_URL="${AUTHENTIK_URL}"
export IDP_LAB_CAPTURE_AUTHENTIK_ISSUER="${AUTHENTIK_ISSUER}"
export IDP_LAB_CAPTURE_CLIENT_ID="${LAB_CLIENT_ID}"
export IDP_LAB_CAPTURE_SCOPES="${LAB_CAPTURE_SCOPES}"
export IDP_LAB_CAPTURE_PERSON="${LAB_PERSON}"
export IDP_LAB_CAPTURE_PERSON_PASSWORD="${IDP_LAB_PERSON_PASSWORD}"
export IDP_LAB_CAPTURE_PERSON_EMAIL="${LAB_PERSON_EMAIL}"
export IDP_LAB_CAPTURE_PERSON_NAME="${LAB_PERSON_NAME}"

lab_log "Capturing into ${OUTPUT_DIR}."
exec python3 "${IDP_LAB_DIR}/capture-fixtures.py" \
    --output-dir "${OUTPUT_DIR}" "${PROVIDERS[@]+"${PROVIDERS[@]}"}"
