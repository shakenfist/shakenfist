#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-idp-keycloak.sh -- stand a real OpenID Connect provider up on the
# CI cluster's primary, configure it for a human login, and leave a
# descriptor the functional tests read.
#
# Why this exists. The federated exchange has end to end coverage
# against a throwaway JWKS server the test process runs itself
# (tools/ci-jwks-ca.sh and cluster_ci_tests/test_federation.py), which
# is the right trade for testing validation: the test controls the
# claims and the signature, so it can assert what the cluster refuses.
# It is no use at all for testing a *flow*. Discovery reads a document
# we did not write, the login exchange trades an identity token a
# provider minted for its own reasons, and the device grant is a
# conversation between a person, a provider and a command line client.
# None of that can be faked by the thing under test without the test
# becoming a reimplementation of the provider. So CI needs a provider.
#
# Keycloak, and only Keycloak. The other provider the operator guide
# walks through needs four containers and runs database migrations on
# its first boot, which takes minutes on a cold host; this one is a
# single container with an embedded database. The primary this runs on
# has 12288 MiB in the topology that calls this script and 4096 MiB in
# the ones that do not, which is why this is not run on all of them,
# and why one provider rather than two. The other provider keeps the
# coverage it has: committed captures of its discovery document and its
# identity token claims under shakenfist/tests/files/idp/, and a
# reproducible lab under tools/idp-lab/.
#
# The certificate. The cluster refuses an issuer_url which is not
# https, and refuses a discovery document whose endpoints or jwks_uri
# are not https -- a document or a JWKS fetched over plaintext can be
# substituted by anyone on the path, which turns signature
# verification into theatre. So the provider has to serve https with a
# certificate the cluster will accept, and it gets one from the
# certificate authority tools/ci-jwks-ca.sh already minted and
# installed: discovery, the JWKS fetch and the login exchange all
# resolve TLS through FEDERATION_JWKS_CA_BUNDLE, so a leaf signed by
# that authority needs no new configuration key and no edit to any
# node's system trust store. That script must therefore have run
# first, and this one says so rather than failing later with a
# certificate error.
#
# The subjectAltName below is the point of the certificate work, not a
# detail of it. A certificate with no subjectAltName is refused by
# hostname even when the authority that signed it is the only trust
# anchor, and no bundle setting repairs that:
#
#     certificate verify failed: Hostname mismatch, certificate is not
#     valid for '10.0.0.10'
#
# THIS IS FOR THROWAWAY CI CLUSTERS ONLY, and installs Docker on a
# cluster node to get there. That is acceptable here for the same
# reasons tools/ci-jwks-ca.sh is willing to leave a signing key on a
# node: the cluster is built for one workflow run and destroyed within
# the hour, the provider it serves is trusted for nothing outside that
# run, and every credential involved is generated fresh and never
# written down anywhere a later run can reach. It is unacceptable
# anywhere else. A real deployment's provider is not a container
# somebody's test harness installed a container runtime to start; it
# has an operator, a certificate from the operator's own authority,
# and accounts that are not throwaway. Do not reach for this script to
# configure one.
#
# Docker and this node's networking. The node this runs on is a
# hypervisor and the cluster's network node, so its iptables rules are
# instance connectivity, and Docker left to itself rewrites some of
# them. Measured against the version Debian 13 packages (26.1), a
# default daemon's first start inserts twelve rules into the filter
# table -- among them a DOCKER-USER and a DOCKER-ISOLATION-STAGE-1 jump
# at the head of FORWARD -- and five into nat. It sets the FORWARD
# policy to DROP as well where it has had to turn ip forwarding on
# itself, which it has not on a node that already forwards. All of that
# is correct for a host whose containers want a private bridge, and
# none of it is wanted here: one container, serving one port, on a node
# where nothing else listens on it. So the container is given the
# host's network namespace, which needs no address translation at all,
# and the daemon is told to add no rules.
#
# It is told so *before the package is installed*, because installing
# it starts it. A daemon.json written afterwards stops Docker managing
# rules from its restart onwards and does nothing about the rules the
# first start has already inserted, so the mitigation would not hold on
# a node where Docker was absent -- which is every CI node. Configured
# first, it inserts none: the same measurement with {"iptables": false}
# in place beforehand leaves the filter table exactly as it found it
# and adds nothing to nat. Where the node already has a daemon.json of
# its own, this script leaves it alone and says so rather than assuming
# it harmless; the first run of the lane is the evidence, and the node
# level checks the workflow runs after the suite are where a broken
# bridge would show.
#
# Credentials. The person's password is generated here, with openssl
# rand, and the administrative password is generated and then
# discarded: it reaches the container's environment and nothing else,
# because the tests have no business with Keycloak's admin API.
# Exporting SF_CI_IDP_PERSON_PASSWORD or SF_CI_IDP_ADMIN_PASSWORD
# chooses one instead, which is what a person reproducing a CI failure
# by hand wants; the generated default means no credential is written
# down in this repository and each cluster gets its own.
#
# What the tests get is the descriptor, ~/.sf-ci-idp.json on the
# primary, mode u=r,go=, holding:
#
#   issuer_url    what to create a trusted issuer with
#   client_id     the client the login flows use, and the audience the
#                 identity token will carry
#   oidc_scopes   what a login must ask for. "openid profile" rather
#                 than a groups scope: Keycloak has no groups scope and
#                 refuses a request that names one, while the mapper
#                 below is on the client's dedicated scope and so is
#                 always in scope anyway
#   username      the person who logs in
#   password      their password
#   group_claim   the name of the claim the identity token carries the
#                 person's groups in. Keycloak ships no group claim at
#                 all, so this is the name the mapper below was given
#                 and not a standard: a mapping rule binds
#                 {group_claim: group}
#   group         the group membership a mapping rule binds, as the
#                 string that claim actually carries. The leading slash
#                 is not a typo: the mapper writes full group paths, so
#                 a nested group would be "/parent/child", and a rule
#                 has to bind the string the token carries rather than
#                 the group's name
#   image         the provider image this descriptor describes, so a
#                 test that broke against a new provider version can
#                 say which version
#
# and nothing else. In particular it does not carry a trust anchor: a
# test which needs to talk to the provider directly verifies with
# ~/.sf-ci-jwks-ca/ca-cert.pem, the same authority
# cluster_ci_tests/test_federation.py already reads, overridable with
# SF_CI_JWKS_CA_DIR.
#
# Every request to the provider is made on the primary, over ssh, and
# only the reading of what comes back happens on the runner. Driving
# the administrative API from the runner would be easier to write, and
# it would make the runner's ability to reach the provider's port a
# dependency of the whole configuration phase -- which nothing else in
# this workflow has. Every step that touches a node reaches it over
# ssh and nothing else; no step has ever opened another port on one.
# A lane that runs weekly, and reports a failure as an issue up to a
# week later, is a bad place to be the first. So the primary curls its
# own address, verifying against the authority already sitting there,
# and a body that cannot survive being quoted for a remote shell --
# which means any body carrying a credential, because a credential has
# no business in a command line either -- is written to a file on the
# primary first and named to curl as a file.
#
# What the cluster itself needs is a different question, and it is
# asked at the end, from every node, with the bundle those nodes trust.
#
# Safe to re-run, and a re-run starts over rather than adjusting what
# is there: the container, its certificate and its administrative
# password are all replaced, so the second run configures a provider
# from scratch. This provider keeps its state inside its own
# container, which is why that is cheap and why nothing here mounts a
# volume.
#
# Requires the node address variables and the "nodes" list from
# ci-environment.sh, which the caller must have sourced, and requires
# tools/ci-jwks-ca.sh to have run first.
#
# Arguments (positional):
#   $1  ssh user for the test nodes (default: debian)

