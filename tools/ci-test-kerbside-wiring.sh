#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-test-kerbside-wiring.sh -- check how examples/_shared/site.yml wires the
# kerbside role of the shakenfist.kerbside collection into a Shaken Fist
# deploy, against the release examples/_shared/requirements.yml pins, and drive
# site.yml's Kerbside plays and the node role's kerbside_source entry point
# against a test inventory.
#
# The kerbside role is tested in Kerbside, where it lives. This tests the seam
# between the two. It proves that site.yml places its Kerbside plays before
# any host is changed and last, hands the role only variables the installed
# role's argument specs declare, overrides no variable an operator sets, and
# reaches the role only through include_role; that internal_ca installs the
# proxy certificate, key and CA where the installed role reads them, owned as
# it expects, whatever the operator overrides; that site.yml parses with and
# without the collection, reaches a Kerbside host outside allsf only in the
# reachability and Kerbside plays, and changes no play's hosts when the
# kerbside group is absent; that kerbside_source builds a source Kerbside's own
# checker accepts, field by field, reserves the ports Shaken Fist uses, and
# refuses an empty key, a CA which is not a PEM, a loopback api_url on a host
# which is not a Shaken Fist node and a kerbside_url the two sides see
# differently; that the cluster shape play refuses a kerbside_url which ends in
# a slash, or which Shaken Fist's nodes see differently, before any host is
# changed; that the validation play checks every Kerbside host with the
# variables that host sees, and that a refusal on any host ends the run; that
# without the collection it names the install command when there is a Kerbside
# host, and does nothing when there is none; that it accepts a release in the
# range requirements.yml pins, refuses one outside it, and accepts a checkout
# with a warning; that kerbside_extra_sources follow this cluster's source;
# that the Deploy play's source carries the CA byte for byte; that internal_ca
# issues the proxy certificate with an IP SAN for an address and a DNS SAN for
# a name; and that the secrets never reach the ansible output.
#
# No host is changed: the plays are extracted from site.yml by name and run
# against an inventory whose hosts are all this machine, with every play which
# would change one left out, and become made to fail. It runs without root.
# Secrets are passed with -e @file, never -e key=value: ansible echoes command
# line extra vars at -v and above.
#
# shakenfist.kerbside is installed from Ansible Galaxy, as an operator installs
# it, from examples/_shared/requirements.yml into a temporary collections path,
# so this needs network access to Galaxy. Shaken Fist's own collection comes
# first in that path, through a symlink to the working tree, so this tests
# Shaken Fist's roles as checked out.
#
# Run from anywhere, with ansible-playbook, ansible-galaxy, openssl, certtool
# (gnutls-bin) and python3 on PATH:
#
#     tools/ci-test-kerbside-wiring.sh [CASE_NUMBER...]
#
# With case numbers, only those cases run, which is quicker when debugging one.
# Case 4 needs case 3, and the last case checks the logs of the cases run
# before it. Set KEEP_WORK=1 to keep the temporary directory, and every run's
# log in it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COLLECTION_DIR="${REPO_ROOT}/shakenfist/deploy/collection"
SITE_YML="${REPO_ROOT}/examples/_shared/site.yml"
REQUIREMENTS="${REPO_ROOT}/examples/_shared/requirements.yml"

CURRENT_CASE='setup'
LAST_LOG=''

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ci-test-kerbside-wiring.XXXXXX")"
trap '[ -n "${KEEP_WORK:-}" ] || rm -rf "${WORK}"' EXIT

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

need ansible-playbook 'install ansible-core (the sanity job gets it with ansible-lint)'
need ansible-galaxy 'install ansible-core (the sanity job gets it with ansible-lint)'
need openssl 'install the openssl package'
need certtool 'install the gnutls-bin package'
need python3 'install python3'

PY="$(python3 -c 'import sys; print(sys.executable)')"
"${PY}" -c 'import jinja2, yaml' 2> /dev/null \
    || fail "${PY} cannot import jinja2 and yaml (ansible-core depends on both)"

mkdir -p "${WORK}/sf/ansible_collections/shakenfist" "${WORK}/logs" "${WORK}/ansible-tmp" \
    "${WORK}/vars" "${WORK}/certs" "${WORK}/site/group_vars" "${WORK}/site/host_vars"
ln -s "${COLLECTION_DIR}" "${WORK}/sf/ansible_collections/shakenfist/shakenfist"

# An empty config and a private ansible home, so neither a developer's
# ~/.ansible.cfg nor an installed copy of either collection changes what runs.
: > "${WORK}/ansible.cfg"
export ANSIBLE_CONFIG="${WORK}/ansible.cfg"
export ANSIBLE_HOME="${WORK}/ansible-home"
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false
export ANSIBLE_LOCAL_TEMP="${WORK}/ansible-tmp"
export ANSIBLE_REMOTE_TMP="${WORK}/ansible-tmp"
export ANSIBLE_NOCOLOR=1
export ANSIBLE_STDOUT_CALLBACK=default

LAST_LOG="${WORK}/logs/galaxy-install.log"
ansible-galaxy collection install -r "${REQUIREMENTS}" -p "${WORK}/kerbside" \
    < /dev/null > "${LAST_LOG}" 2>&1 \
    || fail 'ansible-galaxy could not install examples/_shared/requirements.yml; this test needs Ansible Galaxy'
KERBSIDE_ROLE="${WORK}/kerbside/ansible_collections/shakenfist/kerbside/roles/kerbside"
[ -f "${KERBSIDE_ROLE}/meta/argument_specs.yml" ] || fail "the install left no kerbside role at ${KERBSIDE_ROLE}"
LAST_LOG=''

# Shaken Fist's collection, then shakenfist.kerbside; or Shaken Fist's alone.
WITH="${WORK}/sf:${WORK}/kerbside"
WITHOUT="${WORK}/sf"

# The CA which Shaken Fist's /admin/cacert serves.
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
    -keyout "${WORK}/certs/sf-ca-key.pem" -out "${WORK}/certs/sf-ca-cert.pem" \
    -days 30 -subj '/O=Shaken Fist/CN=Shaken Fist Test SPICE CA' 2> /dev/null \
    || fail 'openssl could not make the test CA'
SF_CA_FILE="${WORK}/certs/sf-ca-cert.pem"

URL='https://console.example.com:13002/kerbside'
FQDN='console.example.com'
API_URL='https://sf-api.example.com:13000'
# The secrets. The seed is exactly 32 characters, Kerbside's minimum.
SYSTEM_KEY='kb-system-key-Vt7qL2mXc9Rw'
SEED='seed-Jk4pW8zN2bQ6vT1yH5cR9mD3fL7'
SQL_SECRET='Xq8Tz3LmPw'
SQL_URL="mysql+pymysql://kerbside:pw-${SQL_SECRET}@db.example.com/kerbside"
[ "${#SEED}" -eq 32 ] || fail "the test seed is ${#SEED} characters, not 32"
SECRETS=("${SYSTEM_KEY}" "${SEED}" "${SQL_SECRET}")

RUNS=0
# run COLLECTIONS_PATH ARGUMENT...: ansible-playbook with the arguments, into a
# fresh log, with the collections path given. Returns its status.
run() {
    local collections="$1"
    shift
    RUNS=$((RUNS + 1))
    LAST_LOG="${WORK}/logs/run-${RUNS}.log"
    local rc=0
    ANSIBLE_COLLECTIONS_PATH="${collections}" ansible-playbook "$@" < /dev/null > "${LAST_LOG}" 2>&1 || rc=$?
    return "${rc}"
}

