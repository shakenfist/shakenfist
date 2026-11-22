#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# configure-keycloak.sh -- configure the lab's Keycloak for a human
# login, through its admin REST API, so that the walkthrough in
# docs/operator_guide/authentication.md can be re-run rather than
# remembered.
#
# What it creates, and why each piece is needed:
#
#   * a realm, whose name is the last element of the issuer URL;
#   * a group, membership of which is what a Shaken Fist mapping rule
#     binds;
#   * a public client. Public rather than confidential because the
#     thing registering is a command line program on a person's
#     laptop: a confidential client's secret would have to be handed
#     to everyone who logs in. The device grant requires a public
#     client anyway;
#   * a group membership protocol mapper on the client's own dedicated
#     scope, with id.token.claim on. Keycloak ships no group claim at
#     all, and a mapper pointed only at the access token leaves a
#     claim visible at /userinfo while every login is refused,
#     because the identity token is the only token Shaken Fist sees;
#   * a person, in that group, with a verified email and a
#     non-temporary password. Either alternative puts a required
#     action on the account, which stops the login on an extra screen
#     before any token is issued.
#
# Two settings are deliberately absent. The client's PKCE method is
# left unset, because setting it to S256 makes Keycloak require a
# challenge on every request including the device authorization
# request, which has nowhere to put one -- that breaks the device flow
# and nothing else. And the login asks for "openid profile" rather
# than naming a groups scope, because Keycloak has no groups scope and
# refuses the request outright; the mapper on the client's dedicated
# scope is always in scope and the claim arrives anyway.
#
# Safe to re-run: every object is checked for before it is created.
#
#   tools/idp-lab/configure-keycloak.sh

set -euo pipefail

# shellcheck source=tools/idp-lab/lab-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"

lab_load_env

TOKEN=''

kc_get() {
    "${CURL[@]}" -H "Authorization: Bearer ${TOKEN}" "${KEYCLOAK_URL}$1"
}

# A write, printing the HTTP status so the caller can tell "created"
# from "already there" from "refused" rather than guessing from an
# empty body.
kc_write() {
    local method="$1" path="$2" body="${3:-}"
    local args=(--output /dev/null --write-out '%{http_code}' --request "${method}"
                -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json')

    if [ -n "${body}" ]; then
        args+=(--data "${body}")
    fi
    "${CURL[@]}" "${args[@]}" "${KEYCLOAK_URL}${path}"
}

# Created, or already present, and anything else is a failure. A realm
# left half configured because a 400 went unnoticed is a login which
# fails much later for no visible reason.
kc_expect_created() {
    local what="$1" status="$2"

    case "${status}" in
        201|204) lab_log "  created ${what}" ;;
        409) lab_log "  ${what} is already there" ;;
        *) lab_fail "Creating ${what} answered HTTP ${status}." ;;
    esac
}

