#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-test-internal-ca.sh -- drive the internal_ca role's bootstrap,
# host_certificate and distribute_certificates entry points against
# localhost, and check what they produce with openssl.
#
# It proves that a certificate is issued with the requested name, SANs,
# lifetime and modes; that a rerun changes nothing; that a second
# certificate with its own cert_name leaves the first alone; and that a
# certificate is reissued, with the same key, when it nears expiry or its
# template changes -- and not otherwise, even when the run that saw the
# change is interrupted; and that a mode or certificate name which would be
# misread is refused. Before this existed nothing ran
# the role's renewal path at all, and it had never worked.
#
# It runs without root, which is a shape the role must support anyway:
# sfcbr deploys from an unprivileged control node. -e ansible_become=false
# outranks the key slurp's become: true, and the only two tasks which need
# root (the apt install and /etc/pki/CA, which nothing reads) are skipped
# by tag. Everything is written under a temporary directory which is
# removed on exit; the working tree is used in place via a symlinked
# collections path, so this tests the role as checked out.
#
# Run from anywhere, with ansible-playbook, certtool and openssl on PATH:
#
#     tools/ci-test-internal-ca.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COLLECTION_DIR="${REPO_ROOT}/shakenfist/deploy/collection"

CURRENT_CASE='setup'
LAST_LOG=''

fail() {
    echo "FAIL [${CURRENT_CASE}]: $*" >&2
    if [ -n "${LAST_LOG}" ] && [ -f "${LAST_LOG}" ]; then
        echo "---- last 40 lines of ${LAST_LOG##*/} ----" >&2
        tail -n 40 "${LAST_LOG}" >&2
    fi
    exit 1
}

need() {
    command -v "$1" > /dev/null || fail "$1 not found on PATH: $2"
}

need certtool 'install the gnutls-bin package'
need openssl 'install the openssl package'
need ansible-playbook 'install ansible-core (the sanity job gets it with ansible-lint)'

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ci-test-internal-ca.XXXXXX")"
trap 'rm -rf "${WORK}"' EXIT

CA="${WORK}/ca"
DEST="${WORK}/dest"
mkdir -p "${WORK}/collections/ansible_collections/shakenfist" "${WORK}/logs" \
    "${WORK}/ansible-tmp"
ln -s "${COLLECTION_DIR}" "${WORK}/collections/ansible_collections/shakenfist/shakenfist"

# An empty config, so neither a developer's ~/.ansible.cfg nor an
# installed copy of the collection changes what runs.
: > "${WORK}/ansible.cfg"
export ANSIBLE_CONFIG="${WORK}/ansible.cfg"
export ANSIBLE_COLLECTIONS_PATH="${WORK}/collections"
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false
export ANSIBLE_LOCAL_TEMP="${WORK}/ansible-tmp"
export ANSIBLE_REMOTE_TMP="${WORK}/ansible-tmp"
export ANSIBLE_NOCOLOR=1
export ANSIBLE_STDOUT_CALLBACK=default

cat > "${WORK}/play.yml" << 'EOF'
---
- name: Exercise the internal_ca role
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Bootstrap the CA
      ansible.builtin.include_role:
        name: shakenfist.shakenfist.internal_ca
        tasks_from: bootstrap
    - name: Issue the host certificate
      ansible.builtin.include_role:
        name: shakenfist.shakenfist.internal_ca
        tasks_from: host_certificate
    - name: Distribute the certificates
      ansible.builtin.include_role:
        name: shakenfist.shakenfist.internal_ca
        tasks_from: distribute_certificates
EOF

# Every run gets these. Only hostname and the paths are set, so the
# first certificate is issued with the role's defaults.
cat > "${WORK}/common.json" << EOF
{
  "hostname": "testhost",
  "ca_path": "${CA}",
  "cert_dest_dir": "${DEST}/testhost",
  "cert_owner": "$(id -un)",
  "cert_group": "$(id -gn)"
}
EOF

# A second certificate on the same host, as the Kerbside proxy will have.
write_kerbside_vars() {
    cat > "${WORK}/kerbside.json" << EOF
{
  "cert_name": "testhost-kerbside",
  "cert_cn": "console.example.com",
  "cert_san_dns": [$1],
  "cert_san_ip": ["192.0.2.10"],
  "cert_dest_dir": "${DEST}/kerbside",
  "cert_key_mode": "0400"
}
EOF
}
write_kerbside_vars '"console.example.com"'

HOST_CERT="${CA}/testhost-server-cert.pem"
HOST_KEY="${CA}/testhost-server-key.pem"
KB_CERT="${CA}/testhost-kerbside-server-cert.pem"
KB_KEY="${CA}/testhost-kerbside-server-key.pem"
CA_CERT="${CA}/sf-ca-cert.pem"

