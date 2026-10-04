#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-test-kerbside-cluster-config.sh -- drive the node role's
# kerbside_preflight and kerbside_credentials entry points against a stub
# sf-ctl on localhost, as play 6a of examples/_shared/site.yml runs them,
# and check what they asked sf-ctl to do.
#
# It proves that a cluster with the feature off is not touched at all; that
# with kerbside_url set the signing key is ensured, and with
# kerbside_system_key set too the "kerbside" key is minted on stdin and never
# reaches the ansible output, even at -vvvv; that a KERBSIDE_URL or
# KERBSIDE_TOKEN_DURATION cluster_config row which differs from its variable
# (or which an older sf-ctl redacts), or an extra_config entry naming either,
# stops the deploy before anything is written, while an equal row only warns;
# that a Kerbside key which is equal to system_key, too short, or set without
# a URL is refused before it is minted; and that the node role's config
# template renders the token duration only beside the URL. No CI lane can
# turn the feature on yet, so this is the only thing which runs the "on"
# path. It also refuses a non-positive duration, and a show-config which is not
# a JSON object, before anything is read or written, and checks structurally
# that examples/_shared/site.yml calls the two entry points in the right places.
#
# The stub sf-ctl appends each call's argv, its SHAKENFIST_NODE_MESH_IP and
# any stdin to a log, and answers show-config from a JSON file each case
# writes. The playbook writes a marker into the same log between the two entry
# points and after the second, so a case can tell how far a run got. Secrets
# are passed with -e @file, never -e key=value: ansible echoes command line
# extra vars at -v and above, which would leak the key for the wrong reason.
#
# It runs without root. Everything is written under a temporary directory
# which is removed on exit; the working tree is used in place via a symlinked
# collections path, so this tests the role as checked out.
#
# Run from anywhere, with ansible-playbook on PATH:
#
#     tools/ci-test-kerbside-cluster-config.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COLLECTION_DIR="${REPO_ROOT}/shakenfist/deploy/collection"

CURRENT_CASE='setup'
LAST_LOG=''

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ci-test-kerbside-cluster-config.XXXXXX")"
trap 'rm -rf "${WORK}"' EXIT

STUB="${WORK}/sf-ctl"
STUB_LOG="${WORK}/sf-ctl.log"
ROWS="${WORK}/rows.json"

fail() {
    echo "FAIL [${CURRENT_CASE}]: $*" >&2
    if [ -n "${LAST_LOG}" ] && [ -f "${LAST_LOG}" ]; then
        echo "---- last 40 lines of ${LAST_LOG##*/} ----" >&2
        tail -n 40 "${LAST_LOG}" >&2
    fi
    echo "---- the stub sf-ctl's log ----" >&2
    stub_log >&2
    exit 1
}

need() {
    command -v "$1" > /dev/null || fail "$1 not found on PATH: $2"
}

need ansible-playbook 'install ansible-core (the sanity job gets it with ansible-lint)'

mkdir -p "${WORK}/collections/ansible_collections/shakenfist" "${WORK}/logs" \
    "${WORK}/ansible-tmp" "${WORK}/vars"
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
# The stub logs this when it is set, so it must come only from the play.
unset SHAKENFIST_NODE_MESH_IP

# The stub sf-ctl. stdin is read only when it is not a terminal, and a call
# without any is logged without a STDIN line.
cat > "${STUB}" << EOF
#!/bin/bash
{
    echo "ARGV: \$*"
    if [ -n "\${SHAKENFIST_NODE_MESH_IP:-}" ]; then
        echo "ENV: SHAKENFIST_NODE_MESH_IP=\${SHAKENFIST_NODE_MESH_IP}"
    fi
    if [ ! -t 0 ]; then
        input="\$(cat)"
        if [ -n "\${input}" ]; then
            echo "STDIN: \${input}"
        fi
    fi
} >> '${STUB_LOG}'
if [ "\${1:-}" = 'show-config' ]; then
    cat '${ROWS}'
fi
EOF
chmod +x "${STUB}"

