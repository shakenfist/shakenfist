#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# make-certificates.sh -- issue the self signed certificate both
# providers in this lab serve https with.
#
# Two separate reasons the lab cannot do without this, one per
# provider, and neither is the obvious "it needs TLS".
#
# Keycloak's start-dev generates no certificate at all and listens on
# http only. Shaken Fist refuses an issuer URL which is not https, and
# refuses a discovery document whose endpoints or jwks_uri are not
# https, so a plaintext provider cannot produce a usable capture.
#
# Authentik does serve https from first boot, with a certificate it
# generates itself -- but that certificate carries no subjectAltName.
# Python's TLS then refuses it by hostname even when that very
# certificate is the trust anchor, so FEDERATION_JWKS_CA_BUNDLE cannot
# rescue it and only replacing it helps:
#
#     certificate verify failed: Hostname mismatch, certificate is not
#     valid for 'localhost'
#
# So the subjectAltName below is the point of this script, not a
# detail of it. configure-authentik.sh uploads what this writes and
# sets it as the brand's web certificate.
#
# The certificate is valid for two days, which is longer than any lab
# should live and short enough that a forgotten container's
# certificate expires rather than lingering as something that might be
# trusted. It is reissued when it has less than a day to run.
#
#   tools/idp-lab/make-certificates.sh

set -euo pipefail

# shellcheck source=tools/idp-lab/lab-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lab-common.sh"

CERTIFICATE="${IDP_LAB_TLS_DIR}/tls.crt"
KEY="${IDP_LAB_TLS_DIR}/tls.key"

mkdir -p "${IDP_LAB_TLS_DIR}"
chmod 0700 "${IDP_LAB_TLS_DIR}"

if [ -r "${CERTIFICATE}" ] && [ -r "${KEY}" ] && \
        openssl x509 -in "${CERTIFICATE}" -noout -checkend 86400 >/dev/null 2>&1; then
    lab_log "Reusing the certificate in ${IDP_LAB_TLS_DIR}."
    exit 0
fi

lab_log "Issuing a self signed certificate for localhost into ${IDP_LAB_TLS_DIR}."
umask 077
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "${KEY}" -out "${CERTIFICATE}" -days 2 -subj '/CN=localhost' \
    -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' 2>/dev/null

# Keycloak runs as a non-root user inside its container and reads both
# files from a read only mount, so the key has to be readable by
# somebody other than the person who made it. That is acceptable for a
# key which is self signed, loopback only and valid for two days, and
# it is why the directory above it is 0700.
chmod 0644 "${CERTIFICATE}"
chmod 0644 "${KEY}"

openssl x509 -in "${CERTIFICATE}" -noout -subject -dates -ext subjectAltName | \
    sed 's/^/[idp-lab]   /'
