#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-jwks-ca.sh -- give the CI cluster a certificate authority it will
# trust for federated identity provider JWKS fetches, and leave that
# authority's key on the primary so the functional tests can issue
# themselves a leaf certificate with it.
#
# Why this exists (issue #3639). The federated exchange can only be
# tested end to end if the cluster can fetch a JWKS from the test
# process. jwks_uri must be https -- a JWKS fetched over plaintext can
# be substituted by anyone on the path, which turns signature
# verification into theatre -- and PyJWKClient verifies against the
# trust store, so a certificate the test signs itself is refused and
# five tests in cluster_ci_tests/test_federation.py skip.
#
# What it does NOT do is relax that validation. It uses
# FEDERATION_JWKS_CA_BUNDLE, which is a production feature in its own
# right: a self hosted Authentik or Keycloak is usually behind a
# private CA, and an operator needs some way to say so. CI is simply
# its first user. The anchors are added to the system ones rather than
# replacing them, so a public issuer still verifies normally.
#
# THIS IS FOR THROWAWAY CI CLUSTERS ONLY. It puts a CA signing key on a
# node, readable by the test user. That is acceptable here because the
# CA is minted fresh for this run, is trusted for nothing but JWKS
# fetches, and the whole cluster is destroyed within the hour. Do not
# reach for this script to configure a real deployment: there, the
# operator's own CA certificate goes on the nodes, the key stays where
# it already lives, and only federation_jwks_ca_bundle is set.
#
# Requires the node address variables and the "nodes" list from
# ci-environment.sh, which the caller must have sourced.
#
# Arguments (positional):
#   $1  ssh user for the test nodes (default: debian)

# set -u as well as set -e, because the failure this script exists to
# prevent is a silent one. Without it, an unsourced ci-environment.sh
# leaves ${nodes} empty, the install loop iterates zero times, and the
# script exits 0 having done nothing -- after which the five federation
# tests skip with a message blaming this script for not having run. The
# explicit guards below say which variable is missing rather than
# leaving it to scp's argument parsing to fail obscurely.
set -eu

SSH_USER="${1:-debian}"
SSH_OPTS="-i /srv/github/id_ci -o StrictHostKeyChecking=no"
SSH_OPTS="${SSH_OPTS} -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

# Where the bundle lands on every node, and where the test looks for the
# signing material on the primary. Both are also named in
# shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_federation.py;
# change them together.
BUNDLE_PATH="/etc/sf/jwks-ca.pem"
TEST_CA_DIR=".sf-ci-jwks-ca"

: "${nodes:?ci-environment.sh must be sourced before running this script}"
: "${primary:?ci-environment.sh must be sourced before running this script}"

workdir=$(mktemp -d /tmp/sf-ci-jwks-ca.XXXXXX)
trap 'rm -rf "${workdir}"' EXIT

echo "Minting a throwaway JWKS certificate authority in ${workdir}"
openssl req -x509 -newkey rsa:2048 -sha256 -days 1 -nodes \
    -keyout "${workdir}/ca-key.pem" -out "${workdir}/ca-cert.pem" \
    -subj "/CN=Shaken Fist CI JWKS CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"

# The exchange is served by whichever sf-api the test talks to, and
# api_url defaults to http://localhost:13000 -- so today that is always
# the primary. Installing on every node anyway costs one scp each and
# means a topology which later points api_url elsewhere does not
# silently go back to skipping.
for node in ${nodes}; do
    address="${!node}"
    echo "Installing the CA bundle on ${node} (${address})"

    scp ${SSH_OPTS} "${workdir}/ca-cert.pem" \
        "${SSH_USER}@${address}:/tmp/jwks-ca.pem"

    # Appending to /etc/sf/config rather than templating it: the deploy
    # has already run, and sf-api reads this file as an EnvironmentFile
    # at start, so the restart below is what actually applies it.
    ssh ${SSH_OPTS} "${SSH_USER}@${address}" \
        "sudo install -m 0444 -o root -g root /tmp/jwks-ca.pem ${BUNDLE_PATH} \
         && sudo sed -i '/^SHAKENFIST_FEDERATION_JWKS_CA_BUNDLE=/d' /etc/sf/config \
         && echo 'SHAKENFIST_FEDERATION_JWKS_CA_BUNDLE=\"${BUNDLE_PATH}\"' \
            | sudo tee -a /etc/sf/config > /dev/null"
done

# Restarted in parallel: sf-api drains for API_DRAIN_GRACE (25s) on
# SIGTERM, so doing five nodes serially would cost over two minutes of
# CI wall clock. Nothing is using the cluster at this point -- the
# functional suite has not started -- so there is no traffic to shed.
echo "Restarting sf-api to pick up the bundle"
for node in ${nodes}; do
    address="${!node}"
    ssh ${SSH_OPTS} "${SSH_USER}@${address}" \
        "sudo systemctl restart sf-api" &
done
wait

# A restart that has not finished looks exactly like a cluster that
# never trusted the CA, so wait for each node to answer before handing
# back. 60 attempts at 5s is a five minute budget, well past the 70s
# TimeoutStopSec in the unit file.
for node in ${nodes}; do
    address="${!node}"
    count=0
    until ssh ${SSH_OPTS} "${SSH_USER}@${address}" \
            "curl -sf http://localhost:13000/readyz > /dev/null"; do
        count=$(( count + 1 ))
        if [ "${count}" -gt 60 ]; then
            echo "sf-api on ${node} (${address}) did not come back."
            ssh ${SSH_OPTS} "${SSH_USER}@${address}" \
                "sudo journalctl -u sf-api --no-pager -n 100" || true
            exit 1
        fi
        sleep 5
    done
    # Readiness proves sf-api came back, not that it came back with the
    # bundle -- and an sf-api which restarted without the setting fails
    # in exactly the way this script exists to prevent.
    ssh ${SSH_OPTS} "${SSH_USER}@${address}" \
        "grep -q '^SHAKENFIST_FEDERATION_JWKS_CA_BUNDLE=' /etc/sf/config \
         && sudo test -r ${BUNDLE_PATH}"

    echo "sf-api on ${node} (${address}) is ready."
done

# The signing key, for the test process only. mode 0700/0600 rather
# than something more relaxed: the cluster is disposable but there is
# no reason for this to be readable by every process on the box.
#
# Copied straight into the 0700 directory rather than via /tmp. The
# staged version left the CA signing key sitting world readable with
# scp's default mode between the copy and the chmod, which is a window
# this script's own header does not admit to and does not need to have.
echo "Leaving the CA signing material on the primary for the tests"
ssh ${SSH_OPTS} "${SSH_USER}@${primary}" \
    "mkdir -p ~/${TEST_CA_DIR} && chmod 0700 ~/${TEST_CA_DIR}"
scp ${SSH_OPTS} "${workdir}/ca-cert.pem" "${workdir}/ca-key.pem" \
    "${SSH_USER}@${primary}:${TEST_CA_DIR}/"
ssh ${SSH_OPTS} "${SSH_USER}@${primary}" \
    "chmod 0600 ~/${TEST_CA_DIR}/ca-key.pem"

echo "Done. cluster_ci_tests/test_federation.py will now issue a trusted leaf."