# Play 6a's shape: the preflight first, a marker where the play's set-config
# and deploy key tasks would be, then the credentials. cluster_db_host and
# extra_config are play vars, as there, and node_mesh_ip an include var.
cat > "${WORK}/play.yml" << 'EOF'
---
- name: Exercise the Kerbside entry points as play 6a does
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    extra_config: "[]"
    cluster_db_host: localhost
  tasks:
    - name: Check the Kerbside configuration against cluster_config
      ansible.builtin.include_role:
        name: shakenfist.shakenfist.node
        tasks_from: kerbside_preflight

    - name: Mark the end of the preflight
      ansible.builtin.shell: echo '-- preflight passed --' >> "{{ stub_log }}"
      changed_when: false

    - name: Mint the Kerbside signing key and system namespace credential
      ansible.builtin.include_role:
        name: shakenfist.shakenfist.node
        tasks_from: kerbside_credentials
      vars:
        node_mesh_ip: 192.0.2.53

    - name: Mark the end of the credentials
      ansible.builtin.shell: echo '-- credentials done --' >> "{{ stub_log }}"
      changed_when: false
EOF

# The node role's config template, rendered from the role's own defaults and
# nothing else, which is all it needs.
cat > "${WORK}/template.yml" << EOF
---
- name: Render the node role's config template
  hosts: localhost
  connection: local
  gather_facts: false
  vars_files:
    - ${COLLECTION_DIR}/roles/node/defaults/main.yml
  tasks:
    - name: Render the config template
      ansible.builtin.template:
        src: ${COLLECTION_DIR}/roles/node/templates/config
        dest: ${WORK}/config
        mode: '0600'
EOF

URL='https://kerbside.example.com'
SYSTEM_KEY='deploy-key-Hq3vX9cT2mWb'
KERBSIDE_KEY='kerbside-key-Zr8nP4fJ7kLs'

cat > "${WORK}/common.json" << EOF
{
  "sf_ctl_path": "${STUB}",
  "stub_log": "${STUB_LOG}",
  "system_key": "${SYSTEM_KEY}"
}
EOF

# write_vars NAME JSON: an extra vars file for one run.
write_vars() {
    echo "$2" > "${WORK}/vars/$1.json"
    echo "${WORK}/vars/$1.json"
}

# write_rows JSON: what the stub's show-config answers.
write_rows() {
    echo "$1" > "${ROWS}"
}

stub_log() {
    cat "${STUB_LOG}" 2> /dev/null || true
}

RUNS=0
# run_ansible PLAYBOOK [extra vars file...]: run a playbook into a fresh log,
# with an empty stub log, at ${VERBOSITY} if that is set. Returns its status.
run_ansible() {
    local playbook="$1"
    shift
    local args=(-e "@${WORK}/common.json")
    local f
    for f in "$@"; do
        args+=(-e "@${f}")
    done
    if [ -n "${VERBOSITY:-}" ]; then
        args+=("${VERBOSITY}")
    fi
    RUNS=$((RUNS + 1))
    LAST_LOG="${WORK}/logs/run-${RUNS}.log"
    rm -f "${STUB_LOG}"
    ansible-playbook -i localhost, -c local -e ansible_become=false \
        "${args[@]}" "${playbook}" < /dev/null > "${LAST_LOG}" 2>&1
}

# run_play [extra vars file...]: run the playbook, expecting success.
run_play() {
    run_ansible "${WORK}/play.yml" "$@" || fail 'ansible-playbook failed'
}

# run_play_fails PATTERN [extra vars file...]: run the playbook, expecting
# it to fail with output matching the extended regex PATTERN.
run_play_fails() {
    local pattern="$1"
    shift
    if run_ansible "${WORK}/play.yml" "$@"; then
        fail "ansible-playbook succeeded, expected a failure matching '${pattern}'"
    fi
    grep -qE "${pattern}" "${LAST_LOG}" || fail "the failure does not match '${pattern}'"
}

# assert_stub_log EXPECTED: the stub's log is exactly EXPECTED.
assert_stub_log() {
    [ "$(stub_log)" = "$1" ] || fail "the stub's log is not as expected; wanted:
$1"
}

# assert_no_call SUBCOMMAND...: the stub was never called with any of them.
assert_no_call() {
    local sub
    for sub in "$@"; do
        if stub_log | grep -q "^ARGV: ${sub}"; then
            fail "sf-ctl ${sub} was called"
        fi
    done
}

# assert_output_has / assert_output_lacks FIXED_STRING: the ansible output.
assert_output_has() {
    grep -qF -- "$1" "${LAST_LOG}" || fail "the ansible output lacks '$1'"
}

assert_output_lacks() {
    if grep -qF -- "$1" "${LAST_LOG}"; then
        fail "the ansible output contains '$1'"
    fi
}

case_1_feature_off() {
    CURRENT_CASE='case 1: feature off'
    write_rows '{"KERBSIDE_URL": "https://old.example.com", "KERBSIDE_TOKEN_DURATION": 60}'
    run_play
    assert_no_call show-config ensure-kerbside-signing-key bootstrap-system-key
    assert_stub_log '-- preflight passed --
-- credentials done --'
}

