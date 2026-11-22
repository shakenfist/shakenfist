#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# configure-authentik.sh -- configure the lab's Authentik for a human
# login, through its API, so that the walkthrough in
# docs/operator_guide/authentication.md can be re-run rather than
# remembered.
#
# The bootstrap environment this lab starts the containers with creates
# an API token as well as an administrator password, so every step
# below is a bearer-token API call from the first second and there is
# no initial setup interface to click through.
#
# What it creates, and why each piece is needed:
#
#   * the brand's web certificate, replaced with the lab's. Authentik
#     serves https from first boot with a certificate it generates
#     itself, and that certificate carries no subjectAltName -- so a
#     cluster fetching its discovery document refuses it by hostname
#     even when that same certificate is the trust anchor. Trusting a
#     certificate does not give it a name; only replacing it does;
#   * a group, whose *name* is what reaches the token: there is no
#     path and no uuid in the claim, unlike the other provider;
#   * a person in that group, with a password;
#   * an OAuth2/OpenID provider. Three of its fields have an API
#     default which does not work and are the whole reason this script
#     is longer than it looks: grant_types defaults to the empty list
#     and must name the device code grant, property_mappings defaults
#     to the empty list and must include the shipped profile mapping
#     which is what emits the group claim, and signing_key defaults to
#     null, leaving nothing to sign an RS256 identity token with. Its
#     authorization and invalidation flows are required and have no
#     defaults either;
#   * an application, whose *slug* is the last element of the issuer
#     URL -- and that issuer carries a trailing slash which is part of
#     it;
#   * a device code flow, and the brand pointed at it. This is not the
#     default, and without it a device login answers the person a bare
#     404 with an empty body after they have already typed their
#     password, with the reason only in the server's log. Nothing in
#     the discovery document distinguishes that state from a working
#     one.
#
# No mapper is needed for the group claim: the shipped profile
# property mapping already emits group names as an array. What it
# needs instead is for that scope to be granted, so a login must ask
# for at least "openid profile".
#
# The redirect URI is a regex entry rather than a strict one, because
# sf-client binds an ephemeral loopback port. The other provider's
# spelling, http://127.0.0.1:*/, is accepted here as a strict entry
# and then matches nothing.
#
# Safe to re-run: every object is checked for before it is created.
#
#   tools/idp-lab/configure-authentik.sh

set -euo pipefail

# shellcheck source=tools/idp-lab/lab-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"

lab_load_env

API="${AUTHENTIK_URL}/api/v3"
AUTH_HEADER="Authorization: Bearer ${IDP_LAB_AUTHENTIK_API_TOKEN}"

ak_get() {
    "${CURL[@]}" -H "${AUTH_HEADER}" "${API}$1"
}

# The primary key of the first object a listing returns, or the empty
# string. Authentik names that key differently per endpoint -- brands
# use brand_uuid -- so the member to read is an argument.
#
#   ak_first <path with query> [key member]
ak_first() {
    ak_get "$1" | python3 -I -c '
import json
import sys

results = json.load(sys.stdin).get("results", [])
print(results[0][sys.argv[1]] if results else "")
' "${2:-pk}"
}