RUNS=0
# run_play [extra vars file...]: run the playbook, leaving the PLAY RECAP's
# changed= count in CHANGED.
run_play() {
    local args=(-e "@${WORK}/common.json")
    local f
    for f in "$@"; do
        args+=(-e "@${f}")
    done
    RUNS=$((RUNS + 1))
    LAST_LOG="${WORK}/logs/run-${RUNS}.log"
    if ! ansible-playbook -i localhost, -c local -e ansible_become=false \
            --skip-tags packages,system-dirs "${args[@]}" "${WORK}/play.yml" \
            < /dev/null > "${LAST_LOG}" 2>&1; then
        fail 'ansible-playbook failed'
    fi
    CHANGED="$(sed -n 's/^localhost .* changed=\([0-9]*\) .*/\1/p' "${LAST_LOG}")"
    [ -n "${CHANGED}" ] || fail 'no PLAY RECAP in the ansible output'
}

# run_play_fails PATTERN [extra vars file...]: run the playbook, expecting
# it to fail with output matching the extended regex PATTERN.
run_play_fails() {
    local pattern="$1"
    shift
    local args=(-e "@${WORK}/common.json")
    local f
    for f in "$@"; do
        args+=(-e "@${f}")
    done
    RUNS=$((RUNS + 1))
    LAST_LOG="${WORK}/logs/run-${RUNS}.log"
    if ansible-playbook -i localhost, -c local -e ansible_become=false \
            --skip-tags packages,system-dirs "${args[@]}" "${WORK}/play.yml" \
            < /dev/null > "${LAST_LOG}" 2>&1; then
        fail "ansible-playbook succeeded, expected a failure matching '${pattern}'"
    fi
    grep -qE "${pattern}" "${LAST_LOG}" || fail "the failure does not match '${pattern}'"
}

serial() { openssl x509 -noout -serial -in "$1"; }
fingerprint() { openssl x509 -noout -fingerprint -sha256 -in "$1"; }
pubkey() { openssl x509 -noout -pubkey -in "$1"; }
cn() { openssl x509 -noout -subject -nameopt RFC2253 -in "$1" | sed -n 's/.*CN=\([^,]*\).*/\1/p'; }
sans() { openssl x509 -noout -ext subjectAltName -in "$1" 2>&1; }

assert_mode() {
    local mode
    mode="$(stat -c '%a %U' "$1")"
    [ "${mode}" = "$2 $(id -un)" ] || fail "$1 is '${mode}', expected '$2 $(id -un)'"
}

# Valid for (about) a year from now: still valid in 364 days, not in 366.
assert_year_lifetime() {
    openssl x509 -noout -checkend $((364 * 86400)) -in "$1" > /dev/null \
        || fail "$1 expires within 364 days"
    if openssl x509 -noout -checkend $((366 * 86400)) -in "$1" > /dev/null; then
        fail "$1 is still valid in 366 days"
    fi
}

assert_verifies() {
    openssl verify -CAfile "${CA_CERT}" "$1" > /dev/null || fail "$1 does not verify against the CA"
}

# The only set-aside copy of $1, named $1.<14 digit UTC timestamp>.
set_aside() {
    local found=()
    local f
    for f in "$1".*; do
        [ -e "${f}" ] && found+=("${f}")
    done
    [ "${#found[@]}" -eq 1 ] || fail "expected one set-aside copy of $1, found ${#found[@]}"
    [[ "${found[0]}" =~ \.[0-9]{14}$ ]] || fail "set-aside copy ${found[0]} is not <path>.<timestamp>"
    echo "${found[0]}"
}

case_1_fresh_issue() {
    CURRENT_CASE='case 1: fresh issue with defaults'
    run_play
    [ -f "${CA_CERT}" ] || fail 'no CA certificate'
    [ -f "${HOST_CERT}" ] || fail 'no host certificate'
    assert_verifies "${HOST_CERT}"
    [ "$(cn "${HOST_CERT}")" = 'testhost' ] || fail "CN is '$(cn "${HOST_CERT}")', expected testhost"
    if sans "${HOST_CERT}" | grep -q 'Subject Alternative Name'; then
        fail 'the default certificate has a SAN'
    fi
    assert_year_lifetime "${HOST_CERT}"
    assert_mode "${DEST}/testhost/ca-cert.pem" 444
    assert_mode "${DEST}/testhost/server-cert.pem" 444
    assert_mode "${DEST}/testhost/server-key.pem" 444
    cmp -s "${HOST_CERT}" "${DEST}/testhost/server-cert.pem" || fail 'installed certificate differs'
    cmp -s "${HOST_KEY}" "${DEST}/testhost/server-key.pem" || fail 'installed key differs'
    cmp -s "${CA_CERT}" "${DEST}/testhost/ca-cert.pem" || fail 'installed CA certificate differs'
}