case_2_url_only() {
    CURRENT_CASE='case 2: kerbside_url only, no rows'
    write_rows '{"DNS_SERVER": "8.8.8.8"}'
    # An extra_config entry for anything else is not the guard's business.
    run_play "$(write_vars url-only "{
      \"kerbside_url\": \"${URL}\",
      \"extra_config\": \"[{\\\"name\\\": \\\"DNS_SERVER\\\", \\\"value\\\": \\\"8.8.8.8\\\"}]\"
    }")"
    assert_stub_log 'ARGV: show-config
-- preflight passed --
ARGV: ensure-kerbside-signing-key
-- credentials done --'
    assert_output_lacks 'WARNING: the cluster_config row'
}

case_3_both_set() {
    CURRENT_CASE='case 3: kerbside_url and kerbside_system_key set'
    write_rows '{}'
    # At -vvvv, for case 4, which reads this run's output.
    VERBOSITY=-vvvv run_play "$(write_vars both "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_system_key\": \"${KERBSIDE_KEY}\"
    }")"
    assert_stub_log "ARGV: show-config
-- preflight passed --
ARGV: ensure-kerbside-signing-key
ARGV: bootstrap-system-key --key-from-stdin kerbside
ENV: SHAKENFIST_NODE_MESH_IP=192.0.2.53
STDIN: ${KERBSIDE_KEY}
-- credentials done --"
    BOTH_SET_LOG="${LAST_LOG}"
}

case_4_key_not_in_output() {
    CURRENT_CASE='case 4: the key never reaches the output, even at -vvvv'
    LAST_LOG="${BOTH_SET_LOG}"
    # Prove the run really was verbose and really minted, or the absence
    # below proves nothing.
    assert_output_has '<localhost> ESTABLISH LOCAL CONNECTION'
    assert_output_has 'TASK [shakenfist.shakenfist.node : Mint the Kerbside key in the system namespace]'
    assert_output_lacks "${KERBSIDE_KEY}"
}

case_5_equal_rows() {
    CURRENT_CASE='case 5: rows equal to both variables'
    # The duration as set-config stores it: an integer.
    write_rows "{\"KERBSIDE_URL\": \"${URL}\", \"KERBSIDE_TOKEN_DURATION\": 300}"
    run_play "$(write_vars equal "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_token_duration\": 300
    }")"
    assert_output_has 'WARNING: the cluster_config row KERBSIDE_URL repeats kerbside_url'
    assert_output_has 'WARNING: the cluster_config row KERBSIDE_TOKEN_DURATION repeats kerbside_token_duration (300)'
    assert_stub_log 'ARGV: show-config
-- preflight passed --
ARGV: ensure-kerbside-signing-key
-- credentials done --'
}

# assert_stopped_in_preflight: show-config ran, and nothing after it.
assert_stopped_in_preflight() {
    assert_stub_log 'ARGV: show-config'
    assert_no_call ensure-kerbside-signing-key bootstrap-system-key set-config
}

case_6_differing_url() {
    CURRENT_CASE='case 6: a differing KERBSIDE_URL row'
    write_rows '{"KERBSIDE_URL": "https://old.example.com", "KERBSIDE_TOKEN_DURATION": 300}'
    run_play_fails 'sf-ctl unset-config KERBSIDE_URL' "$(write_vars both "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_system_key\": \"${KERBSIDE_KEY}\"
    }")"
    assert_output_has "The cluster_config row KERBSIDE_URL is 'https://old.example.com', but kerbside_url is '${URL}'"
    # The equal duration row only warns.
    assert_output_has 'WARNING: the cluster_config row KERBSIDE_TOKEN_DURATION repeats'
    assert_output_lacks 'The cluster_config row KERBSIDE_TOKEN_DURATION is '
    assert_stopped_in_preflight
}

case_7_differing_duration() {
    CURRENT_CASE='case 7: a differing KERBSIDE_TOKEN_DURATION row'
    local vars
    vars="$(write_vars both "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_system_key\": \"${KERBSIDE_KEY}\"
    }")"
    write_rows '{"KERBSIDE_TOKEN_DURATION": 60}'
    run_play_fails 'sf-ctl unset-config KERBSIDE_TOKEN_DURATION' "${vars}"
    assert_output_has "The cluster_config row KERBSIDE_TOKEN_DURATION is '60', but kerbside_token_duration is '300'"
    assert_output_lacks 'The cluster_config row KERBSIDE_URL is '
    assert_stopped_in_preflight

    # Both rows differing: one run names both fixes.
    write_rows '{"KERBSIDE_URL": "https://old.example.com", "KERBSIDE_TOKEN_DURATION": 60}'
    run_play_fails 'sf-ctl unset-config KERBSIDE_TOKEN_DURATION' "${vars}"
    assert_output_has "The cluster_config row KERBSIDE_URL is 'https://old.example.com'"
    assert_output_has 'sf-ctl unset-config KERBSIDE_URL'
    assert_stopped_in_preflight
}