# The admin bootstrap account, through the master realm's admin-cli
# client. Keycloak 26 calls the account its KC_BOOTSTRAP_ADMIN_*
# variables create a *temporary* admin, which is worth knowing before
# anybody treats it as permanent.
lab_log 'Getting an administrative token.'
TOKEN=$("${CURL[@]}" --request POST \
    "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    --data-urlencode 'client_id=admin-cli' \
    --data-urlencode 'grant_type=password' \
    --data-urlencode "username=${IDP_LAB_KEYCLOAK_ADMIN_USER}" \
    --data-urlencode "password=${IDP_LAB_KEYCLOAK_ADMIN_PASSWORD}" | \
    lab_json_member access_token)
[ -n "${TOKEN}" ] || lab_fail 'Could not get an administrative token from Keycloak.'

lab_log "Configuring the ${KEYCLOAK_REALM} realm."
kc_expect_created "realm ${KEYCLOAK_REALM}" \
    "$(kc_write POST /admin/realms "{\"realm\": \"${KEYCLOAK_REALM}\", \"enabled\": true}")"

kc_expect_created "group ${LAB_GROUP}" \
    "$(kc_write POST "/admin/realms/${KEYCLOAK_REALM}/groups" "{\"name\": \"${LAB_GROUP}\"}")"

# redirect_uris is http://127.0.0.1:* because sf-client binds an
# ephemeral loopback port for the browser flow, so the redirect URI it
# sends cannot be registered exactly. Keycloak's trailing wildcard
# matches it. Plaintext http on a loopback literal is correct here, and
# is what the device grant exists to avoid needing at all.
kc_expect_created "client ${LAB_CLIENT_ID}" "$(kc_write POST \
    "/admin/realms/${KEYCLOAK_REALM}/clients" "$(cat <<JSON
{
  "clientId": "${LAB_CLIENT_ID}",
  "name": "Shaken Fist command line client",
  "enabled": true,
  "publicClient": true,
  "standardFlowEnabled": true,
  "directAccessGrantsEnabled": false,
  "serviceAccountsEnabled": false,
  "redirectUris": ["http://127.0.0.1:*"],
  "webOrigins": [],
  "attributes": {"oauth2.device.authorization.grant.enabled": "true"}
}
JSON
)")"

CLIENT_UUID=$(kc_get "/admin/realms/${KEYCLOAK_REALM}/clients?clientId=${LAB_CLIENT_ID}" | \
    python3 -I -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')

# full.path writes "/sf-admins" rather than "sf-admins", and it is set
# explicitly because the two ways of creating this mapper disagree
# about its default: the console submits true, while a POST which
# omits the key behaves as though it were false. The leading slash is
# a path, so a group nested under another is "/parent/child", which is
# the real reason to keep full paths on -- and whichever form is
# chosen, a mapping rule has to bind the string the token carries.
if kc_get "/admin/realms/${KEYCLOAK_REALM}/clients/${CLIENT_UUID}/protocol-mappers/models" | \
        grep -q '"name"[ ]*:[ ]*"groups"'; then
    lab_log '  the groups mapper is already there'
else
    kc_expect_created 'the groups mapper' "$(kc_write POST \
        "/admin/realms/${KEYCLOAK_REALM}/clients/${CLIENT_UUID}/protocol-mappers/models" \
        "$(cat <<'JSON'
{
  "name": "groups",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-group-membership-mapper",
  "config": {"claim.name": "groups",
             "full.path": "true",
             "id.token.claim": "true",
             "access.token.claim": "true",
             "userinfo.token.claim": "true",
             "introspection.token.claim": "true"}
}
JSON
)")"
fi

kc_expect_created "person ${LAB_PERSON}" "$(kc_write POST \
    "/admin/realms/${KEYCLOAK_REALM}/users" "$(cat <<JSON
{
  "username": "${LAB_PERSON}",
  "enabled": true,
  "emailVerified": true,
  "email": "${LAB_PERSON_EMAIL}",
  "firstName": "${LAB_PERSON_NAME%% *}",
  "lastName": "${LAB_PERSON_NAME##* }",
  "credentials": [{"type": "password",
                   "value": "${IDP_LAB_PERSON_PASSWORD}",
                   "temporary": false}]
}
JSON
)")"

USER_UUID=$(kc_get "/admin/realms/${KEYCLOAK_REALM}/users?username=${LAB_PERSON}&exact=true" | \
    python3 -I -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')
GROUP_UUID=$(kc_get "/admin/realms/${KEYCLOAK_REALM}/groups?search=${LAB_GROUP}" | \
    python3 -I -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')

kc_expect_created "${LAB_PERSON}'s membership of ${LAB_GROUP}" \
    "$(kc_write PUT "/admin/realms/${KEYCLOAK_REALM}/users/${USER_UUID}/groups/${GROUP_UUID}")"

# A device code request is the cheapest proof that the client
# attribute above took effect: without it this endpoint answers 400
# with "Client is not allowed to initiate OAuth 2.0 Device
# Authorization Grant", which is clearer than anything the cluster can
# say. Note that the endpoint is advertised in the discovery document
# per *realm* whether or not any client can use the grant, so the
# document is not the thing to check.
lab_log 'Checking the device grant is enabled on the client.'
device=$("${CURL[@]}" --request POST \
    "${KEYCLOAK_ISSUER}/protocol/openid-connect/auth/device" \
    --data-urlencode "client_id=${LAB_CLIENT_ID}" \
    --data-urlencode "scope=${LAB_SCOPES}")
if [ -z "$(printf '%s' "${device}" | lab_json_member user_code)" ]; then
    lab_fail "The device endpoint refused a code request: ${device}"
fi
lab_log '  the device endpoint issued a user code'

# The certificate this provider serves is the one the lab mounted into
# its container, so unlike the other provider there is nothing to
# reload and nothing to wait for. It is still checked, with the lab's
# certificate as the only trust anchor and the host named, because
# that is the question a cluster asks of an issuer and a capture taken
# from a provider which cannot answer it is not a usable fixture.
lab_log 'Checking the discovery document is reachable over verified TLS.'
curl --silent --fail --max-time 10 --cacert "${IDP_LAB_TLS_DIR}/tls.crt" \
    --output /dev/null "${KEYCLOAK_ISSUER}/.well-known/openid-configuration" || \
    lab_fail "${KEYCLOAK_ISSUER} is not serving the lab certificate."
lab_log '  the issuer is reachable with the lab certificate as the only trust anchor'

lab_log 'Keycloak is configured.'
lab_log "  issuer       ${KEYCLOAK_ISSUER}"
lab_log "  client_id    ${LAB_CLIENT_ID}"
lab_log "  oidc_scopes  ${LAB_SCOPES}"
lab_log "  group claim  \"groups\": [\"/${LAB_GROUP}\"]"
lab_log "  person       ${LAB_PERSON}, password in ${IDP_LAB_ENV_FILE}"