# set -u as well as set -e, for the reason tools/ci-jwks-ca.sh gives:
# the failure this script exists to prevent is a silent one, and an
# unsourced ci-environment.sh leaves ${primary} empty, after which ssh
# is handed a user with no host and the tests skip blaming a step that
# did run. The explicit guards below say which variable is missing
# rather than leaving it to ssh's argument parsing to fail obscurely.
#
# pipefail as well, which ci-jwks-ca.sh has no need of: the
# configuration below reads provider documents through pipes into
# python3, and a curl that failed must not reach the parser looking
# like an empty document.
set -eu
set -o pipefail

SSH_USER="${1:-debian}"
SSH_OPTS="-i /srv/github/id_ci -o StrictHostKeyChecking=no"
SSH_OPTS="${SSH_OPTS} -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10"

# Pinned, and pinned to the same tag tools/idp-lab/docker-compose.yml
# serves the committed captures from, so the fixtures, the operator
# guide's walkthrough and CI are all describing one provider version.
# shakenfist/tests/test_ci_idp_keycloak_seams.py holds the two tags
# together; change them in one commit. A moving tag would rewrite the
# provider's own login page and administration API underneath the tests
# that drive them, which is what pinning is for.
IMAGE='quay.io/keycloak/keycloak:26.8.2'
CONTAINER='sf-ci-idp'
IDP_PORT='8443'

# An explicit ceiling rather than whatever the JVM decides it may have.
# This container shares the primary with sf-api, the node's own
# services, the functional suite's virtualenv and five stestr workers,
# and the reason this lane was chosen is that it has the memory to
# spare -- "has the memory to spare" is a claim about a bounded
# container, not an unbounded one. Keycloak's own container sizes its
# heap as a percentage of this, so the limit is also the heap setting.
MEMORY='1500m'

# The realm name is the last element of the issuer URL, and these
# values deliberately match tools/idp-lab/lab-common.sh so that the
# committed captures under shakenfist/tests/files/idp/ describe the
# same configuration this one serves. The captures' issuer URLs are the
# lab's and are not these, which is why a test may compare their shape
# and never their values.
REALM='shakenfist'
CLIENT_ID='shakenfist'
SCOPES='openid profile'
GROUP='sf-admins'
PERSON='person'
PERSON_EMAIL='person@example.com'
PERSON_FIRST='Example'
PERSON_LAST='Person'

# Where tools/ci-jwks-ca.sh left the signing material, on the primary.
# A relative path is relative to the ssh user's home, which is where
# that script puts it.
#
# SF_CI_JWKS_CA_DIR overrides it, and its three readers do not mean the
# same thing by it -- which matters because overriding it is how a
# person reproduces a CI failure by hand:
#
#   * here it is a path on the *primary*, read by the commands this
#     script sends over ssh, and relative by default;
#   * cluster_ci_tests/test_oidc_login.py and
#     cluster_ci_tests/test_federation.py read it as a path on whatever
#     host runs the suite, defaulting to an absolute ~/... -- the same
#     directory as this one whenever that host is the primary, which in
#     CI it is, and a different one as soon as it is not;
#   * tools/ci-jwks-ca.sh, which creates the authority in the first
#     place, does not honour it at all and always writes
#     ~/.sf-ci-jwks-ca.
#
# So overriding it means overriding it for each side separately, and
# putting the authority somewhere else means moving it by hand.
CA_DIR="${SF_CI_JWKS_CA_DIR:-.sf-ci-jwks-ca}"