assert_output_has() {
    grep -qF -- "$1" "${LAST_LOG}" || fail "the ansible output lacks '$1'"
}

assert_output_lacks() {
    if grep -qF -- "$1" "${LAST_LOG}"; then
        fail "the ansible output contains '$1'"
    fi
}

# assert_task_ok TASK HOST: the task ran on the host, and passed there.
assert_task_ok() {
    awk -v task="TASK [$1]" -v ok="ok: [$2]" '
        index($0, "TASK [") == 1 || index($0, "PLAY [") == 1 { inside = (index($0, task) == 1) }
        inside && index($0, ok) == 1 { found = 1 }
        END { exit !found }' "${LAST_LOG}" || fail "the task '$1' did not pass on $2"
}

# write_vars NAME JSON: an extra vars file for one run, as @path.
write_vars() {
    printf '%s\n' "$2" > "${WORK}/vars/$1.json"
    echo "@${WORK}/vars/$1.json"
}

# json_string FILE: the file's contents as a JSON string.
json_string() {
    "${PY}" -c 'import json, sys; print(json.dumps(open(sys.argv[1]).read()))' "$1"
}

# extract_plays DEST NAME...: site.yml's plays of those names, in that order,
# written to DEST as a playbook of its own. A play which follows them and only
# says that it ran is added, so a case can tell whether the run ended in them.
cat > "${WORK}/extract-plays.py" << 'PYEOF'
import sys

import yaml

site, dest, *names = sys.argv[1:]
with open(site) as f:
    plays = yaml.safe_load(f)
picked = [p for p in plays if p.get('name') in names]
if [p['name'] for p in picked] != names:
    sys.exit(f'expected the plays {names} in that order, found {[p["name"] for p in picked]}')
picked.append({
    'name': 'A play after the Kerbside validation',
    'hosts': 'localhost',
    'connection': 'local',
    'gather_facts': False,
    'tasks': [{'name': 'Say that a later play ran', 'ansible.builtin.debug': {'msg': 'LATER PLAY RAN'}}],
})
with open(dest, 'w') as f:
    yaml.safe_dump(picked, f, sort_keys=False)
PYEOF
extract_plays() {
    local out
    out="$("${PY}" "${WORK}/extract-plays.py" "${SITE_YML}" "$@" 2>&1)" || fail "${out}"
}

PROBE_PLAY='Probe every node for reachability'
QUARANTINE_PLAY='Quarantine unreachable nodes and validate the cluster shape'
VALIDATE_PLAY='Validate the Kerbside configuration'
DEPLOY_PLAY='Deploy Kerbside'

# site.yml's reachability and Kerbside validation plays, which change no host.
# They read requirements.yml beside them, as site.yml does.
extract_plays "${WORK}/site/site-head.yml" "${PROBE_PLAY}" "${QUARANTINE_PLAY}" "${VALIDATE_PLAY}"