case_2_idempotent() {
    CURRENT_CASE='case 2: an identical rerun changes nothing'
    local before_serial before_fp
    before_serial="$(serial "${HOST_CERT}")"
    before_fp="$(fingerprint "${HOST_CERT}")"
    run_play
    [ "${CHANGED}" -eq 0 ] || fail "the rerun reported changed=${CHANGED}"
    [ "$(serial "${HOST_CERT}")" = "${before_serial}" ] || fail 'the serial changed'
    [ "$(fingerprint "${HOST_CERT}")" = "${before_fp}" ] || fail 'the fingerprint changed'
}

case_3_sans_and_stem() {
    CURRENT_CASE='case 3: SANs and a distinct cert_name'
    local before_fp
    before_fp="$(fingerprint "${HOST_CERT}")"
    run_play "${WORK}/kerbside.json"
    [ -f "${KB_CERT}" ] || fail 'no second certificate'
    assert_verifies "${KB_CERT}"
    [ "$(cn "${KB_CERT}")" = 'console.example.com' ] || fail "CN is '$(cn "${KB_CERT}")'"
    sans "${KB_CERT}" | grep -q 'DNS:console.example.com' || fail 'DNS SAN missing'
    sans "${KB_CERT}" | grep -q 'IP Address:192.0.2.10' || fail 'IP SAN missing'
    [ "$(fingerprint "${HOST_CERT}")" = "${before_fp}" ] || fail 'the first certificate changed'
    [ "$(pubkey "${KB_CERT}")" != "$(pubkey "${HOST_CERT}")" ] || fail 'both certificates share a key'
    cmp -s "${HOST_CERT}" "${DEST}/testhost/server-cert.pem" || fail 'the first installed certificate changed'
    assert_mode "${DEST}/kerbside/server-key.pem" 400
    cmp -s "${KB_CERT}" "${DEST}/kerbside/server-cert.pem" || fail 'installed certificate differs'
    cmp -s "${KB_KEY}" "${DEST}/kerbside/server-key.pem" || fail 'installed key differs'
}

case_4_renew_on_expiry() {
    CURRENT_CASE='case 4: renewal by expiry'
    # Reissue the host certificate from its own template and key, but for one
    # day, so only the expiry check can trigger a renewal.
    sed 's/^expiration_days = .*/expiration_days = 1/' "${CA}/testhost-server-template.pem" \
        > "${WORK}/short-template.pem"
    rm -f "${HOST_CERT}"
    certtool --generate-certificate --template "${WORK}/short-template.pem" \
        --load-privkey "${HOST_KEY}" --load-ca-certificate "${CA_CERT}" \
        --load-ca-privkey "${CA}/sf-ca-key.pem" --outfile "${HOST_CERT}" > /dev/null 2>&1 \
        || fail 'certtool could not issue the short-lived certificate'
    cp "${HOST_CERT}" "${WORK}/short-cert.pem"
    local short_serial before_pub
    short_serial="$(serial "${HOST_CERT}")"
    before_pub="$(pubkey "${HOST_CERT}")"

    run_play
    [ "$(serial "${HOST_CERT}")" != "${short_serial}" ] || fail 'the certificate was not reissued'
    assert_verifies "${HOST_CERT}"
    assert_year_lifetime "${HOST_CERT}"
    [ "$(pubkey "${HOST_CERT}")" = "${before_pub}" ] || fail 'the reissued certificate has a new key'
    local old
    old="$(set_aside "${HOST_CERT}")"
    cmp -s "${old}" "${WORK}/short-cert.pem" || fail "${old} is not the expiring certificate"
    cmp -s "${HOST_CERT}" "${DEST}/testhost/server-cert.pem" || fail 'the installed copy is not the new certificate'
}

case_5_renew_on_template_change() {
    CURRENT_CASE='case 5: renewal by template change'
    local before_serial before_pub
    before_serial="$(serial "${KB_CERT}")"
    before_pub="$(pubkey "${KB_CERT}")"
    write_kerbside_vars '"console.example.com", "kerbside.example.com"'
    run_play "${WORK}/kerbside.json"
    [ "$(serial "${KB_CERT}")" != "${before_serial}" ] || fail 'the certificate was not reissued'
    sans "${KB_CERT}" | grep -q 'DNS:kerbside.example.com' || fail 'the new SAN is missing'
    [ "$(pubkey "${KB_CERT}")" = "${before_pub}" ] || fail 'the reissued certificate has a new key'
    set_aside "${KB_CERT}" > /dev/null
    cmp -s "${KB_CERT}" "${DEST}/kerbside/server-cert.pem" || fail 'the installed copy is not the new certificate'
}