# Where that script installed the bundle on every node. The last check
# in this script is that each node can fetch the provider's discovery
# document with this as its only trust anchor, which is the question
# sf-api asks of an issuer.
BUNDLE_PATH='/etc/sf/jwks-ca.pem'

# What the tests read, and where the container's own files live. Both
# are also named in
# shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_oidc_login.py;
# change them together.
DESCRIPTOR='.sf-ci-idp.json'
STATE_DIR='.sf-ci-idp'

GROUP_CLAIM_NAME='groups'

fail() {
    echo "ci-idp-keycloak.sh: $*" >&2
    exit 1
}

: "${nodes:?ci-environment.sh must be sourced before running this script}"
: "${primary:?ci-environment.sh must be sourced before running this script}"

# Every node resolved before anything is built, rather than when the
# verification loop reaches it. A cluster whose third node has no
# address is a cluster this script cannot finish verifying, and
# discovering that after a provider is running wastes the run and
# leaves a half configured node behind.
node_addresses=''
for node in ${nodes}; do
    # ci-environment.sh replaces '-' with '_' in node names so each one
    # is a shell variable name; anything else cannot be dereferenced.
    if ! [[ "${node}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        fail "\${nodes} names '${node}', which is not a shell variable" \
             "name, so its address cannot be looked up"
    fi
    address="${!node:-}"
    [ -n "${address}" ] || \
        fail "\${nodes} names ${node} but \${${node}} (its address) is" \
             "unset or empty"
    node_addresses="${node_addresses}${node} ${address}"$'\n'
done

ISSUER_URL="https://${primary}:${IDP_PORT}/realms/${REALM}"
KEYCLOAK_URL="https://${primary}:${IDP_PORT}"
GROUP_CLAIM="/${GROUP}"

PERSON_PASSWORD="${SF_CI_IDP_PERSON_PASSWORD:-$(openssl rand -hex 12)}"
ADMIN_USER='admin'
ADMIN_PASSWORD="${SF_CI_IDP_ADMIN_PASSWORD:-$(openssl rand -hex 16)}"

workdir=$(mktemp -d /tmp/sf-ci-idp.XXXXXX)
trap 'rm -rf "${workdir}"' EXIT

# Run one command on the primary.
#
# -n, so that stdin belongs to this script and not to ssh: without it
# the first call inside a loop reads the rest of the loop's input and
# the loop runs once.
on_primary() {
    ssh -n ${SSH_OPTS} "${SSH_USER}@${primary}" "$1"
}

# Write stdin to a file on the primary, at a mode, reporting what was
# left behind.
#
# Under a umask rather than copied and then chmodded: these files hold
# credentials, and the staged form leaves one world readable with scp's
# default mode for as long as the chmod takes to arrive. Removed first
# because a mode this narrow cannot be written to a second time.
#
#   to_primary <path> [mode]
to_primary() {
    local path="$1" mode="${2:-0600}"

    ssh ${SSH_OPTS} "${SSH_USER}@${primary}" \
        "umask 077 && rm -f '${path}' && cat > '${path}' && \
         chmod ${mode} '${path}' && ls -l '${path}'"
}

# One round trip for the two facts every later step needs: where the
# ssh user's home is, so that every path below is absolute and
# unambiguous in the log, and whether the certificate authority is
# there. Asked together and answered as data, so that an ssh which
# could not connect at all fails here as itself rather than being
# reported as a missing authority.
echo "Asking ${primary} for its home directory and the JWKS CA"
primary_facts=$(on_primary \
    "echo \"home=\${HOME}\"; \
     if [ -r '${CA_DIR}/ca-cert.pem' ] && [ -r '${CA_DIR}/ca-key.pem' ]; \
     then echo 'ca=yes'; else echo 'ca=no'; fi")

case "${primary_facts}" in
    *'ca=yes'*) ;;
    *) fail "${CA_DIR}/ca-cert.pem and ca-key.pem are not both readable" \
            "on ${primary}. tools/ci-jwks-ca.sh mints that authority," \
            "installs it on every node and leaves the key there, and it" \
            "must run before this script: the provider's certificate is" \
            "issued from it, and a certificate from anywhere else is one" \
            "the cluster will refuse." ;;
esac

remote_home=$(printf '%s\n' "${primary_facts}" | sed -n 's/^home=//p')
[ -n "${remote_home}" ] || \
    fail "${primary} did not say where ${SSH_USER}'s home directory is"

remote_state="${remote_home}/${STATE_DIR}"
remote_descriptor="${remote_home}/${DESCRIPTOR}"