# site_inventory KERBSIDE_HOST...: the test inventory, whose kerbside group is
# the hosts given. The Shaken Fist cluster is SF_NODES (sf-1 alone unless a
# case sets it), every one a hypervisor, with sf-1 the network and database
# node, and every host is this machine. become fails, so a task which would
# change a host fails instead.
SF_NODES='sf-1'
site_inventory() {
    {
        printf '[allsf]\n%s\n[hypervisors]\n%s\n' "${SF_NODES}" "${SF_NODES}"
        printf '[network_node]\nsf-1\n[database_node]\nsf-1\n[kerbside]\n'
        if [ $# -gt 0 ]; then
            printf '%s\n' "$@"
        fi
        printf '[all:vars]\nansible_connection=local\n'
        printf 'ansible_python_interpreter={{ ansible_playbook_python }}\n'
        printf 'ansible_become_exe=/bin/false\n'
    } > "${WORK}/site/hosts.ini"
}

# A valid configuration: the Kerbside settings in group_vars/kerbside, and
# kerbside_url and api_url for every host, with no host_vars, and the real
# requirements.yml. Cases change them, and call this again when they are done.
reset_site_vars() {
    rm -f "${WORK}"/site/host_vars/*.yml
    cat > "${WORK}/site/group_vars/kerbside.yml" << EOF
kerbside_public_fqdn: ${FQDN}
kerbside_system_key: ${SYSTEM_KEY}
kerbside_auth_secret_seed: ${SEED}
kerbside_sql_url: '${SQL_URL}'
EOF
    printf "kerbside_url: '%s'\napi_url: %s\n" "${URL}" "${API_URL}" > "${WORK}/site/group_vars/all.yml"
    cp "${REQUIREMENTS}" "${WORK}/site/requirements.yml"
}

# run_site_head COLLECTIONS_PATH [ARGUMENT...]: the head of site.yml against the
# test inventory, at -v unless the arguments say otherwise. Returns its status.
run_site_head() {
    local collections="$1"
    shift
    run "${collections}" -i "${WORK}/site/hosts.ini" -v "$@" "${WORK}/site/site-head.yml"
}

assert_later_play_ran() {
    assert_output_has '"msg": "LATER PLAY RAN"'
}

assert_later_play_did_not_run() {
    assert_output_lacks '"msg": "LATER PLAY RAN"'
}

case_1_site_yml_structure() {
    CURRENT_CASE='case 1: site.yml places and wires the Kerbside plays, against the installed role'
    local out
    out="$("${PY}" - "${SITE_YML}" "${KERBSIDE_ROLE}" \
        "${COLLECTION_DIR}/roles/node/meta/argument_specs.yml" << 'PYEOF' 2>&1
import sys

import jinja2
from jinja2 import meta
import yaml

site_path, role_path, node_specs_path = sys.argv[1:]


def load(path):
    with open(path) as f:
        return yaml.safe_load(f)


plays = load(site_path)
specs = load(role_path + '/meta/argument_specs.yml')['argument_specs']
role_defaults = load(role_path + '/defaults/main.yml')
role_vars = load(role_path + '/vars/main.yml')
node_specs = load(node_specs_path)['argument_specs']
names = [p.get('name') for p in plays]

PROBE = 'Probe every node for reachability'
QUARANTINE = 'Quarantine unreachable nodes and validate the cluster shape'
VALIDATE = 'Validate the Kerbside configuration'
SANITY = 'Final deployment sanity checks'
DEPLOY = 'Deploy Kerbside'
KERBSIDE = 'shakenfist.kerbside.kerbside'
NODE = 'shakenfist.shakenfist.node'
INTERNAL_CA = 'shakenfist.shakenfist.internal_ca'

for name in (PROBE, QUARANTINE, VALIDATE, SANITY, DEPLOY):
    if names.count(name) != 1:
        sys.exit(f'expected exactly one play named {name!r}')

# Validation straight after the reachability plays, so before any play which
# changes a host, and the deploy last, after Shaken Fist is proven up.
if names[:3] != [PROBE, QUARANTINE, VALIDATE]:
    sys.exit(f'the first three plays are {names[:3]}, not the reachability plays then {VALIDATE!r}')
if names[-1] != DEPLOY:
    sys.exit(f'the last play is {names[-1]!r}, not {DEPLOY!r}')
if names.index(SANITY) > names.index(DEPLOY):
    sys.exit(f'{DEPLOY!r} comes before {SANITY!r}')

validate = plays[names.index(VALIDATE)]
deploy = plays[names.index(DEPLOY)]
if plays[0].get('hosts') != 'allsf:kerbside':
    sys.exit(f'the probe play targets {plays[0].get("hosts")!r}, not allsf:kerbside')
for play in (validate, deploy):
    if play.get('hosts') != 'kerbside:&reachable':
        sys.exit(f'{play["name"]!r} targets {play.get("hosts")!r}, not kerbside:&reachable')
if validate.get('any_errors_fatal') is not True:
    sys.exit(f'{VALIDATE!r} does not set any_errors_fatal')


def tasks_of(tasks):
    """Every task, and every task in a block, its rescue and its always."""
    for task in tasks or []:
        yield task
        for key in ('block', 'rescue', 'always'):
            yield from tasks_of(task.get(key))


def include(task):
    inc = task.get('ansible.builtin.include_role')
    if isinstance(inc, dict):
        return (inc.get('name'), inc.get('tasks_from'))
    return None


v_tasks = [t for t in tasks_of(validate['tasks']) if include(t)]
v_includes = [include(t) for t in v_tasks]
want = [(NODE, 'kerbside_source'), (KERBSIDE, 'validate')]
if v_includes != want:
    sys.exit(f'{VALIDATE!r} includes {v_includes}, not {want}')
for task in v_tasks:
    if task.get('run_once'):
        sys.exit(f"{VALIDATE!r} runs {task['name']!r} run_once, so only the first Kerbside host is checked")
guarded = [t for t in validate['tasks']
           if t.get('rescue') and any(include(b) == (KERBSIDE, 'validate') for b in t.get('block') or [])]
if len(guarded) != 1:
    sys.exit(f"{VALIDATE!r} does not run the role's validate in a block with a rescue")

d_tasks = [t for t in tasks_of(deploy['tasks']) if include(t)]
d_includes = [include(t) for t in d_tasks]
want = [
    (NODE, 'kerbside_source'),
    (KERBSIDE, 'bootstrap'),
    (INTERNAL_CA, 'host_certificate'),
    (INTERNAL_CA, 'distribute_certificates'),
    (KERBSIDE, 'config'),
    (KERBSIDE, 'register'),
]
if d_includes != want:
    sys.exit(f'{DEPLOY!r} includes {d_includes}, not {want}')
cert_tasks = d_tasks[2:4]
for task in cert_tasks:
    if 'kerbside_proxy_cert_path' not in str(task.get('when', '')):
        sys.exit(f'{task["name"]!r} is not skipped when the certificate overrides are set')

# Both plays tell kerbside_source whether this host is a Shaken Fist node, and
# the Deploy play gives it the CA it read, after reading it.
COLOCATED = "{{ inventory_hostname in groups['allsf'] }}"
for play, tasks in ((validate, v_tasks), (deploy, d_tasks)):
    source_vars = tasks[0].get('vars') or {}
    if source_vars.get('kerbside_colocated') != COLOCATED:
        sys.exit(f'{play["name"]!r} passes kerbside_colocated {source_vars.get("kerbside_colocated")!r}')
slurps = [t for t in deploy['tasks'] if 'ansible.builtin.slurp' in t]
if len(slurps) != 1 or deploy['tasks'].index(slurps[0]) > deploy['tasks'].index(d_tasks[0]):
    sys.exit(f'{DEPLOY!r} does not read the CA once, before kerbside_source')
if str(slurps[0].get('register')) not in str((d_tasks[0].get('vars') or {}).get('kerbside_sf_ca_cert')):
    sys.exit(f"{DEPLOY!r} does not pass kerbside_source the CA it read, {slurps[0].get('register')!r}")

# Everything site.yml hands an entry point of the role is an argument that
# entry point declares: an include's own vars, the Deploy play's vars, and the
# fact kerbside_source sets for it.
for play, tasks in ((validate, v_tasks), (deploy, d_tasks)):
    for task in tasks:
        name, entry = include(task)
        if name != KERBSIDE:
            continue
        if entry not in specs:
            sys.exit(f'the installed kerbside role has no entry point {entry!r}, which {task["name"]!r} runs')
        declared = set(specs[entry].get('options') or {})
        handed = set(task.get('vars') or {}) | {'kerbside_reserved_ports'}
        if play is deploy:
            handed |= set(deploy.get('vars') or {})
        undeclared = handed - declared
        if undeclared:
            sys.exit(f'{task["name"]!r} hands the kerbside role {sorted(undeclared)}, which its {entry} '
                     'entry point does not declare')

# The validation play's own vars are visible to the role it includes, so none
# may share a name with a variable the role defines.
role_names = set(role_defaults) | set(role_vars)
for spec in specs.values():
    role_names |= set(spec.get('options') or {})
clash = set(validate.get('vars') or {}) & role_names
if clash:
    sys.exit(f'{VALIDATE!r} sets play vars {sorted(clash)}, which the kerbside role also defines')

# Play, task and include vars all outrank inventory group_vars (#4441), so they
# may set only names derived from the inventory, never a setting an operator
# makes: an operator's value would be silently ignored.
operator_args = set(node_specs['kerbside_source'].get('options') or {})
for spec in specs.values():
    operator_args |= set(spec.get('options') or {})
derived = {'kerbside_hosts', 'kerbside_sources', 'kerbside_colocated', 'kerbside_url_on_sf_nodes',
           'kerbside_sf_ca_cert'}
for play in (validate, deploy):
    bad = (set(play.get('vars') or {}) & operator_args) - derived
    if bad:
        sys.exit(f'{play["name"]!r} sets operator-facing play vars {sorted(bad)}')
    for task in tasks_of(play['tasks']):
        bad = (set(task.get('vars') or {}) & operator_args) - derived
        if bad:
            sys.exit(f'{task["name"]!r} in {play["name"]!r} sets operator-facing vars {sorted(bad)}')

# internal_ca installs the proxy certificate, key and CA as cert_dest_dir
# joined with each cert_dest_*_name, owned by cert_owner and cert_group, and
# the installed role reads them at kerbside_proxy_cert_file,
# kerbside_proxy_key_file and kerbside_cacert_file, as kerbside_file_owner and
# kerbside_group. site.yml derives the one from the variables which decide the
# other, so the two must agree with nothing overridden, and with each override.
env = jinja2.Environment()


def resolve(name, values, seen=()):
    value = values[name]
    if not isinstance(value, str) or '{' not in value:
        return value
    refs = meta.find_undeclared_variables(env.parse(value))
    context = {r: resolve(r, values, seen + (name,)) for r in refs if r in values and r not in seen}
    return env.from_string(value).render(context)


OVERRIDES = [
    {},
    {'kerbside_config_dir': '/opt/kerbside'},
    {'kerbside_pki_dir': '/opt/kerbside-pki'},
    {'kerbside_proxy_cert_name': 'proxy.crt', 'kerbside_proxy_key_name': 'proxy.key',
     'kerbside_proxy_cacert_name': 'proxy-ca.crt'},
    {'kerbside_file_owner': 'operator', 'kerbside_group': 'consoles'},
]
PATHS = (('cert_dest_cert_name', 'kerbside_proxy_cert_file'), ('cert_dest_key_name', 'kerbside_proxy_key_file'),
         ('cert_dest_ca_name', 'kerbside_cacert_file'))
OWNERS = (('cert_owner', 'kerbside_file_owner'), ('cert_group', 'kerbside_group'))
COMPARED = ['cert_dest_dir'] + [p[0] for p in PATHS] + [o[0] for o in OWNERS]
for task in cert_tasks:
    task_vars = task.get('vars') or {}
    missing = sorted(set(COMPARED) - set(task_vars))
    if missing:
        sys.exit(f'{task["name"]!r} leaves {missing} to internal_ca, whose defaults are not what the kerbside '
                 'role reads')
    for override in OVERRIDES:
        site = {k: env.from_string(str(task_vars[k])).render(override) for k in COMPARED}
        # Role vars outrank inventory; defaults do not. Every override names a
        # default.
        values = dict(role_defaults)
        values.update(override)
        values.update(role_vars)
        label = override or 'no override'
        for site_name, role_file in PATHS:
            installed = f"{site['cert_dest_dir']}/{site[site_name]}"
            read = resolve(role_file, values)
            if installed != read:
                sys.exit(f'with {label}, {task["name"]!r} installs {installed}, but the installed kerbside '
                         f'role reads its {role_file} {read}')
        for site_name, role_name in OWNERS:
            if site[site_name] != resolve(role_name, values):
                sys.exit(f'with {label}, {task["name"]!r} passes {site_name} {site[site_name]!r}, but the '
                         f'installed kerbside role uses {role_name} {resolve(role_name, values)!r}')
PYEOF
)" || fail "${out}"
}

case_2_only_include_role() {
    CURRENT_CASE='case 2: site.yml reaches shakenfist.kerbside only through include_role'
    # import_role and a play's roles: are resolved while ansible parses the
    # playbook, whether or not the play would run, so either makes site.yml
    # unusable without the collection.
    local out
    out="$("${PY}" - "${SITE_YML}" << 'PYEOF' 2>&1
import re
import sys

import yaml

with open(sys.argv[1]) as f:
    plays = yaml.safe_load(f)

ROLE = re.compile(r'^shakenfist\.kerbside\.')
IMPORTS = ('import_role', 'ansible.builtin.import_role', 'ansible.legacy.import_role')
INCLUDES = ('include_role', 'ansible.builtin.include_role', 'ansible.legacy.include_role')


def role_name(ref):
    if isinstance(ref, dict):
        return str(ref.get('role') or ref.get('name') or '')
    m = re.search(r'(?:^|\s)name=(\S+)', str(ref))
    return m.group(1) if m else str(ref)


def tasks_of(tasks):
    for task in tasks or []:
        yield task
        for key in ('block', 'rescue', 'always'):
            yield from tasks_of(task.get(key))


problems = []
includes = 0
for play in plays:
    for ref in play.get('roles') or []:
        if ROLE.match(role_name(ref)):
            problems.append(f'the play {play.get("name")!r} lists {role_name(ref)} in roles:')
    for section in ('pre_tasks', 'tasks', 'post_tasks', 'handlers'):
        for task in tasks_of(play.get(section)):
            for key in IMPORTS:
                if key in task and ROLE.match(role_name(task[key])):
                    problems.append(f'{task.get("name")!r} imports {role_name(task[key])} with {key}')
            for key in INCLUDES:
                if key in task and ROLE.match(role_name(task[key])):
                    includes += 1
if problems:
    sys.exit('; '.join(problems))
if not includes:
    sys.exit('site.yml includes no shakenfist.kerbside role, so this case checks nothing')
PYEOF
)" || fail "${out}"
    if grep -v '^[[:space:]]*#' "${SITE_YML}" | grep -E 'import_role|roles:' | grep -q kerbside; then
        fail 'a line of site.yml names kerbside beside import_role or roles:'
    fi
}

# hosts_by_play LOG: "NAME<TAB>host,host" per play, in order, from
# ansible-playbook --list-hosts output.
hosts_by_play() {
    "${PY}" - "$1" << 'PYEOF'
import re
import sys

plays = []
with open(sys.argv[1]) as f:
    for line in f:
        m = re.match(r'^  play #\d+ \([^)]*\): (.*?)\tTAGS:', line)
        if m:
            plays.append((m.group(1), []))
        elif plays and re.match(r'^      \S', line):
            plays[-1][1].append(line.strip())
for name, hosts in plays:
    print(name + '\t' + ','.join(sorted(hosts)))
PYEOF
}

list_hosts() {
    run "${WITH}" -i "$1" --list-hosts "${SITE_YML}" || fail 'ansible-playbook --list-hosts failed'
    hosts_by_play "${LAST_LOG}"
}

case_3_list_hosts() {
    CURRENT_CASE='case 3: site.yml reaches a dedicated Kerbside host only in its plays'
    # The reachable group is built by add_host at run time, which
    # --list-hosts cannot see, so the inventory declares it.
    local common='[allsf]
sf-1
sf-2
sf-3
[hypervisors]
sf-1
sf-2
[network_node]
sf-1
[database_node]
sf-3
[etcd_master]'
    printf '%s\n[kerbside]\nsf-2\nkb-1\n[reachable:children]\nallsf\nkerbside\n' "${common}" \
        > "${WORK}/inventory-on.ini"
    printf '%s\n[reachable:children]\nallsf\n' "${common}" > "${WORK}/inventory-off.ini"

    list_hosts "${WORK}/inventory-on.ini" > "${WORK}/plays-on.tsv"
    local name hosts
    while IFS=$'\t' read -r name hosts; do
        case "${name}" in
            "${PROBE_PLAY}")
                [ "${hosts}" = 'kb-1,sf-1,sf-2,sf-3' ] || fail "the probe play's hosts are ${hosts}" ;;
            "${VALIDATE_PLAY}"|"${DEPLOY_PLAY}")
                [ "${hosts}" = 'kb-1,sf-2' ] || fail "the play '${name}' has hosts ${hosts}" ;;
            *)
                if [[ ",${hosts}," == *,kb-1,* ]]; then
                    fail "the dedicated Kerbside host is in the play '${name}'"
                fi ;;
        esac
    done < "${WORK}/plays-on.tsv"
    local play
    for play in "${PROBE_PLAY}" "${VALIDATE_PLAY}" "${DEPLOY_PLAY}"; do
        grep -q "^${play}"$'\t' "${WORK}/plays-on.tsv" || fail "no play named '${play}'"
    done
}

case_4_list_hosts_feature_off() {
    CURRENT_CASE='case 4: without a kerbside group, site.yml targets what it did before'
    [ -f "${WORK}/plays-on.tsv" ] || fail 'case 4 needs case 3 to have run'
    list_hosts "${WORK}/inventory-off.ini" > "${WORK}/plays-off.tsv"
    # Every play as with the group, less kb-1 everywhere and sf-2 from the
    # two Kerbside plays, which match nothing.
    "${PY}" - "${WORK}/plays-on.tsv" > "${WORK}/plays-expected.tsv" << PYEOF
import sys

for line in open(sys.argv[1]):
    name, hosts = line.rstrip('\n').split('\t')
    hosts = [h for h in hosts.split(',') if h and h != 'kb-1']
    if name in ('${VALIDATE_PLAY}', '${DEPLOY_PLAY}'):
        hosts = []
    print(name + '\t' + ','.join(hosts))
PYEOF
    diff -u "${WORK}/plays-expected.tsv" "${WORK}/plays-off.tsv" > "${WORK}/plays.diff" \
        || fail "the feature-off plays differ: $(cat "${WORK}/plays.diff")"
}

case_5_syntax() {
    local playbook
    for playbook in _shared cluster single-node; do
        CURRENT_CASE="case 5: examples/${playbook}/site.yml parses with shakenfist.kerbside installed"
        run "${WITH}" -i localhost, --syntax-check "${REPO_ROOT}/examples/${playbook}/site.yml" \
            || fail 'the syntax check failed'
        CURRENT_CASE="case 5: examples/${playbook}/site.yml parses without shakenfist.kerbside"
        run "${WITHOUT}" -i localhost, --syntax-check "${REPO_ROOT}/examples/${playbook}/site.yml" \
            || fail 'the syntax check failed'
    done
}

# The node role's kerbside_source entry point, run alone, with its facts
# written to files a case can read.
cat > "${WORK}/kerbside-source.yml" << 'EOF'
---
- name: Run the node role's kerbside_source entry point
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Build this cluster's Kerbside source and reserved ports
      ansible.builtin.include_role:
        name: shakenfist.shakenfist.node
        tasks_from: kerbside_source

    - name: Write the source
      ansible.builtin.copy:
        content: "{{ kerbside_sf_source | to_json }}"
        dest: "{{ dump_dir }}/source.json"
        mode: '0600'
      no_log: true

    - name: Write the reserved ports
      ansible.builtin.copy:
        content: "{{ kerbside_reserved_ports | to_json }}"
        dest: "{{ dump_dir }}/ports.json"
        mode: '0600'
EOF

# source_vars NAME [JSON_MEMBERS]: kerbside_source's inputs for one run, as
# @path: valid for a dedicated Kerbside host with the CA known, with the
# members given replacing them.
source_vars() {
    write_vars "$1" "{\"kerbside_system_key\": \"${SYSTEM_KEY}\", \"kerbside_url\": \"${URL}\",
        \"kerbside_url_on_sf_nodes\": \"${URL}\", \"deploy_name\": \"cl1\", \"api_url\": \"${API_URL}\",
        \"kerbside_sf_ca_cert\": $(json_string "${SF_CA_FILE}"), \"kerbside_colocated\": false,
        \"dump_dir\": \"${WORK}/source\"${2:+, $2}}"
}

# source_refuses DESCRIPTION JSON_MEMBERS MESSAGE: kerbside_source must refuse
# the inputs with the message.
source_refuses() {
    CURRENT_CASE="case 6: kerbside_source refuses $1"
    if run "${WITHOUT}" -i localhost, -v -e "$(source_vars refuse "$2")" "${WORK}/kerbside-source.yml"; then
        fail 'kerbside_source accepted it'
    fi
    assert_output_has 'fatal: [localhost]: FAILED!'
    assert_output_has "$3"
    assert_output_lacks "${SYSTEM_KEY}"
}

case_6_kerbside_source() {
    local mode members colocated out
    mkdir -p "${WORK}/source"
    for mode in ca colocated placeholder; do
        CURRENT_CASE="case 6: kerbside_source builds the source and reserved ports (${mode})"
        rm -f "${WORK}/source/source.json" "${WORK}/source/ports.json"
        case "${mode}" in
            ca) members='' colocated=false ;;
            colocated) members='"kerbside_colocated": true' colocated=true ;;
            placeholder) members='"kerbside_sf_ca_cert": null' colocated=false ;;
        esac
        run "${WITHOUT}" -i localhost, -vvv -e "$(source_vars "source-${mode}" "${members}")" \
            "${WORK}/kerbside-source.yml" || fail 'kerbside_source failed'
        assert_output_lacks "${SYSTEM_KEY}"
        # Kerbside's own check of a sources list, from the installed role.
        "${PY}" -c 'import json, sys; print(json.dumps([json.load(open(sys.argv[1]))]))' \
            "${WORK}/source/source.json" > "${WORK}/source/sources.json"
        "${PY}" -I "${KERBSIDE_ROLE}/files/kerbside-check-sources.py" --json - \
            < "${WORK}/source/sources.json" > "${WORK}/source/check.out" 2>&1 \
            || fail "the installed role's sources check refuses the source: $(cat "${WORK}/source/check.out")"
        out="$("${PY}" - "${WORK}/source" "${mode}" "${SYSTEM_KEY}" "${SF_CA_FILE}" "${API_URL}" "${colocated}" \
            << 'PYEOF' 2>&1
import json
import sys

where, mode, key, ca_path, api_url, colocated = sys.argv[1:]
with open(where + '/source.json') as f:
    source = json.load(f)
with open(where + '/ports.json') as f:
    ports = json.load(f)
with open(ca_path) as f:
    ca = f.read()
want = {'source': 'cl1', 'type': 'shakenfist', 'url': api_url, 'username': 'system', 'password': key}
problems = []
if sorted(source) != sorted(list(want) + ['ca_cert']):
    problems.append(f'its fields are {sorted(source)}')
for field, value in want.items():
    if source.get(field) != value:
        problems.append(f'its {field} is not {"kerbside_system_key" if field == "password" else repr(value)}')
if mode != 'placeholder' and source.get('ca_cert') != ca:
    problems.append('its ca_cert is not the CA, byte for byte')
if mode == 'placeholder' and ('BEGIN CERTIFICATE' in source.get('ca_cert', '') or not source.get('ca_cert')):
    problems.append(f'its ca_cert is {source.get("ca_cert")!r}, not a placeholder')
want_ports = ([13000, 13001, 13005, 13006, 13007] if colocated == 'true' else []) + [[30000, 65535]]
if ports != want_ports:
    problems.append(f'the reserved ports are {ports}, not {want_ports}')
if problems:
    sys.exit('; '.join(problems))
PYEOF
)" || fail "${out}"
    done

    source_refuses 'an empty key' '"kerbside_system_key": ""' \
        'kerbside_system_key is empty on localhost, but the kerbside group is not.'
    source_refuses 'a CA which is not a PEM' '"kerbside_sf_ca_cert": "not a certificate"' \
        'kerbside_sf_ca_cert does not hold a PEM certificate.'
    source_refuses 'an empty CA' '"kerbside_sf_ca_cert": ""' 'kerbside_sf_ca_cert does not hold a PEM certificate.'
    local host
    for host in 127.0.0.1 127.1 localhost api.localhost 0.0.0.0 '[::1]' '[0:0::1]' '[::ffff:127.0.0.1]'; do
        source_refuses "a loopback api_url (${host}) on a host which is not a Shaken Fist node" \
            "\"api_url\": \"http://${host}:13000\"" 'a loopback address, but localhost is not a Shaken Fist node'
    done
    source_refuses 'a kerbside_url the Shaken Fist nodes see differently' \
        '"kerbside_url_on_sf_nodes": "https://other.example.com"' \
        "Shaken Fist's nodes render a different kerbside_url, https://other.example.com."
    source_refuses 'a kerbside_url the Shaken Fist nodes do not have' '"kerbside_url_on_sf_nodes": ""' \
        "Shaken Fist's nodes render no kerbside_url at all."

    CURRENT_CASE='case 6: kerbside_source accepts a loopback api_url on a Shaken Fist node'
    run "${WITHOUT}" -i localhost, -e "$(source_vars loopback-colocated \
        '"api_url": "http://localhost:13000", "kerbside_colocated": true')" "${WORK}/kerbside-source.yml" \
        || fail 'kerbside_source refused it'

    CURRENT_CASE="case 6: kerbside_source skips the kerbside_url check without the Shaken Fist nodes' value"
    run "${WITHOUT}" -i localhost, -e "$(source_vars url-unknown '"kerbside_url_on_sf_nodes": null')" \
        "${WORK}/kerbside-source.yml" || fail 'kerbside_source refused it'
}

case_7_site_validation_runs() {
    CURRENT_CASE='case 7: site.yml refuses a kerbside_url only the Kerbside hosts see'
    reset_site_vars
    site_inventory kb-1
    echo "kerbside_url: '${URL}'" >> "${WORK}/site/group_vars/kerbside.yml"
    echo "api_url: ${API_URL}" > "${WORK}/site/group_vars/all.yml"
    run_site_head "${WITH}" && fail 'the validation play passed it'
    assert_output_has "Shaken Fist's nodes render no kerbside_url at all"
    assert_output_has 'fatal: [kb-1]: FAILED!'
    assert_later_play_did_not_run

    # The cluster shape play judges kerbside_url from the Shaken Fist nodes'
    # inventory, before the Kerbside validation play, with or without a
    # Kerbside host.
    CURRENT_CASE='case 7: site.yml refuses a kerbside_url which ends in a slash'
    reset_site_vars
    site_inventory
    printf "kerbside_url: '%s/'\napi_url: %s\n" "${URL}" "${API_URL}" > "${WORK}/site/group_vars/all.yml"
    run_site_head "${WITH}" && fail 'the cluster shape play passed a kerbside_url ending in a slash'
    assert_output_has "kerbside_url is ${URL}/, which ends in a slash."
    assert_output_has 'fatal: [localhost]: FAILED!'
    assert_output_lacks "PLAY [${VALIDATE_PLAY}]"
    assert_later_play_did_not_run

    CURRENT_CASE="case 7: site.yml refuses a kerbside_url one Shaken Fist node sees differently"
    reset_site_vars
    SF_NODES=$'sf-1\nsf-2' site_inventory kb-1
    echo "kerbside_url: 'https://other.example.com'" > "${WORK}/site/host_vars/sf-2.yml"
    run_site_head "${WITH}" && fail "the cluster shape play passed a second Shaken Fist node's own kerbside_url"
    assert_output_has 'failed: [localhost] (item=sf-2)'
    assert_output_has "sf-2 sees kerbside_url as https://other.example.com, but sf-1 sees ${URL}."
    assert_output_lacks 'failed: [localhost] (item=sf-1)'
    assert_output_lacks "PLAY [${VALIDATE_PLAY}]"
    assert_later_play_did_not_run

    CURRENT_CASE="case 7: site.yml passes two Shaken Fist nodes which agree on kerbside_url"
    reset_site_vars
    SF_NODES=$'sf-1\nsf-2' site_inventory kb-1
    run_site_head "${WITH}" || fail 'the cluster shape play refused two nodes with one kerbside_url'
    assert_task_ok 'Fail if a Shaken Fist node sees kerbside_url differently from the first' localhost
    assert_task_ok 'Fail if kerbside_url ends in a slash' localhost
    assert_later_play_ran

    CURRENT_CASE="case 7: site.yml refuses the default, loopback, api_url on a dedicated Kerbside host"
    site_inventory kb-1
    reset_site_vars
    echo "kerbside_url: '${URL}'" > "${WORK}/site/group_vars/all.yml"
    run_site_head "${WITH}" && fail 'the validation play passed it'
    assert_output_has 'a loopback address, but kb-1 is not a Shaken Fist node'
    assert_later_play_did_not_run

    # At -vvv, for the secrets check.
    CURRENT_CASE='case 7: site.yml passes a valid inventory, with the released collection'
    reset_site_vars
    run_site_head "${WITH}" -vvv || fail 'the validation play refused a valid inventory'
    assert_task_ok "shakenfist.shakenfist.node : Refuse a kerbside_url which Shaken Fist's nodes see differently" kb-1
    assert_task_ok 'shakenfist.kerbside.kerbside : Refuse a kerbside_sources Kerbside could not use' kb-1
    assert_task_ok "Refuse a shakenfist.kerbside outside the range requirements.yml pins" kb-1
    assert_output_lacks 'WARNING: shakenfist.kerbside'
    assert_later_play_ran

    # The first Kerbside host is valid and the second is not, through a
    # host_vars override only the second sees.
    CURRENT_CASE='case 7: site.yml validates every Kerbside host, not only the first, and stops them all'
    site_inventory kb-1 kb-2
    echo 'kerbside_metrics_port: 30001' > "${WORK}/site/host_vars/kb-2.yml"
    run_site_head "${WITH}" && fail "the validation play passed the second Kerbside host's console range port"
    assert_output_has 'failed: [kb-2] (item=kerbside_metrics_port)'
    assert_output_has 'kerbside_metrics_port is 30001 on kb-2, but kerbside_reserved_ports reserves 30000-65535'
    assert_output_has 'The kerbside role refused the Kerbside configuration on kb-2'
    assert_output_has 'on another Kerbside host, for the reason above, so kb-1 stops too.'
    assert_output_lacks 'failed: [kb-1] (item='
    assert_later_play_did_not_run
    reset_site_vars

    # A co-located Kerbside host may not take sf-api's port; a dedicated one
    # may, so site.yml must tell the two apart.
    CURRENT_CASE="case 7: site.yml refuses a Shaken Fist daemon's port on a co-located Kerbside host only"
    site_inventory kb-1 sf-1
    echo 'kerbside_api_port: 13000' > "${WORK}/site/host_vars/kb-1.yml"
    echo 'kerbside_api_port: 13000' > "${WORK}/site/host_vars/sf-1.yml"
    run_site_head "${WITH}" && fail "the validation play passed sf-api's port on a co-located Kerbside host"
    assert_output_has 'failed: [sf-1] (item=kerbside_api_port)'
    assert_output_has 'kerbside_api_port is 13000 on sf-1, but kerbside_reserved_ports reserves 13000 there'
    assert_output_lacks 'failed: [kb-1] (item='
    reset_site_vars

    # api_url is judged per host: a co-located host's loopback api_url reaches
    # its own sf-api, whatever another Kerbside host is, and a dedicated
    # host's does not.
    CURRENT_CASE="case 7: site.yml passes a co-located host's loopback api_url beside a dedicated host"
    echo 'api_url: http://localhost:13000' > "${WORK}/site/host_vars/sf-1.yml"
    run_site_head "${WITH}" || fail "the validation play refused a co-located host's loopback api_url"
    assert_task_ok \
        'shakenfist.shakenfist.node : Refuse a loopback api_url on a Kerbside host which is not a Shaken Fist node' \
        sf-1

    CURRENT_CASE="case 7: site.yml refuses a dedicated host's loopback api_url only"
    echo 'api_url: http://127.0.0.1:13000' > "${WORK}/site/host_vars/kb-1.yml"
    run_site_head "${WITH}" && fail "the validation play passed a dedicated host's loopback api_url"
    assert_output_has 'fatal: [kb-1]: FAILED!'
    assert_output_has "api_url's host on kb-1 is 127.0.0.1, a loopback address, but kb-1 is not a Shaken Fist node"
    assert_output_lacks "api_url's host on sf-1"
    assert_later_play_did_not_run
    reset_site_vars
}

INSTALL_MESSAGE='shakenfist.kerbside is not installed; run ansible-galaxy collection install -r'
INSTALL_MESSAGE+=' examples/_shared/requirements.yml'

case_8_missing_collection() {
    CURRENT_CASE='case 8: without shakenfist.kerbside or a Kerbside host, the validation play does nothing'
    reset_site_vars
    site_inventory
    run_site_head "${WITHOUT}" || fail 'the head of site.yml failed'
    grep -A1 -F "PLAY [${VALIDATE_PLAY}]" "${LAST_LOG}" | grep -q 'skipping: no hosts matched' \
        || fail 'the validation play matched a host'
    assert_output_lacks 'shakenfist.kerbside'
    assert_output_lacks 'kerbside_source'
    assert_later_play_ran

    CURRENT_CASE='case 8: without shakenfist.kerbside, a Kerbside host stops the run with the install command'
    site_inventory kb-1 sf-1
    run_site_head "${WITHOUT}" && fail 'the validation play passed'
    assert_output_has "${INSTALL_MESSAGE}"
    assert_output_lacks 'The kerbside role refused'
    assert_later_play_did_not_run
}

# stub_collection VERSION: a copy of the installed collection, at
# ${WORK}/stub, which reports VERSION, or no version at all when it is empty.
stub_collection() {
    rm -rf "${WORK}/stub"
    mkdir -p "${WORK}/stub"
    cp -a "${WORK}/kerbside/ansible_collections" "${WORK}/stub/"
    local vars="${WORK}/stub/ansible_collections/shakenfist/kerbside/roles/kerbside/vars/main.yml"
    grep -q '^kerbside_collection_version: ' "${vars}" || fail 'the installed role reports no version'
    if [ -n "$1" ]; then
        sed -i "s/^kerbside_collection_version: .*/kerbside_collection_version: \"$1\"/" "${vars}"
    else
        sed -i '/^kerbside_collection_version: /d' "${vars}"
    fi
}

# version_run VERSION RANGE: the head of site.yml with a collection reporting
# VERSION, and a requirements.yml pinning RANGE. Returns its status.
version_run() {
    stub_collection "$1"
    printf "collections:\n  - name: shakenfist.kerbside\n    version: '%s'\n" "$2" \
        > "${WORK}/site/requirements.yml"
    run_site_head "${WORK}/sf:${WORK}/stub"
}

case_9_version_range() {
    reset_site_vars
    site_inventory kb-1
    # The range is read from the requirements file beside site.yml, and
    # compared clause by clause as ansible-galaxy compares it.
    local range='>=1.2.0,!=1.2.3,<1.3.0' version
    for version in 1.1.9 1.2.3 1.3.0 2.0.0 0.8.0; do
        CURRENT_CASE="case 9: site.yml refuses shakenfist.kerbside ${version} when requirements.yml pins ${range}"
        version_run "${version}" "${range}" && fail 'the validation play passed'
        assert_output_has "shakenfist.kerbside ${version} is installed, but requirements.yml pins ${range}."
        assert_output_has 'Install a release in that range with ansible-galaxy collection install --force -r'
        assert_later_play_did_not_run
    done
    for version in 1.2.0 1.2.1 1.2.99; do
        CURRENT_CASE="case 9: site.yml accepts shakenfist.kerbside ${version} when requirements.yml pins ${range}"
        version_run "${version}" "${range}" || fail 'the validation play refused it'
        assert_output_lacks 'WARNING: shakenfist.kerbside'
        assert_later_play_ran
    done

    CURRENT_CASE='case 9: a bare version in requirements.yml pins exactly that version'
    version_run 1.2.0 1.2.0 || fail 'the validation play refused 1.2.0'
    version_run 1.2.1 1.2.0 && fail 'the validation play passed 1.2.1'
    assert_output_has 'shakenfist.kerbside 1.2.1 is installed, but requirements.yml pins 1.2.0.'

    CURRENT_CASE='case 9: site.yml refuses a shakenfist.kerbside which reports no version'
    version_run '' "${range}" && fail 'the validation play passed'
    assert_output_has 'shakenfist.kerbside does not report its version, so it predates the releases this playbook'

    CURRENT_CASE='case 9: site.yml accepts a checkout of shakenfist.kerbside, with a warning naming it'
    version_run unreleased "${range}" || fail 'the validation play refused it'
    assert_output_has 'WARNING: shakenfist.kerbside reports its version as unreleased'
    assert_output_has "It is installed at ${WORK}/stub/ansible_collections/shakenfist/kerbside."
    assert_later_play_ran

    CURRENT_CASE='case 9: a refusal by an out-of-range collection says the version may be why'
    echo 'kerbside_metrics_port: 40000' > "${WORK}/site/host_vars/kb-1.yml"
    version_run 1.3.0 "${range}" && fail 'the validation play passed'
    assert_output_has "shakenfist.kerbside 1.3.0 is installed, but requirements.yml pins ${range}, which may be why"
    assert_output_lacks "${INSTALL_MESSAGE}"
    reset_site_vars
}

case_10_extra_sources() {
    CURRENT_CASE="case 10: kerbside_extra_sources follow this cluster's source"
    # The kerbside_sources expressions site.yml gives the validate include and
    # the Deploy play, evaluated after the validation play, as the role would
    # see them.
    local out
    out="$("${PY}" - "${SITE_YML}" "${WORK}/site/site-head.yml" "${WORK}/site/site-sources.yml" \
        << 'PYEOF' 2>&1
import sys

import yaml

site, head, dest = sys.argv[1:]
with open(site) as f:
    plays = yaml.safe_load(f)
with open(head) as f:
    head_plays = yaml.safe_load(f)


def tasks_of(tasks):
    for task in tasks or []:
        yield task
        for key in ('block', 'rescue', 'always'):
            yield from tasks_of(task.get(key))


validate = [p for p in plays if p.get('name') == 'Validate the Kerbside configuration'][0]
include = [t for t in tasks_of(validate['tasks'])
           if (t.get('ansible.builtin.include_role') or {}).get('tasks_from') == 'validate'][0]
deploy = [p for p in plays if p.get('name') == 'Deploy Kerbside'][0]
expressions = {'validate': include['vars']['kerbside_sources'], 'deploy': deploy['vars']['kerbside_sources']}
head_plays.append({
    'name': 'Write the source names site.yml hands the role',
    'hosts': 'kerbside',
    'gather_facts': False,
    'tasks': [{
        'name': f'Write the names the {where} gets',
        'ansible.builtin.copy': {
            'content': "{{ kerbside_sources | map(attribute='source') | to_json }}",
            'dest': '{{ dump }}-%s.json' % where,
            'mode': '0600',
        },
        'vars': {'kerbside_sources': expression},
        'delegate_to': 'localhost',
    } for where, expression in expressions.items()],
})
with open(dest, 'w') as f:
    yaml.safe_dump(head_plays, f, sort_keys=False)
PYEOF
)" || fail "${out}"
    reset_site_vars
    site_inventory kb-1
    printf 'kerbside_extra_sources:\n  - {source: lab, type: static, consoles: []}\n' \
        >> "${WORK}/site/group_vars/kerbside.yml"
    run "${WITH}" -i "${WORK}/site/hosts.ini" -e "dump=${WORK}/names" "${WORK}/site/site-sources.yml" \
        || fail 'the validation play refused an extra source'
    local where
    for where in validate deploy; do
        [ "$(cat "${WORK}/names-${where}.json")" = '["sf", "lab"]' ] \
            || fail "the ${where} gets the sources $(cat "${WORK}/names-${where}.json"), not [\"sf\", \"lab\"]"
    done

    CURRENT_CASE="case 10: an extra source is checked with this cluster's"
    reset_site_vars
    printf 'kerbside_extra_sources:\n  - {source: sf, type: static, consoles: []}\n' \
        >> "${WORK}/site/group_vars/kerbside.yml"
    run_site_head "${WITH}" && fail 'the validation play passed two sources named sf'
    assert_output_has 'sources 0 and 1 share a source name'
    reset_site_vars
}