case_6_no_renewal() {
    CURRENT_CASE='case 6: no renewal when nothing changed'
    local host_serial kb_serial
    host_serial="$(serial "${HOST_CERT}")"
    kb_serial="$(serial "${KB_CERT}")"
    run_play "${WORK}/kerbside.json"
    [ "${CHANGED}" -eq 0 ] || fail "the rerun reported changed=${CHANGED}"
    [ "$(serial "${KB_CERT}")" = "${kb_serial}" ] || fail 'the certificate was reissued'
    set_aside "${KB_CERT}" > /dev/null
    run_play
    [ "${CHANGED}" -eq 0 ] || fail "the rerun of the first certificate reported changed=${CHANGED}"
    [ "$(serial "${HOST_CERT}")" = "${host_serial}" ] || fail 'the first certificate was reissued'
    set_aside "${HOST_CERT}" > /dev/null
}

case_7_interrupted_reissue() {
    CURRENT_CASE='case 7: a template change survives an interrupted reissue'
    # Fail the run at the step which sets the old certificate aside, by
    # putting an mv which always fails first on PATH. The changed template
    # must not be recorded on disk until the certificate is gone, or the
    # rerun sees nothing to do and the new SAN waits for the expiry window.
    local before_serial before_pub
    before_serial="$(serial "${KB_CERT}")"
    before_pub="$(pubkey "${KB_CERT}")"
    mkdir -p "${WORK}/failing-mv"
    printf '#!/bin/sh\nexit 1\n' > "${WORK}/failing-mv/mv"
    chmod +x "${WORK}/failing-mv/mv"
    write_kerbside_vars '"console.example.com", "kerbside.example.com", "spice.example.com"'
    PATH="${WORK}/failing-mv:${PATH}" run_play_fails '^fatal: ' "${WORK}/kerbside.json"
    grep '^TASK \[' "${LAST_LOG}" | tail -n 1 | grep -q 'Move the old host certificate aside' \
        || fail 'the run did not fail at the set-aside step'
    [ "$(serial "${KB_CERT}")" = "${before_serial}" ] || fail 'the interrupted run replaced the certificate'

    run_play "${WORK}/kerbside.json"
    [ "$(serial "${KB_CERT}")" != "${before_serial}" ] || fail 'the rerun did not reissue the certificate'
    sans "${KB_CERT}" | grep -q 'DNS:spice.example.com' || fail 'the new SAN is missing'
    [ "$(pubkey "${KB_CERT}")" = "${before_pub}" ] || fail 'the reissued certificate has a new key'
    local copies=("${KB_CERT}".*)
    [ "${#copies[@]}" -eq 2 ] || fail "expected two set-aside copies of ${KB_CERT}, found ${#copies[@]}"
}

case_8_rejects_bad_values() {
    CURRENT_CASE='case 8: values which would be misread are rejected'
    local host_serial
    host_serial="$(serial "${HOST_CERT}")"
    # The JSON integer 256 is what an unquoted 0400 becomes in YAML.
    echo '{"cert_key_mode": 256}' > "${WORK}/bad-mode-int.json"
    run_play_fails 'cert_key_mode is 256, but must be a quoted' "${WORK}/bad-mode-int.json"
    echo '{"cert_mode": "444"}' > "${WORK}/bad-mode-str.json"
    run_play_fails 'cert_mode is \\?"444\\?", but must be a quoted' "${WORK}/bad-mode-str.json"
    echo '{"cert_cn": "console.example.com\nca"}' > "${WORK}/bad-cn.json"
    run_play_fails 'is not a single line' "${WORK}/bad-cn.json"
    echo '{"cert_san_dns": ["console.example.com", "x.example.com\rca"]}' > "${WORK}/bad-san.json"
    run_play_fails 'is not a single line' "${WORK}/bad-san.json"
    [ "$(serial "${HOST_CERT}")" = "${host_serial}" ] || fail 'a rejected run reissued the certificate'
}

case_1_fresh_issue
case_2_idempotent
case_3_sans_and_stem
case_4_renew_on_expiry
case_5_renew_on_template_change
case_6_no_renewal
case_7_interrupted_reissue
case_8_rejects_bad_values

echo "internal_ca: all eight cases passed (${RUNS} ansible runs)"