# The leaf, issued on the primary by the primary. The signing key is
# already there and there is no reason to copy a certificate
# authority's key over another hop to do arithmetic that can be done
# where it lives.
#
# An address is typed in a subjectAltName: IP for a literal, DNS for a
# name, and the wrong one of those is a certificate that verifies
# against nothing. The topologies this runs in give the nodes literal
# addresses, and the other branch is here so that a topology which
# gives them names is a configuration change rather than a debugging
# session.
if [[ "${primary}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    SAN="IP:${primary}"
else
    SAN="DNS:${primary}"
fi

# Valid for one day, which is the authority's own lifetime and far
# longer than any CI cluster lives.
#
# The key identifier extensions are not decoration, and are not set
# here because openssl adds both when it is given a -CA: the leaf's
# authority key identifier is the authority's subject key identifier.
# Python 3.13 turned on ssl.VERIFY_X509_STRICT by default, and a leaf
# with no authority key identifier fails that check with "Missing
# Authority Key Identifier", which looks exactly like a cluster that
# does not trust the authority.
echo "Issuing the provider's certificate on ${primary} (${SAN})"
on_primary \
    "mkdir -p '${remote_state}' && chmod 0700 '${remote_state}' && \
     openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 1 \
         -CA '${CA_DIR}/ca-cert.pem' -CAkey '${CA_DIR}/ca-key.pem' \
         -keyout '${remote_state}/tls.key' -out '${remote_state}/tls.crt' \
         -subj '/CN=${primary}' \
         -addext 'subjectAltName=${SAN}' \
         -addext 'basicConstraints=critical,CA:FALSE' \
         -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
         -addext 'extendedKeyUsage=serverAuth' && \
     chmod 0644 '${remote_state}/tls.crt' '${remote_state}/tls.key' && \
     openssl x509 -in '${remote_state}/tls.crt' -noout \
         -subject -issuer -dates -ext subjectAltName"

# The container's environment.
#
# KC_HOSTNAME is what decides the issuer URL and every endpoint in the
# discovery document, so it has to be the address the cluster will use.
# KC_HTTP_ENABLED is false because start-dev serves http as well
# otherwise, and an issuer reachable over plaintext is an issuer
# something will eventually reach that way.
printf '%s\n' \
    "KC_BOOTSTRAP_ADMIN_USERNAME=${ADMIN_USER}" \
    "KC_BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_PASSWORD}" \
    "KC_HOSTNAME=${KEYCLOAK_URL}" \
    "KC_HTTP_ENABLED=false" \
    "KC_HTTPS_PORT=${IDP_PORT}" \
    "KC_HTTPS_CERTIFICATE_FILE=/opt/keycloak/conf/tls.crt" \
    "KC_HTTPS_CERTIFICATE_KEY_FILE=/opt/keycloak/conf/tls.key" \
    > "${workdir}/container.env"
to_primary "${remote_state}/container.env" < "${workdir}/container.env"

# Docker, installed from Debian's own archive rather than Docker's
# repository: this needs a container runtime, not a particular one, and
# adding a third party apt source to a node whose whole life is one
# workflow run buys nothing.
#
# Configured before it is installed, which is the order that matters
# and not a tidiness: installing the package starts the daemon, and a
# daemon that has already started has already inserted its rules. See
# this script's header for what was measured.
#
# Every docker command goes through sudo. Adding the ssh user to the
# docker group would need a new login session to take effect, which is
# more moving parts than prefixing six commands.
#
# The remote script is single quoted, so no apostrophes in its
# comments: the shell would end the quote.
echo "Ensuring Docker is installed on ${primary}"
on_primary '
set -e

# Told to add no iptables rules at all, which it does not need: the one
# container this node runs is in the host network namespace, so there
# is no address translation to arrange.
if [ ! -e /etc/docker/daemon.json ]; then
    sudo mkdir -p /etc/docker
    printf "%s\n" "{\"iptables\": false}" | \
        sudo tee /etc/docker/daemon.json > /dev/null
    echo "  wrote /etc/docker/daemon.json with iptables disabled"
    # A daemon which is somehow already running has started without
    # this, so it is restarted; one which is not is left to the install
    # below, or to the start at the end, either of which reads the file
    # as it now is.
    if sudo systemctl is-active --quiet docker; then
        sudo systemctl restart docker
    fi
elif ! sudo grep -q "\"iptables\"[[:space:]]*:[[:space:]]*false" \
        /etc/docker/daemon.json; then
    echo "  NOTE: this node already has /etc/docker/daemon.json, so its"
    echo "  Docker manages its own iptables rules. That file is left"
    echo "  alone rather than overwritten. The container below still"
    echo "  uses the host network namespace, so nothing needs a"
    echo "  translated port, but Docker will have inserted its chains"
    echo "  into the forwarding path of a node which forwards instance"
    echo "  traffic. If this lane starts failing on instance"
    echo "  networking, start here."
fi

# Each package named explicitly. Debian split the client out of
# docker.io at 26.1.4 -- docker-cli Replaces docker.io (<< 26.1.4) --
# and docker.io only Recommends it, so with --no-install-recommends the
# daemon installs and the docker command does not exist. The first run
# of this script on a cluster node died at the version check below with
# "sudo: docker: command not found"; a development host already has a
# client, so it never reaches this branch and the gap did not show
# until CI ran it. ca-certificates is on the same Recommends list and
# the pull below needs it to verify the registry over TLS, so it is
# named here rather than relied upon. The flag stays, because the rest
# of that Recommends list is needrestart, criu, git, apparmor and more,
# and none of it belongs on a CI node about to be destroyed.
#
# No apostrophes in this comment. The whole block is one single-quoted
# string sent over ssh, so a prose apostrophe ends the string and runs
# everything after it on the runner instead of the node.
if ! command -v docker > /dev/null 2>&1; then
    echo "  installing docker.io, docker-cli and ca-certificates"
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
        --no-install-recommends docker.io docker-cli ca-certificates
fi
sudo systemctl is-active --quiet docker || sudo systemctl start docker
sudo docker version --format "  docker {{.Server.Version}}"'

# A pinned tag, pulled explicitly, so that a tag which has been
# withdrawn or renamed fails here saying so rather than at a TLS error
# twenty minutes later. There is deliberately no fallback to latest: a
# provider version nothing recorded is a test result nothing can
# reproduce.
echo "Starting ${IMAGE} on ${primary}"
on_primary "sudo docker pull '${IMAGE}'" || \
    fail "could not pull ${IMAGE} on ${primary}. The tag is pinned to the" \
         "one tools/idp-lab/docker-compose.yml serves the committed" \
         "provider captures from, and this script will not substitute" \
         "another one."

# Replaced rather than reused, because a container left by an earlier
# run of this script has an earlier run's certificate and an earlier
# run's administrative password.
#
# --restart no: a provider that keeps restarting under a configuration
# error looks, from the tests, exactly like a provider that is slow.
on_primary \
    "sudo docker rm --force '${CONTAINER}' > /dev/null 2>&1 || true; \
     sudo docker run --detach --name '${CONTAINER}' \
         --network host --memory '${MEMORY}' --restart no \
         --env-file '${remote_state}/container.env' \
         --volume '${remote_state}/tls.crt:/opt/keycloak/conf/tls.crt:ro' \
         --volume '${remote_state}/tls.key:/opt/keycloak/conf/tls.key:ro' \
         '${IMAGE}' start-dev > /dev/null && \
     echo '  started ${CONTAINER}'"

# curl, as it is run on the primary: verifying against the authority
# the cluster trusts and nothing else. Verifying rather than passing
# --insecure is the point -- a configuration step that would have been
# happy with any certificate proves nothing about the one the cluster
# has to accept.
REMOTE_CURL="curl --silent --show-error --max-time 30"
REMOTE_CURL="${REMOTE_CURL} --cacert '${CA_DIR}/ca-cert.pem'"

# One member of a JSON object on stdin, with no jq dependency: this
# repository already requires python3 everywhere and does not require
# jq anywhere.
json_member() {
    python3 -I -c '
import json
import sys

document = json.load(sys.stdin)
value = document.get(sys.argv[1], "")
if isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print("" if value is None else value)
' "$@"
}

# The master realm answers as soon as the provider is listening, so
# this waits on a document rather than sleeping on a guess -- and it is
# the first thing in this script to prove the whole TLS path at once:
# the container is up, the leaf chains to the authority the cluster
# trusts, and its subjectAltName matches the address the cluster will be
# given, because that address is the one being asked for here.
#
# Not REMOTE_CURL: a provider which is still starting refuses the
# connection every five seconds, and --show-error would report each of
# those as though it were a problem.
MASTER_DOC="${KEYCLOAK_URL}/realms/master/.well-known/openid-configuration"

echo "Waiting up to 300s for the provider to answer ${KEYCLOAK_URL}"
waited=0
until on_primary "curl --silent --fail --max-time 10 \
        --cacert '${CA_DIR}/ca-cert.pem' --output /dev/null \
        '${MASTER_DOC}'"; do
    waited=$(( waited + 5 ))
    if [ "${waited}" -ge 300 ]; then
        on_primary "sudo docker logs --tail 200 '${CONTAINER}'" || true
        fail "the provider did not answer ${KEYCLOAK_URL} within 300s," \
             "asked from ${primary} itself. The container log is above."
    fi
    sleep 5
done
echo "  the provider answered after about ${waited}s"

# -- the administrative configuration -------------------------------
#
# What this creates, and why each piece is needed, is
# tools/idp-lab/configure-keycloak.sh's header at greater length. In
# short: a realm, whose name is the last element of the issuer URL; a
# group, membership of which is what a mapping rule binds; a public
# client, because the thing registering is a command line program on a
# person's laptop and a confidential client's secret would have to be
# handed to everyone who logs in; a group membership mapper on that
# client's own dedicated scope with id.token.claim on, because Keycloak
# ships no group claim at all and a mapper pointed only at the access
# token leaves the claim visible at /userinfo while every login is
# refused -- the identity token is the only token Shaken Fist sees; and
# a person in that group with a verified email and a non-temporary
# password, either alternative being a required action that stops the
# login on an extra screen before any token is issued.
#
# Two settings are deliberately absent, both from the same lesson. The
# client's PKCE method is left unset, because setting it to S256 makes
# Keycloak require a challenge on every request including the device
# authorization request, which has nowhere to put one. And the login
# asks for "openid profile" rather than naming a groups scope, because
# Keycloak has no groups scope and refuses the request outright.
#
# One setting is here that the lab does not have:
# directAccessGrantsEnabled. The lab is driven by a person in a
# browser, and a test that wants an identity token to hand to
# /auth/login without playing out a whole interactive flow asks for one
# directly.

TOKEN=''

# How every administrative request below presents that token: a curl
# configuration file on the primary holding the whole header, rather
# than -H with the token interpolated into it.
#
# The same rule as the two passwords, and for the same reason -- a
# bearer token for the master realm's admin-cli client is a credential,
# and a credential in a command line is readable in ps for the life of
# the request by anything else on the host, and is in the ssh command
# line on the runner as well. The passwords were already kept out this
# way and this was not, which left the script enforcing for two of its
# three credentials a rule it states as absolute.
#
# curl's --config reads "header = ..." as the long form of -H, and the
# file is removed with the grant files once the requests which read it
# are done.
REMOTE_AUTH=''

# A read, whose document comes back over ssh to be parsed here.
kc_get() {
    on_primary "${REMOTE_CURL} ${REMOTE_AUTH} '${KEYCLOAK_URL}$1'"
}

# A write, printing the HTTP status so the caller can tell "created"
# from "already there" from "refused" rather than guessing from an
# empty body.
#
# The body is single quoted for the primary's shell, which every body
# below is safe for: they are JSON, so they hold double quotes and no
# single ones. A body which would carry a credential is written to a
# file on the primary instead and named here as "@path", which curl
# reads as a file -- so no credential is in any command line, on either
# host.
kc_write() {
    local method="$1" path="$2" body="${3:-}" data=''

    if [ -n "${body}" ]; then
        data="--data '${body}'"
    fi
    on_primary "${REMOTE_CURL} --output /dev/null \
        --write-out '%{http_code}' --request ${method} ${REMOTE_AUTH} \
        -H 'Content-Type: application/json' ${data} \
        '${KEYCLOAK_URL}${path}'"
}

# Created, or already present, and anything else is a failure. A realm
# left half configured because a 400 went unnoticed is a login which
# fails much later for no visible reason. Every run normally reports
# everything as created, because every run gets a new container; the
# "already there" arm is for a provider somebody is configuring a
# second time by hand.
kc_expect_created() {
    local what="$1" status="$2"

    case "${status}" in
        201|204) echo "  created ${what}" ;;
        409) echo "  ${what} is already there" ;;
        *) fail "creating ${what} answered HTTP ${status}" ;;
    esac
}