case_11_deploy_source() {
    CURRENT_CASE="case 11: the Deploy play's kerbside_sources carries the CA, and passes the role's validate"
    # The Deploy play's vars and its kerbside_source include, extracted from
    # site.yml, with the CA a slurp would return, then the role's validate on
    # what bootstrap, config and register would be given.
    local out
    out="$("${PY}" - "${SITE_YML}" "${WORK}/site/deploy-source.yml" << 'PYEOF' 2>&1
import sys

import yaml

site, dest = sys.argv[1:]
with open(site) as f:
    plays = yaml.safe_load(f)
deploy = [p for p in plays if p.get('name') == 'Deploy Kerbside'][0]
tasks = [t for t in deploy['tasks']
         if (t.get('ansible.builtin.include_role') or {}).get('tasks_from') == 'kerbside_source']
if len(tasks) != 1:
    sys.exit('expected one kerbside_source include in the Deploy play')
deploy['tasks'] = tasks + [{
    'name': 'Validate what the role would be given',
    'ansible.builtin.include_role': {'name': 'shakenfist.kerbside.kerbside', 'tasks_from': 'validate'},
}, {
    'name': 'Write the CA the source carries',
    'ansible.builtin.copy': {'content': '{{ kerbside_sources[0].ca_cert }}', 'dest': '{{ dump }}', 'mode': '0600'},
    'no_log': True,
}]
deploy['become'] = False
with open(dest, 'w') as f:
    yaml.safe_dump([deploy], f, sort_keys=False)
PYEOF
)" || fail "${out}"
    reset_site_vars
    site_inventory kb-1
    printf '[reachable:children]\nkerbside\nallsf\n' >> "${WORK}/site/hosts.ini"
    run "${WITH}" -i "${WORK}/site/hosts.ini" -e "dump=${WORK}/deploy-ca.pem" \
        -e "$(write_vars slurp "{\"kerbside_sf_ca_slurp\": {\"content\": \"$(base64 -w0 < "${SF_CA_FILE}")\"}}")" \
        "${WORK}/site/deploy-source.yml" || fail 'the Deploy play could not build a source the role accepts'
    cmp -s "${WORK}/deploy-ca.pem" "${SF_CA_FILE}" || fail "the source's ca_cert is not the CA, byte for byte"
}