case_8_redacted_duration() {
    CURRENT_CASE='case 8: a <redacted> KERBSIDE_TOKEN_DURATION row, from an older sf-ctl'
    write_rows '{"KERBSIDE_TOKEN_DURATION": "<redacted>"}'
    run_play_fails 'sf-ctl unset-config KERBSIDE_TOKEN_DURATION' "$(write_vars both "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_system_key\": \"${KERBSIDE_KEY}\"
    }")"
    assert_output_has 'The cluster_config row KERBSIDE_TOKEN_DURATION exists, but this sf-ctl redacts its value'
    assert_stopped_in_preflight
}

case_9_extra_config() {
    CURRENT_CASE='case 9: extra_config names a Kerbside setting'
    write_rows '{}'
    local name
    for name in KERBSIDE_TOKEN_DURATION KERBSIDE_URL; do
        run_play_fails "extra_config sets ${name}," "$(write_vars "extra-${name}" "{
          \"kerbside_url\": \"${URL}\",
          \"kerbside_system_key\": \"${KERBSIDE_KEY}\",
          \"extra_config\": \"[{\\\"name\\\": \\\"${name}\\\", \\\"value\\\": \\\"300\\\"}]\"
        }")"
        assert_output_has "Remove the ${name} entry from extra_config"
        # Stopped before the preflight's end, so before anything is written.
        if stub_log | grep -q -- '-- preflight passed --'; then
            fail 'the run got past the preflight'
        fi
        assert_no_call ensure-kerbside-signing-key bootstrap-system-key set-config
    done
}

case_10_refused_credentials() {
    CURRENT_CASE='case 10a: a Kerbside key equal to system_key'
    write_rows '{}'
    VERBOSITY=-vvvv run_play_fails 'kerbside_system_key is the same as system_key' \
        "$(write_vars same-key "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_system_key\": \"${SYSTEM_KEY}\"
    }")"
    assert_stub_log 'ARGV: show-config
-- preflight passed --'
    assert_output_lacks "${SYSTEM_KEY}"

    CURRENT_CASE='case 10b: a Kerbside key without kerbside_url'
    VERBOSITY=-vvvv run_play_fails 'kerbside_system_key is set but kerbside_url is not' \
        "$(write_vars no-url "{
      \"kerbside_system_key\": \"${KERBSIDE_KEY}\"
    }")"
    assert_stub_log '-- preflight passed --'
    assert_output_lacks "${KERBSIDE_KEY}"

    CURRENT_CASE='case 10c: a short Kerbside key'
    local short='Zq7shortKEY'
    VERBOSITY=-vvvv run_play_fails 'kerbside_system_key is shorter than 16 characters' \
        "$(write_vars short-key "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_system_key\": \"${short}\"
    }")"
    assert_stub_log 'ARGV: show-config
-- preflight passed --'
    assert_output_lacks "${short}"
}

# render [extra vars file...]: render the config template into
# ${WORK}/config.
render() {
    rm -f "${WORK}/config"
    run_ansible "${WORK}/template.yml" "$@" || fail 'rendering the config template failed'
}

case_11_template() {
    CURRENT_CASE='case 11: the config template renders the duration only beside the URL'
    render
    if grep -q 'KERBSIDE' "${WORK}/config"; then
        fail "the defaults render a Kerbside setting: $(grep KERBSIDE "${WORK}/config")"
    fi

    render "$(write_vars duration-only '{"kerbside_token_duration": 600}')"
    if grep -q 'KERBSIDE' "${WORK}/config"; then
        fail "a duration without a URL renders: $(grep KERBSIDE "${WORK}/config")"
    fi

    render "$(write_vars url-and-duration "{\"kerbside_url\": \"${URL}\", \"kerbside_token_duration\": 600}")"
    [ "$(grep KERBSIDE "${WORK}/config")" = "SHAKENFIST_KERBSIDE_URL=\"${URL}\"
SHAKENFIST_KERBSIDE_TOKEN_DURATION=\"600\"" ] || fail "the Kerbside lines are wrong: $(grep KERBSIDE "${WORK}/config")"

    render "$(write_vars url-default-duration "{\"kerbside_url\": \"${URL}\"}")"
    grep -qx 'SHAKENFIST_KERBSIDE_TOKEN_DURATION="300"' "${WORK}/config" \
        || fail "the default duration is not rendered: $(grep KERBSIDE "${WORK}/config")"
}