# The administrative token, through the master realm's admin-cli
# client. The password goes to a file on the primary and is read from
# there by curl, so it is in no command line on either host; the same
# is done below for the person's.
#
# The file holds the value alone: curl's name@file form takes the whole
# file as the value, so a file with "password=" in it sends that as
# part of the password.
echo 'Getting an administrative token'
printf '%s' "${ADMIN_PASSWORD}" > "${workdir}/admin-grant"
to_primary "${remote_state}/admin-grant" < "${workdir}/admin-grant" \
    > /dev/null
TOKEN=$(on_primary "${REMOTE_CURL} --request POST \
    '${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token' \
    --data-urlencode 'client_id=admin-cli' \
    --data-urlencode 'grant_type=password' \
    --data-urlencode 'username=${ADMIN_USER}' \
    --data-urlencode 'password@${remote_state}/admin-grant'" | \
    json_member access_token)
[ -n "${TOKEN}" ] || \
    fail "could not get an administrative token from ${KEYCLOAK_URL}"

# Written through stdin under a umask, exactly as the passwords are, so
# the token reaches the primary without passing through a command line
# on either host. Quoted in the file because a curl configuration file
# otherwise ends the value at the first space, and "Bearer x" has one.
printf 'header = "Authorization: Bearer %s"\n' "${TOKEN}" \
    > "${workdir}/admin-auth"
