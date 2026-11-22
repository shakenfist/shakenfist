#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# lab-common.sh -- settings and helpers shared by the identity provider
# lab's scripts. Sourced, never run.
#
# The names below are not decoration: the captured provider documents
# under shakenfist/tests/files/idp/ carry the issuer URLs these
# settings produce, and the unit tests which read those documents
# compare the issuers character for character. Changing the realm name,
# the application slug or either port means the lab can no longer
# regenerate what is committed.
#
# Credentials are never written into this repository. lab-up.sh
# generates them into ${IDP_LAB_STATE_DIR}/lab.env, which lives outside
# the working tree, and every script reads them from there. An exported
# IDP_LAB_* variable wins over the generated one, so a lab which has to
# have a known password can be given one without editing a file here.

# Where the lab's generated state lives: credentials, certificates and
# the cookie jars the login flows need. Deliberately outside the
# repository, and deliberately not a hidden directory inside it.
IDP_LAB_STATE_DIR="${IDP_LAB_STATE_DIR:-${TMPDIR:-/tmp}/sf-idp-lab}"
IDP_LAB_ENV_FILE="${IDP_LAB_STATE_DIR}/lab.env"
IDP_LAB_TLS_DIR="${IDP_LAB_TLS_DIR:-${IDP_LAB_STATE_DIR}/tls}"

IDP_LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IDP_LAB_REPO_ROOT="$(cd "${IDP_LAB_DIR}/../.." && pwd)"

# Keycloak. The realm is the last element of its issuer URL.
KEYCLOAK_URL='https://localhost:8443'
KEYCLOAK_REALM="${IDP_LAB_KEYCLOAK_REALM:-shakenfist}"
KEYCLOAK_ISSUER="${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}"

# Authentik. The slug of the application, not the name of the
# provider, is the last element of its issuer URL -- and the issuer
# carries a trailing slash, which is part of it.
AUTHENTIK_URL='https://localhost:9443'
AUTHENTIK_SLUG="${IDP_LAB_AUTHENTIK_SLUG:-sf}"
AUTHENTIK_ISSUER="${AUTHENTIK_URL}/application/o/${AUTHENTIK_SLUG}/"

# The same client id, group and person at both providers, so that the
# only differences between the two captures are the providers'.
LAB_CLIENT_ID="${IDP_LAB_CLIENT_ID:-shakenfist}"
LAB_GROUP="${IDP_LAB_GROUP:-sf-admins}"
LAB_PERSON="${IDP_LAB_PERSON:-person}"
LAB_PERSON_EMAIL="${IDP_LAB_PERSON_EMAIL:-person@example.com}"
LAB_PERSON_NAME="${IDP_LAB_PERSON_NAME:-Example Person}"

# The scopes a login asks for. Both providers need "profile" or the
# group claim is absent, and neither accepts a "groups" scope for this
# configuration.
LAB_SCOPES="${IDP_LAB_SCOPES:-openid profile}"

# The scopes the captures are taken with, which is not the same thing.
# "openid profile" is the minimum a mapping rule needs, and is what an
# issuer's oidc_scopes should be; the email scope is added here
# because the committed captures carry email and email_verified, and a
# capture missing two claims is a capture which no longer says what
# the providers emit. The difference is itself a provider difference:
# Keycloak grants its email scope whether or not it was asked for,
# while Authentik grants exactly what the request named.
LAB_CAPTURE_SCOPES="${IDP_LAB_CAPTURE_SCOPES:-openid profile email}"

# curl against a self signed certificate, which is what the lab has.
# Everything this reaches is on loopback and was started by the caller.
CURL=(curl --silent --show-error --insecure --max-time 30)

# Progress goes to stderr, not stdout. Several of the helpers below
# print a document or a primary key on stdout for a caller to read, and
# a progress line mixed into that is a JSON parse failure somewhere
# else.
lab_log() {
    echo "[idp-lab] $*" >&2
}

lab_fail() {
    echo "[idp-lab] $*" >&2
    exit 1
}

# docker compose, pointed at this lab's file and credentials.
lab_compose() {
    docker compose --file "${IDP_LAB_DIR}/docker-compose.yml" \
        --env-file "${IDP_LAB_ENV_FILE}" "$@"
}

# Read the generated credentials. Anything already exported stays as it
# is, which is what lets a caller override one value without owning the
# whole file.
lab_load_env() {
    [ -r "${IDP_LAB_ENV_FILE}" ] || \
        lab_fail "No lab environment at ${IDP_LAB_ENV_FILE}. Run tools/idp-lab/lab-up.sh first."

    local line name
    while IFS= read -r line; do
        case "${line}" in
            ''|'#'*) continue ;;
        esac
        name="${line%%=*}"
        if [ -z "${!name:-}" ]; then
            export "${line?}"
        fi
    done < "${IDP_LAB_ENV_FILE}"
}

# Wait until a URL answers, rather than sleeping for a guessed time.
# Authentik's first boot runs database migrations and takes minutes on
# a cold host; a fixed sleep is either wrong or slow.
#
#   lab_wait_for_url <description> <url> [seconds]
lab_wait_for_url() {
    local description="$1" url="$2" deadline="${3:-300}"
    local waited=0

    lab_log "Waiting up to ${deadline}s for ${description}."
    # Not "${CURL[@]}": a provider which is still starting answers 503
    # or nothing at all, every five seconds, and --show-error would
    # report each of those as though it were a problem.
    while ! curl --silent --insecure --fail --max-time 10 \
            --output /dev/null "${url}"; do
        waited=$((waited + 5))
        if [ "${waited}" -ge "${deadline}" ]; then
            lab_fail "${description} did not answer ${url} within ${deadline}s."
        fi
        sleep 5
    done
    lab_log "${description} is up after about ${waited}s."
}

# Pretty print JSON on stdin, preserving the order the server sent its
# members in, which is how the committed captures are formatted.
lab_format_json() {
    python3 -I -c '
import json
import sys

json.dump(json.load(sys.stdin), sys.stdout, indent=2)
sys.stdout.write("\n")
'
}

# One member of a JSON object on stdin, with no jq dependency: this
# repository already requires python3 everywhere and does not require
# jq anywhere.
#
#   lab_json_member <member> [default]
lab_json_member() {
    python3 -I -c '
import json
import sys

document = json.load(sys.stdin)
value = document.get(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
if isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print("" if value is None else value)
' "$@"
}