# The Kerbside proxy certificate, issued by internal_ca with the variables
# site.yml's "Deploy Kerbside" play passes it. The task is extracted from
# site.yml by name and run as it stands, so the test cannot drift from what the
# play does; only ca_path and kerbside_public_fqdn are extra vars.
# issue_proxy_cert CA_DIR PUBLIC_FQDN: leaves the certificate in CA_DIR.
issue_proxy_cert() {
    local ca_dir="$1" public="$2" out
    if [ ! -f "${WORK}/issue-proxy-cert.yml" ]; then
        out="$("${PY}" - "${SITE_YML}" "${WORK}/issue-proxy-cert.yml" "${DEPLOY_PLAY}" << 'PYEOF' 2>&1
import sys

import yaml

site, dest, name = sys.argv[1:]
with open(site) as f:
    plays = yaml.safe_load(f)
deploy = [p for p in plays if p.get('name') == name]
if len(deploy) != 1:
    sys.exit(f'expected one play named {name!r}')
issue = [t for t in deploy[0]['tasks'] if t.get('name', '').startswith('Issue this host')]
if len(issue) != 1:
    sys.exit("expected one task issuing the host's Kerbside proxy certificate")
bootstrap = {
    'name': 'Create the CA',
    'ansible.builtin.include_role': {'name': 'shakenfist.shakenfist.internal_ca', 'tasks_from': 'bootstrap'},
}
with open(dest, 'w') as f:
    yaml.safe_dump([{'name': 'Issue', 'hosts': 'localhost', 'gather_facts': False,
                     'tasks': [bootstrap] + issue}], f, sort_keys=False)
PYEOF
)" || fail "${out}"
    fi
    mkdir -p "${ca_dir}"
    ANSIBLE_SKIP_TAGS=packages run "${WITH}" -i localhost, -c local -e ansible_become=false \
        -e "$(write_vars proxy-cert "{\"ca_path\": \"${ca_dir}\", \"kerbside_public_fqdn\": \"${public}\"}")" \
        "${WORK}/issue-proxy-cert.yml" || fail "internal_ca could not issue a certificate for ${public}"
    [ -s "${ca_dir}/localhost-kerbside-server-cert.pem" ] || fail "no certificate was issued for ${public}"
}