to_primary "${remote_state}/admin-auth" < "${workdir}/admin-auth" \
    > /dev/null
REMOTE_AUTH="--config '${remote_state}/admin-auth'"

echo "Configuring the ${REALM} realm"
kc_expect_created "realm ${REALM}" \
    "$(kc_write POST /admin/realms \
        "{\"realm\": \"${REALM}\", \"enabled\": true}")"

kc_expect_created "group ${GROUP}" \
    "$(kc_write POST "/admin/realms/${REALM}/groups" \
        "{\"name\": \"${GROUP}\"}")"

# redirectUris is http://127.0.0.1:* because the client binds an
# ephemeral loopback port for the browser flow, so the redirect URI it
# sends cannot be registered exactly, and Keycloak's trailing wildcard
# matches it. No CI test drives that flow -- the primary has no browser
# -- but registering it costs nothing and keeps this client
# interchangeable with the lab's, so a person debugging a CI failure on
# their own machine can use either.
kc_expect_created "client ${CLIENT_ID}" "$(kc_write POST \
    "/admin/realms/${REALM}/clients" "$(cat <<JSON
{
  "clientId": "${CLIENT_ID}",
  "name": "Shaken Fist command line client",
  "enabled": true,
  "publicClient": true,
  "standardFlowEnabled": true,
  "directAccessGrantsEnabled": true,
  "serviceAccountsEnabled": false,
  "redirectUris": ["http://127.0.0.1:*"],
  "webOrigins": [],
  "attributes": {"oauth2.device.authorization.grant.enabled": "true"}
}
JSON
)")"