case_12_non_positive_duration() {
    CURRENT_CASE='case 12: a non-positive kerbside_token_duration'
    write_rows '{}'
    local duration
    for duration in 0 -5; do
        run_play_fails 'must be a positive number of seconds' "$(write_vars "duration-${duration}" "{
          \"kerbside_url\": \"${URL}\",
          \"kerbside_token_duration\": ${duration}
        }")"
        # Refused before show-config, so before anything could be read.
        if stub_log | grep -q '^ARGV'; then
            fail "sf-ctl was called for a duration of ${duration}"
        fi
    done

    # A string is refused earlier still, by the entry point's argument_specs,
    # which says so in its own words (the assert's "| int" never sees it).
    run_play_fails 'kerbside_token_duration. is of type str and we were unable to convert to int' \
        "$(write_vars duration-abc "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_token_duration\": \"abc\"
    }")"
    if stub_log | grep -q '^ARGV'; then
        fail 'sf-ctl was called for a duration of abc'
    fi
}

case_13_show_config_not_json() {
    CURRENT_CASE='case 13: show-config prints something other than a JSON object'
    local vars
    vars="$(write_vars both "{
      \"kerbside_url\": \"${URL}\",
      \"kerbside_system_key\": \"${KERBSIDE_KEY}\"
    }")"
    local rows
    for rows in 'WARNING: something' '{oops' '[1, 2]'; do
        write_rows "${rows}"
        run_play_fails 'sf-ctl show-config did not print a JSON object' "${vars}"
        assert_output_has "${rows}"
        assert_stopped_in_preflight
    done
}

# The real site.yml's cluster config play must call the two entry points in
# the right places: the preflight first, before any set-config, and the
# credentials after the system namespace key, with node_mesh_ip passed.
case_14_site_yml_wiring() {
    CURRENT_CASE='case 14: examples/_shared/site.yml wires the entry points correctly'
    local out
    out="$(python3 - "${REPO_ROOT}/examples/_shared/site.yml" << 'PYEOF' 2>&1
import sys

try:
    import yaml
except ImportError:
    print('PyYAML is not importable by python3, so site.yml cannot be checked')
    sys.exit(1)

PLAY = 'Write the cluster configuration (once, before register)'
with open(sys.argv[1]) as f:
    plays = yaml.safe_load(f)
matches = [p for p in plays if p.get('name') == PLAY]
if len(matches) != 1:
    print(f'expected exactly one play named {PLAY!r}, found {len(matches)}')
    sys.exit(1)
tasks = matches[0]['tasks']


def includes(task, tasks_from):
    inc = task.get('ansible.builtin.include_role')
    return (isinstance(inc, dict) and inc.get('name') == 'shakenfist.shakenfist.node'
            and inc.get('tasks_from') == tasks_from)


if not includes(tasks[0], 'kerbside_preflight'):
    print('the first task is not an ansible.builtin.include_role of '
          'shakenfist.shakenfist.node with tasks_from: kerbside_preflight')
    sys.exit(1)

creds = [i for i, t in enumerate(tasks) if includes(t, 'kerbside_credentials')]
if len(creds) != 1:
    print(f'expected exactly one kerbside_credentials include, found {len(creds)}')
    sys.exit(1)
names = [t.get('name') for t in tasks]
KEY = 'Bootstrap the system namespace key'
if names.count(KEY) != 1:
    print(f'expected exactly one task named {KEY!r}')
    sys.exit(1)
if creds[0] < names.index(KEY):
    print(f'the kerbside_credentials include comes before {KEY!r}')
    sys.exit(1)
if 'node_mesh_ip' not in (tasks[creds[0]].get('vars') or {}):
    print('the kerbside_credentials include does not pass node_mesh_ip in its vars')
    sys.exit(1)
PYEOF
)" || fail "${out}"
}

case_1_feature_off
case_2_url_only
case_3_both_set
case_4_key_not_in_output
case_5_equal_rows
case_6_differing_url
case_7_differing_duration
case_8_redacted_duration
case_9_extra_config
case_10_refused_credentials
case_11_template
case_12_non_positive_duration
case_13_show_config_not_json
case_14_site_yml_wiring

echo "kerbside cluster config: all fourteen cases passed (${RUNS} ansible runs)"