# proxy_cert_sans CA_DIR: the issued certificate's subject alternative names,
# one "TYPE:value" per line, or nothing if it has none.
proxy_cert_sans() {
    openssl x509 -noout -ext subjectAltName -in "$1/localhost-kerbside-server-cert.pem" \
        | sed -n '2,$p' | tr ',' '\n' | sed 's/^ *//'
}

case_12_proxy_cert_ip_san() {
    local public sans
    for public in 10.0.1.12 ::1 fe80::1; do
        CURRENT_CASE="case 12: an address (${public}) as kerbside_public_fqdn gets an IP SAN and no DNS SAN"
        issue_proxy_cert "${WORK}/proxy-ca-ip-${public//:/_}" "${public}"
        sans="$(proxy_cert_sans "${WORK}/proxy-ca-ip-${public//:/_}")"
        printf '%s\n' "${sans}" | grep -q '^IP Address:' \
            || fail "the certificate for ${public} has no IP SAN: ${sans:-no SANs}"
        printf '%s\n' "${sans}" | grep -q '^DNS:' && fail "the certificate for ${public} has a DNS SAN: ${sans}"
    done
    return 0
}

case_13_proxy_cert_dns_san() {
    local public sans
    for public in kerbside.example.com localhost; do
        CURRENT_CASE="case 13: a name (${public}) as kerbside_public_fqdn gets a DNS SAN and no IP SAN"
        issue_proxy_cert "${WORK}/proxy-ca-dns-${public}" "${public}"
        sans="$(proxy_cert_sans "${WORK}/proxy-ca-dns-${public}")"
        printf '%s\n' "${sans}" | grep -qx "DNS:${public}" \
            || fail "the certificate for ${public} has no DNS SAN for it: ${sans:-no SANs}"
        printf '%s\n' "${sans}" | grep -q '^IP Address:' \
            && fail "the certificate for ${public} has an IP SAN: ${sans}"
    done
    return 0
}

case_14_secrets() {
    CURRENT_CASE='case 14: no secret reaches the ansible output'
    LAST_LOG=''
    # Prove the verbose runs happened, or their absence of secrets proves
    # nothing.
    grep -lq 'ESTABLISH LOCAL CONNECTION' "${WORK}"/logs/*.log || fail 'no run was verbose'
    local secret
    for secret in "${SECRETS[@]}"; do
        if grep -lF -- "${secret}" "${WORK}"/logs/*.log > "${WORK}/leaks" 2>&1; then
            fail "a secret reached the output of $(tr '\n' ' ' < "${WORK}/leaks")"
        fi
    done
}

CASES=(
    case_1_site_yml_structure
    case_2_only_include_role
    case_3_list_hosts
    case_4_list_hosts_feature_off
    case_5_syntax
    case_6_kerbside_source
    case_7_site_validation_runs
    case_8_missing_collection
    case_9_version_range
    case_10_extra_sources
    case_11_deploy_source
    case_12_proxy_cert_ip_san
    case_13_proxy_cert_dns_san
    case_14_secrets
)

START="${SECONDS}"
RAN=0
for name in "${CASES[@]}"; do
    number="${name#case_}"
    number="${number%%_*}"
    if [ $# -gt 0 ] && [[ " $* " != *" ${number} "* ]]; then
        continue
    fi
    "${name}"
    RAN=$((RAN + 1))
done
CURRENT_CASE='selection'
[ "${RAN}" -gt 0 ] || fail "no case is numbered $*"

echo "Kerbside wiring: ${RAN} of ${#CASES[@]} cases passed (${RUNS} ansible runs, $((SECONDS - START))s)"