CLIENT_UUID=$(kc_get "/admin/realms/${REALM}/clients?clientId=${CLIENT_ID}" | \
    python3 -I -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')
MAPPERS="/admin/realms/${REALM}/clients/${CLIENT_UUID}/protocol-mappers/models"

# full.path writes "/sf-admins" rather than "sf-admins", and it is set
# explicitly because the two ways of creating this mapper disagree
# about its default: the console submits true, while a POST which omits
# the key behaves as though it were false. The leading slash is a path,
# so a group nested under another is "/parent/child", which is the real
# reason to keep full paths on -- and whichever form is chosen, a
# mapping rule has to bind the string the token carries. The descriptor
# this script writes carries that string rather than the group's name,
# for exactly that reason.
#
# claim.name is ${GROUP_CLAIM_NAME} and so is the descriptor's
# group_claim, from the one variable: Keycloak ships no group claim at
# all, so this name is a choice this script made and a test binding a
# rule has to be told it rather than assume it.
if kc_get "${MAPPERS}" | \
        grep -q "\"name\"[ ]*:[ ]*\"${GROUP_CLAIM_NAME}\""; then
    echo "  the ${GROUP_CLAIM_NAME} mapper is already there"
else
    kc_expect_created "the ${GROUP_CLAIM_NAME} mapper" \
        "$(kc_write POST "${MAPPERS}" "$(cat <<JSON
{
  "name": "${GROUP_CLAIM_NAME}",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-group-membership-mapper",
  "config": {"claim.name": "${GROUP_CLAIM_NAME}",
             "full.path": "true",
             "id.token.claim": "true",
             "access.token.claim": "true",
             "userinfo.token.claim": "true",
             "introspection.token.claim": "true"}
}
JSON
)")"
fi

# The person, written to a file on the primary and handed to curl as a
# file, so that the password is in no command line. The document is
# built by python3 rather than printf so that a generated password
# containing a character JSON would have to escape produces a document
# that parses rather than one that nearly does. The values arrive in
# python's environment for the same reason: not on its command line.
person="${PERSON}" email="${PERSON_EMAIL}" first="${PERSON_FIRST}" \
    last="${PERSON_LAST}" password="${PERSON_PASSWORD}" \
    python3 -I -c '
import json
import os
import sys

json.dump({"username": os.environ["person"],
           "enabled": True,
           "emailVerified": True,
           "email": os.environ["email"],
           "firstName": os.environ["first"],
           "lastName": os.environ["last"],
           "credentials": [{"type": "password",
                            "value": os.environ["password"],
                            "temporary": False}]}, sys.stdout)
' > "${workdir}/person.json"
to_primary "${remote_state}/person.json" < "${workdir}/person.json" \
    > /dev/null

kc_expect_created "person ${PERSON}" "$(kc_write POST \
    "/admin/realms/${REALM}/users" "@${remote_state}/person.json")"

USER_UUID=$(kc_get \
    "/admin/realms/${REALM}/users?username=${PERSON}&exact=true" | \
    python3 -I -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')
GROUP_UUID=$(kc_get "/admin/realms/${REALM}/groups?search=${GROUP}" | \
    python3 -I -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])')

kc_expect_created "${PERSON}'s membership of ${GROUP}" \
    "$(kc_write PUT \
        "/admin/realms/${REALM}/users/${USER_UUID}/groups/${GROUP_UUID}")"

# -- the proofs -----------------------------------------------------

# A device code request is the cheapest proof that the client attribute
# took effect: without it this endpoint answers 400 with "Client is not
# allowed to initiate OAuth 2.0 Device Authorization Grant", which is
# clearer than anything the cluster or a test can say afterwards. Note
# that the endpoint is advertised in the discovery document per *realm*
# whether or not any client can use the grant, so the document is not
# the thing to check.
echo 'Checking the device grant is enabled on the client'
device=$(on_primary "${REMOTE_CURL} --request POST \
    '${ISSUER_URL}/protocol/openid-connect/auth/device' \
    --data-urlencode 'client_id=${CLIENT_ID}' \
    --data-urlencode 'scope=${SCOPES}'")
[ -n "$(printf '%s' "${device}" | json_member user_code)" ] || \
    fail "the device endpoint refused a code request: ${device}"
echo '  the device endpoint issued a user code'

# The direct access grant, and then the identity token it returns,
# which is the one configuration failure this script could otherwise
# hand on silently. A group membership mapper pointed at the access
# token and not the identity token leaves every one of these checks
# passing and every login refused, because the identity token is the
# only token Shaken Fist sees. So the claim is read from the identity
# token here, before any test depends on it.
#
# The token is decoded and not verified. Verifying it is the cluster's
# job and the point of the tests this script sets up; what is being
# checked here is a configuration, not a signature.
echo 'Checking the identity token carries the group claim'
printf '%s' "${PERSON_PASSWORD}" > "${workdir}/person-grant"
to_primary "${remote_state}/person-grant" < "${workdir}/person-grant" \
    > /dev/null
on_primary "${REMOTE_CURL} --request POST \
    '${ISSUER_URL}/protocol/openid-connect/token' \
    --data-urlencode 'client_id=${CLIENT_ID}' \
    --data-urlencode 'grant_type=password' \
    --data-urlencode 'scope=${SCOPES}' \
    --data-urlencode 'username=${PERSON}' \
    --data-urlencode 'password@${remote_state}/person-grant'" | \
    python3 -I -c '
import base64
import json
import sys

claim, group = sys.argv[1], sys.argv[2]
document = json.load(sys.stdin)
if "id_token" not in document:
    sys.exit("the token endpoint returned no id_token, but %s"
             % sorted(document))

payload = document["id_token"].split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(
    payload + "=" * (-len(payload) % 4)))
if group not in claims.get(claim, []):
    sys.exit("the identity token carries %s=%r, not %r. The group "
             "membership mapper is not writing the identity token, so "
             "every login would be refused while /userinfo looked right."
             % (claim, claims.get(claim), group))
print("  %s logged in and the identity token carries %s %s"
      % (claims.get("preferred_username"), claim, group))
' "${GROUP_CLAIM_NAME}" "${GROUP_CLAIM}"

# The discovery document, read as a document. Every endpoint the login
# flows need has to be in it, and the device authorization endpoint in
# particular is published twice by this provider -- the second time
# inside mtls_endpoint_aliases -- so it is read by name and not counted.
echo 'Checking the discovery document'
on_primary "${REMOTE_CURL} \
    '${ISSUER_URL}/.well-known/openid-configuration'" | \
    python3 -I -c '
import json
import sys

expected = sys.argv[1]
document = json.load(sys.stdin)
if document.get("issuer") != expected:
    sys.exit("the provider says its issuer is %r, not %r. Nothing can "
             "create a trusted issuer for it until KC_HOSTNAME and the "
             "address the cluster is given agree."
             % (document.get("issuer"), expected))

missing = [endpoint for endpoint in ("authorization_endpoint",
                                     "token_endpoint", "jwks_uri",
                                     "device_authorization_endpoint")
           if not document.get(endpoint)]
if missing:
    sys.exit("the discovery document is missing %s" % ", ".join(missing))

plaintext = sorted(name for name, value in document.items()
                   if isinstance(value, str) and value.startswith("http:"))