# The same, but waiting for the object to exist.
#
# This is not defensive programming. Authentik's readiness endpoint
# answers while its worker is still creating the objects a stock
# install ships -- the default brand, the built-in flows, the shipped
# scope mappings and the self signed certificate -- so a configure run
# started the moment lab-up.sh finishes finds a provider which is up
# and empty, and fails on whichever object it reached first.
#
#   ak_wait_first <path with query> [key member] [deadline] [optional]
ak_wait_first() {
    local path="$1" key="${2:-pk}" deadline="${3:-240}" optional="${4:-}"
    local waited=0 pk

    while true; do
        pk=$(ak_get "${path}" | python3 -I -c '
import json
import sys

results = json.load(sys.stdin).get("results", [])
print(results[0][sys.argv[1]] if results else "")
' "${key}")
        if [ -n "${pk}" ]; then
            printf '%s' "${pk}"
            return 0
        fi

        waited=$((waited + 5))
        if [ "${waited}" -ge "${deadline}" ]; then
            [ -n "${optional}" ] && return 0
            lab_fail "Nothing at ${path} after ${deadline}s. Authentik's first boot may still be running."
        fi
        if [ "${waited}" = 5 ]; then
            lab_log "  waiting for Authentik to finish creating what it ships (${path})"
        fi
        sleep 5
    done
}

# A write whose body is on stdin. Prints the response body, and fails
# loudly on anything which is not a 2xx: a provider created without
# the fields below is a login which fails for no visible reason much
# later.
#
#   ak_write <method> <path> <description> < body
ak_write() {
    local method="$1" path="$2" what="$3"
    local response status

    response=$("${CURL[@]}" --request "${method}" -H "${AUTH_HEADER}" \
        -H 'Content-Type: application/json' --data @- \
        --write-out '\n%{http_code}' "${API}${path}")
    status="${response##*$'\n'}"
    response="${response%$'\n'*}"

    case "${status}" in
        2*) lab_log "  ${what}" ;;
        *) lab_fail "${what} answered HTTP ${status}: ${response}" ;;
    esac
    printf '%s' "${response}"
}

lab_log 'Configuring Authentik.'