if plaintext:
    sys.exit("the discovery document advertises plaintext %s, which the "
             "cluster refuses" % ", ".join(plaintext))
print("  %s publishes all four endpoints over https" % document["issuer"])
' "${ISSUER_URL}"

# The four files whose only purpose was to keep a credential out of a
# command line have no reason to outlive the requests that read them.
# The administrative password does stay on the node, in the container's
# environment file, because the container needs it for as long as it
# runs; the person's stays in the descriptor, because the tests do.
on_primary "rm -f '${remote_state}/admin-grant' \
    '${remote_state}/admin-auth' \
    '${remote_state}/person-grant' '${remote_state}/person.json'"

# And nothing below makes an administrative request, so the variable
# which names the file that has just gone is emptied rather than left
# pointing at it: a request added after this point fails as
# unauthenticated instead of failing to find a configuration file.
REMOTE_AUTH=''

# -- the descriptor -------------------------------------------------
#
# Built with python3 rather than printf, for the reason the person
# above was, and from python's environment rather than its command
# line for the other one.
issuer_url="${ISSUER_URL}" client_id="${CLIENT_ID}" \
    oidc_scopes="${SCOPES}" username="${PERSON}" \
    password="${PERSON_PASSWORD}" group_claim="${GROUP_CLAIM_NAME}" \
    group="${GROUP_CLAIM}" image="${IMAGE}" \
    python3 -I -c '
import json
import os
import sys

json.dump({"issuer_url": os.environ["issuer_url"],
           "client_id": os.environ["client_id"],
           "oidc_scopes": os.environ["oidc_scopes"],
           "username": os.environ["username"],
           "password": os.environ["password"],
           "group_claim": os.environ["group_claim"],
           "group": os.environ["group"],
           "image": os.environ["image"]}, sys.stdout, indent=2)
sys.stdout.write("\n")
' > "${workdir}/descriptor.json"

# Narrower than anything else this script leaves behind, because this
# is the file the tests read and it holds the person's password.
echo "Writing the descriptor to ${primary}:${remote_descriptor}"
to_primary "${remote_descriptor}" 0400 < "${workdir}/descriptor.json"

# -- what the cluster actually needs --------------------------------
#
# Everything above was asked on the primary, of the primary. What the
# tests depend on is that sf-api can fetch this provider's documents,
# and sf-api runs on every node and trusts exactly one bundle. So ask
# each node the question sf-api will ask: fetch the discovery document
# with the installed bundle as the only trust anchor.
#
# Every node, not just the one the suite talks to, for the reason
# tools/ci-jwks-ca.sh installs the bundle everywhere: api_url defaults
# to the primary today, and a topology which later points it elsewhere
# should not silently go back to skipping.
#
# ssh -n inside the loop: without it the first ssh reads the rest of
# the node list off this loop's stdin and the loop runs once.
echo 'Checking every node can fetch the discovery document'
while read -r node address; do
    [ -n "${node}" ] || continue
    ssh -n ${SSH_OPTS} "${SSH_USER}@${address}" \
        "test -r '${BUNDLE_PATH}'" || \
        fail "${node} (${address}) has no readable ${BUNDLE_PATH}." \
             "tools/ci-jwks-ca.sh installs it and must run before this" \
             "script."
    ssh -n ${SSH_OPTS} "${SSH_USER}@${address}" \
        "curl --silent --fail --max-time 10 --cacert '${BUNDLE_PATH}' \
             --output /dev/null \
             '${ISSUER_URL}/.well-known/openid-configuration'" || \
        fail "${node} (${address}) cannot fetch ${ISSUER_URL} with" \
             "${BUNDLE_PATH} as its only trust anchor, which is the" \
             "fetch sf-api has to make. Either the certificate does not" \
             "chain to the installed bundle, or its subjectAltName does" \
             "not cover ${primary}, or the node cannot reach port" \
             "${IDP_PORT}."
    echo "  ${node} (${address}) fetched it"
done <<< "${node_addresses}"

# -- evidence -------------------------------------------------------
#
# Installing a container runtime on a node which is a hypervisor and
# the cluster's network node is the one thing in this script that
# cannot be settled by reasoning, so it is recorded rather than
# assumed. If this lane starts failing on instance networking, this is
# the output to read first, and the answer is to move the provider to a
# node which is not the network node rather than to loosen anything.
echo "Recording what Docker did to ${primary}'s networking"
on_primary '
echo "  links:"
ip -brief link show | sed "s/^/    /"
echo "  FORWARD policy and rules:"
sudo iptables -S FORWARD 2>&1 | sed "s/^/    /" | head -20
echo "  nat rules:"
sudo iptables -t nat -S 2>&1 | grep -c . | sed "s/^/    /"' || true

# And sf-api, which is the service the whole suite goes through. It was
# running before this script started; a Docker install that broke the
# node should fail here rather than in a test whose name has nothing to
# do with it.
on_primary \
    "curl -sf --max-time 10 http://localhost:13000/readyz > /dev/null" || \
    fail "sf-api on ${primary} is no longer ready. It was before this" \
         "script ran, so suspect the Docker install: see the links and" \
         "FORWARD rules above."
echo "  sf-api on ${primary} is still ready"

echo 'Done. The provider is up and the descriptor is written:'
echo "  issuer_url   ${ISSUER_URL}"
echo "  client_id    ${CLIENT_ID}"
echo "  oidc_scopes  ${SCOPES}"
echo "  username     ${PERSON}, password in ${remote_descriptor}"
echo "  group claim  \"${GROUP_CLAIM_NAME}\": [\"${GROUP_CLAIM}\"]"
echo "  image        ${IMAGE}"