# 1. The brand's web certificate. Uploaded as a keypair, then named by
# the brand. The running server picks it up within seconds and needs
# no restart.
CERTIFICATE_NAME='sf-idp-lab localhost'
CERTIFICATE_PK=$(ak_first "/crypto/certificatekeypairs/?name=${CERTIFICATE_NAME// /%20}")
if [ -z "${CERTIFICATE_PK}" ]; then
    CERTIFICATE_PK=$(python3 -I -c '
import json
import sys

with open(sys.argv[2]) as f:
    certificate = f.read()
with open(sys.argv[3]) as f:
    key = f.read()
json.dump({"name": sys.argv[1], "certificate_data": certificate, "key_data": key},
          sys.stdout)
' "${CERTIFICATE_NAME}" "${IDP_LAB_TLS_DIR}/tls.crt" "${IDP_LAB_TLS_DIR}/tls.key" | \
        ak_write POST /crypto/certificatekeypairs/ 'uploaded the lab certificate' | \
        lab_json_member pk)
else
    lab_log '  the lab certificate is already uploaded'
fi

BRAND_PK=$(ak_wait_first '/core/brands/?default=true' brand_uuid)

printf '{"web_certificate": "%s"}' "${CERTIFICATE_PK}" | \
    ak_write PATCH "/core/brands/${BRAND_PK}/" \
        'set the brand web certificate' > /dev/null

# And then check that it is actually being served, which is not the
# same question. Observed on a cold start: the brand carries the new
# certificate while the server is still presenting the one it
# generated for itself, and another write to the brand is what makes
# it reload. Left unnoticed, the lab would serve a certificate with no
# subjectAltName and the next person to point a cluster at it would be
# debugging a hostname mismatch rather than their own configuration.
#
# The check is the one a cluster makes: curl trusting the lab's
# certificate and nothing else, asking for the provider by name. Not
# --insecure, which is what every other request here uses and what
# would hide exactly this.
lab_log '  waiting for the certificate to be served'
waited=0
until curl --silent --fail --max-time 10 \
        --cacert "${IDP_LAB_TLS_DIR}/tls.crt" \
        --output /dev/null "${AUTHENTIK_URL}/-/health/ready/"; do
    waited=$((waited + 5))
    if [ "${waited}" -ge 120 ]; then
        lab_fail "${AUTHENTIK_URL} is not serving the lab certificate after ${waited}s."
    fi
    sleep 5
    printf '{"web_certificate": "%s"}' "${CERTIFICATE_PK}" | \
        "${CURL[@]}" --request PATCH -H "${AUTH_HEADER}" \
            -H 'Content-Type: application/json' --data @- \
            --output /dev/null "${API}/core/brands/${BRAND_PK}/"
done
lab_log '  the provider is reachable with the lab certificate as the only trust anchor'

# 2. The group, and the person in it.
GROUP_PK=$(ak_first "/core/groups/?name=${LAB_GROUP}")
if [ -z "${GROUP_PK}" ]; then
    GROUP_PK=$(printf '{"name": "%s"}' "${LAB_GROUP}" | \
        ak_write POST /core/groups/ "created group ${LAB_GROUP}" | lab_json_member pk)
else
    lab_log "  group ${LAB_GROUP} is already there"
fi

PERSON_PK=$(ak_first "/core/users/?username=${LAB_PERSON}")
if [ -z "${PERSON_PK}" ]; then
    PERSON_PK=$(python3 -I -c '
import json
import sys

json.dump({"username": sys.argv[1], "name": sys.argv[2], "email": sys.argv[3],
           "is_active": True, "type": "internal", "groups": [sys.argv[4]]},
          sys.stdout)
' "${LAB_PERSON}" "${LAB_PERSON_NAME}" "${LAB_PERSON_EMAIL}" "${GROUP_PK}" | \
        ak_write POST /core/users/ "created person ${LAB_PERSON}" | lab_json_member pk)
else
    lab_log "  person ${LAB_PERSON} is already there"
fi

printf '{"password": "%s"}' "${IDP_LAB_PERSON_PASSWORD}" | \
    ak_write POST "/core/users/${PERSON_PK}/set_password/" \
        "set ${LAB_PERSON}'s password" > /dev/null

# 3. The provider. Everything it refers to is looked up rather than
# hardcoded, except the slugs and managed identifiers of the objects a
# stock install ships, which are stable and are the only names there
# are.
AUTHORIZATION_FLOW=$(ak_wait_first '/flows/instances/?slug=default-provider-authorization-implicit-consent')
INVALIDATION_FLOW=$(ak_wait_first '/flows/instances/?slug=default-provider-invalidation-flow')

# The self signed certificate Authentik generated on first boot is a
# perfectly good signing key even though it is a poor web certificate,
# which is why the lab's replacement above did not take its place
# here. It is found by the name the release ships it under, and the
# lab's own keypair stands in if a later release renames it: this
# listing has no filter for "the one authentik manages", and an
# unrecognised query parameter is ignored rather than refused, so
# asking for one would quietly return every keypair there is.
SIGNING_KEY=$(ak_wait_first '/crypto/certificatekeypairs/?name=authentik%20Self-signed%20Certificate' \
    pk 240 optional)
if [ -z "${SIGNING_KEY}" ]; then
    lab_log '  no shipped self signed certificate; signing with the lab keypair'
    SIGNING_KEY="${CERTIFICATE_PK}"
fi
[ -n "${SIGNING_KEY}" ] || lab_fail 'There is no certificate keypair to sign tokens with.'

scope_mapping() {
    local pk
    pk=$(ak_wait_first "/propertymappings/provider/scope/?managed=goauthentik.io%2Fproviders%2Foauth2%2Fscope-$1")
    printf '%s' "${pk}"
}

PROVIDER_PK=$(ak_first "/providers/oauth2/?name=${LAB_CLIENT_ID}")
if [ -z "${PROVIDER_PK}" ]; then
    PROVIDER_PK=$(cat <<JSON | ak_write POST /providers/oauth2/ 'created the OAuth2 provider' | \
        lab_json_member pk
{
  "name": "${LAB_CLIENT_ID}",
  "authorization_flow": "${AUTHORIZATION_FLOW}",
  "invalidation_flow": "${INVALIDATION_FLOW}",
  "client_type": "public",
  "client_id": "${LAB_CLIENT_ID}",
  "grant_types": ["authorization_code", "refresh_token",
                  "urn:ietf:params:oauth:grant-type:device_code"],
  "include_claims_in_id_token": true,
  "signing_key": "${SIGNING_KEY}",
  "sub_mode": "hashed_user_id",
  "issuer_mode": "per_provider",
  "redirect_uris": [{"matching_mode": "regex",
                     "url": "http://127\\\\.0\\\\.0\\\\.1:[0-9]+/",
                     "redirect_uri_type": "authorization"}],
  "property_mappings": ["$(scope_mapping openid)",
                        "$(scope_mapping profile)",
                        "$(scope_mapping email)"]
}
JSON
)
else
    lab_log "  provider ${LAB_CLIENT_ID} is already there"
fi

# 4. The application, which is what the issuer URL is named after.
if [ -z "$(ak_first "/core/applications/?slug=${AUTHENTIK_SLUG}" slug)" ]; then
    printf '{"name": "Shaken Fist", "slug": "%s", "provider": %s}' \
        "${AUTHENTIK_SLUG}" "${PROVIDER_PK}" | \
        ak_write POST /core/applications/ "created application ${AUTHENTIK_SLUG}" > /dev/null
else
    lab_log "  application ${AUTHENTIK_SLUG} is already there"
fi

# 5. The device code flow, and the brand pointed at it. The flow needs
# no stages bound to it at all; what matters is that it exists and
# that the brand names it.
DEVICE_FLOW_SLUG='sf-idp-lab-device-code'
DEVICE_FLOW=$(ak_first "/flows/instances/?slug=${DEVICE_FLOW_SLUG}")
if [ -z "${DEVICE_FLOW}" ]; then
    DEVICE_FLOW=$(cat <<JSON | ak_write POST /flows/instances/ 'created the device code flow' | \
        lab_json_member pk
{
  "name": "${DEVICE_FLOW_SLUG}",
  "title": "Device code flow",
  "slug": "${DEVICE_FLOW_SLUG}",
  "designation": "stage_configuration",
  "authentication": "require_authenticated"
}
JSON
)
else
    lab_log '  the device code flow is already there'
fi

printf '{"flow_device_code": "%s"}' "${DEVICE_FLOW}" | \
    ak_write PATCH "/core/brands/${BRAND_PK}/" \
        'pointed the brand at the device code flow' > /dev/null

# What the provider now serves, read back rather than assumed. The
# discovery document is deliberately not used as evidence that the
# device flow works -- it advertises the endpoint and the grant in
# every state, including the two broken ones -- so the check here is
# that a device code request is answered, which the provider's
# grant_types decide.
lab_log 'Checking what the provider serves.'
discovery=$("${CURL[@]}" "${AUTHENTIK_ISSUER%/}/.well-known/openid-configuration")
issuer=$(printf '%s' "${discovery}" | lab_json_member issuer)
[ "${issuer}" = "${AUTHENTIK_ISSUER}" ] || \
    lab_fail "The provider calls itself ${issuer}, not ${AUTHENTIK_ISSUER}."
lab_log "  the issuer is ${issuer}"

scopes=$(printf '%s' "${discovery}" | lab_json_member scopes_supported)
case "${scopes}" in
    *profile*) lab_log "  scopes_supported is ${scopes}" ;;
    *) lab_fail "The provider does not offer the profile scope: ${scopes}" ;;
esac

device=$("${CURL[@]}" --request POST "${AUTHENTIK_URL}/application/o/device/" \
    --data-urlencode "client_id=${LAB_CLIENT_ID}" \
    --data-urlencode "scope=${LAB_SCOPES}")
if [ -z "$(printf '%s' "${device}" | lab_json_member user_code)" ]; then
    lab_fail "The device endpoint refused a code request: ${device}"
fi
lab_log '  the device endpoint issued a user code'

lab_log 'Authentik is configured.'
lab_log "  issuer       ${AUTHENTIK_ISSUER}"
lab_log "  client_id    ${LAB_CLIENT_ID}"
lab_log "  oidc_scopes  ${LAB_SCOPES}"
lab_log "  group claim  \"groups\": [\"${LAB_GROUP}\"]"
lab_log "  person       ${LAB_PERSON}, password in ${IDP_LAB_ENV_FILE}"
