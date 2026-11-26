#!/bin/bash
# Copyright 2026 Michael Still and contributors
#
# ci-test-cross-node-reductions.sh -- run site.yml's cross-node reductions
# against a known dry multi-node inventory and check what they compute.
#
# The four reductions in the "Compute capability flags and cluster-wide
# values" play -- network_node_ip, mariadb_gateway_hosts, all_mesh_hosts
# and max_hypervisor_mtu -- are the highest-risk code in the deployer and
# had no automated assertion of any kind (issue #3311). They are also
# where a real outage came from: on 2026-07-20 one dead hypervisor
# aborted the sfcbr deploy on every healthy node, because a reduction
# over a runtime-gathered fact was taken over the whole inventory group
# instead of the hosts which answered.
#
# That is the distinction this checks, and it is not visible by reading
# one expression:
#
#   * reductions over static inventory host_vars (node_mesh_ip) take the
#     whole group, and should -- a node which is down for this run still
#     belongs in the mesh, so it rejoins when it returns;
#   * reductions over runtime facts (node_mtu) take the reachable subset,
#     because over the whole group they dereference a fact that was never
#     set on the absent host and fail the task on EVERY host.
#
# The expressions are extracted from site.yml rather than copied here, so
# this tests the file the deploy runs. An inventory where an absent
# hypervisor has a *lower* MTU than every reachable one is the case worth
# having: reducing over the whole group there succeeds and returns the
# wrong number, which an absent host with no fact at all would not catch.
#
# It needs no root, no cluster and no network. Nothing is connected to:
# every host is ansible_connection=local, because the reductions read
# groups and hostvars and compute facts, and touch no host.
#
# Run from anywhere, with ansible-playbook and python3 on PATH:
#
#     tools/ci-test-cross-node-reductions.sh
#
# KEEP_WORK=1 leaves the working directory, the generated playbook and
# the dumped facts behind.

set -euo pipefail

# fail, need, REPO_ROOT, COLLECTION_DIR, sf_test_workdir,
# sf_test_link_collection and sf_test_ansible_env.
# shellcheck source=tools/ci-test-ansible-lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/ci-test-ansible-lib.sh"

SITE_YML="${REPO_ROOT}/examples/_shared/site.yml"

need ansible-playbook 'install ansible-core (the sanity job gets it with ansible-lint)'
need python3 'install python3'

PY="$(python3 -c 'import sys; print(sys.executable)')"
"${PY}" -c 'import yaml' 2> /dev/null \
    || fail "${PY} cannot import yaml (ansible-core depends on it)"

sf_test_workdir ci-test-cross-node-reductions
# The reductions use only builtin filters, so nothing here needs the
# collection. It is linked anyway, for one sandbox shape across all four
# of these scripts, and so a reduction which grows a collection filter
# keeps working here rather than failing with a confusing lookup error.
sf_test_link_collection "${WORK}/collections"
sf_test_ansible_env "${WORK}/collections"
mkdir -p "${WORK}/facts"

CASES_RUN=0

# The reductions, in the order they have to run: each later one reads what
# an earlier one set.
FACTS=(database_tier_hosts reachable_hypervisors network_node_ip
       mariadb_gateway_hosts all_mesh_hosts max_hypervisor_mtu)

# Pull those set_fact tasks out of site.yml into a tasks file.
#
# By the fact each one sets, not by its task name: a task renamed in
# site.yml should not quietly drop out of this test, which is what
# matching on names would do. Exactly one definition of each is required,
# so a second place computing one of them fails here rather than leaving
# this testing whichever came first.
CURRENT_CASE='extract the reductions from site.yml'
EXTRACT_OUT="$("${PY}" - "${SITE_YML}" "${WORK}/reductions.yml" "${FACTS[@]}" \
    << 'PYEOF' 2>&1
import sys

import yaml


site_yml, out, *wanted = sys.argv[1:]

with open(site_yml) as f:
    plays = yaml.safe_load(f)


def tasks_of(block):
    """Every task in a play, including those nested in a block."""
    for task in block or []:
        yield task
        for key in ('block', 'rescue', 'always'):
            yield from tasks_of(task.get(key))


found = {}
for play in plays:
    if not isinstance(play, dict):
        continue
    for task in tasks_of(play.get('tasks')):
        facts = task.get('ansible.builtin.set_fact')
        if not isinstance(facts, dict):
            continue
        for name in facts:
            if name not in wanted:
                continue
            if name in found:
                sys.exit('%s is computed in more than one place in %s, so this '
                         'test would only be checking one of them'
                         % (name, site_yml))
            found[name] = {name: facts[name]}

missing = [name for name in wanted if name not in found]
if missing:
    sys.exit('%s no longer sets %s with set_fact; if a reduction was renamed '
             'or moved, this test has to move with it' % (site_yml, missing))

with open(out, 'w') as f:
    yaml.safe_dump(
        [{'name': 'Compute %s' % name,
          'ansible.builtin.set_fact': found[name]} for name in wanted],
        f, sort_keys=False, default_flow_style=False)
PYEOF
)" || fail "${EXTRACT_OUT}"

# The playbook which runs them. It runs on allsf:&reachable, as site.yml's
# play does, so the reachable intersection is part of what is tested
# rather than something this file models. Each host dumps what it computed,
# which is also how "these are cluster-wide values, identical on every
# host" gets checked.
cat > "${WORK}/play.yml" << 'EOF'
---
- name: Run site.yml's cross-node reductions
  hosts: allsf:&reachable
  gather_facts: false
  become: false
  tasks:
    - name: The reductions, as extracted from site.yml
      ansible.builtin.include_tasks: reductions.yml

    - name: Dump what this host computed
      ansible.builtin.copy:
        dest: "{{ facts_dir }}/{{ inventory_hostname }}.json"
        mode: "0600"
        content: |
          {{ {'database_tier_hosts': database_tier_hosts,
              'reachable_hypervisors': reachable_hypervisors,
              'network_node_ip': network_node_ip,
              'mariadb_gateway_hosts': mariadb_gateway_hosts,
              'all_mesh_hosts': all_mesh_hosts,
              'max_hypervisor_mtu': max_hypervisor_mtu} | to_nice_json }}
EOF

# One inventory per case. The hosts and their mesh IPs are the same
# throughout; what changes is which of them answered, and which MTUs they
# report.
write_inventory() {
    local reachable="$1" mtus="$2" out
    out="$("${PY}" - "${WORK}/inventory.yml" "${reachable}" "${mtus}" << 'PYEOF' 2>&1
import sys

import yaml


out, reachable, mtus = sys.argv[1:]

MESH = {
    'db1': '10.0.0.11',
    'db2': '10.0.0.12',
    'hv1': '10.0.0.21',
    'hv2': '10.0.0.22',
    'hv3': '10.0.0.23',
    'net1': '10.0.0.31',
}
GROUPS = {
    'database_node': ['db1', 'db2'],
    'hypervisors': ['hv1', 'hv2', 'hv3'],
    'network_node': ['net1'],
}

node_mtu = {}
for pair in mtus.split(','):
    host, value = pair.split('=')
    node_mtu[host] = int(value)

hosts = {}
for host, mesh_ip in MESH.items():
    variables = {'ansible_connection': 'local', 'node_mesh_ip': mesh_ip}
    if host in node_mtu:
        variables['node_mtu'] = node_mtu[host]
    hosts[host] = variables

children = {'allsf': {'hosts': hosts},
            'reachable': {'hosts': {h: None for h in reachable.split(',')}}}
for group, members in GROUPS.items():
    children[group] = {'hosts': {h: None for h in members}}

with open(out, 'w') as f:
    yaml.safe_dump({'all': {'children': children}}, f, sort_keys=False)
PYEOF
)" || fail "${out}"
}

run_case() {
    local reachable="$1" mtus="$2"
    rm -rf "${WORK}/facts"
    mkdir -p "${WORK}/facts"
    write_inventory "${reachable}" "${mtus}"
    LAST_LOG="${WORK}/logs/$(echo "${CURRENT_CASE}" | tr -c 'a-zA-Z0-9' '-').log"
    ansible-playbook -i "${WORK}/inventory.yml" \
        -e "facts_dir=${WORK}/facts" "${WORK}/play.yml" \
        < /dev/null > "${LAST_LOG}" 2>&1 \
        || fail 'the reductions did not run'
    CASES_RUN=$((CASES_RUN + 1))
}

# Assert over the dumped facts. Every expectation is "name=JSON" for a
# value which must match exactly, or "name~=JSON" for a list whose order
# is not guaranteed, and the values must be identical on every host which
# ran -- these are cluster-wide values, which is a claim worth checking
# rather than assuming.
#
# reachable_hypervisors is the ~= one, and it is not a convenience: the
# intersect filter is set-based, so it returns the hosts in whatever order
# the set iterates and not in inventory order. Nothing depends on that --
# it is only ever reduced with min() -- but asserting an order here would
# be asserting something ansible does not promise. mariadb_gateway_hosts
# is checked exactly, because it is built with unique and map, which do
# keep inventory order, and it is the gateway list the database clients
# are handed.
assert_facts() {
    local out
    out="$("${PY}" - "${WORK}/facts" "$@" << 'PYEOF' 2>&1
import glob
import json
import os
import sys


facts_dir, *pairs = sys.argv[1:]

files = sorted(glob.glob(os.path.join(facts_dir, '*.json')))
if not files:
    sys.exit('no host dumped any facts, so this case checked nothing')

computed = {}
for path in files:
    with open(path) as f:
        computed[os.path.basename(path)[:-len('.json')]] = json.load(f)

first = sorted(computed)[0]
for host in sorted(computed):
    if computed[host] != computed[first]:
        sys.exit('%s and %s computed different values for what site.yml '
                 'calls cluster-wide: %s against %s'
                 % (first, host, computed[first], computed[host]))

values = computed[first]
for pair in pairs:
    name, expected = pair.split('=', 1)
    unordered = name.endswith('~')
    name = name.rstrip('~')
    if name not in values:
        sys.exit('%s was not computed at all' % name)
    want = json.loads(expected)
    found = values[name]
    if unordered:
        found, want = sorted(found), sorted(want)
    if found != want:
        sys.exit('%s is %r, expected %r%s (on every one of %s)'
                 % (name, values[name], json.loads(expected),
                    ' in any order' if unordered else '',
                    ', '.join(sorted(computed))))
PYEOF
)" || fail "${out}"
}

ALL_MESH='{"db1":"10.0.0.11","db2":"10.0.0.12","hv1":"10.0.0.21","hv2":"10.0.0.22","hv3":"10.0.0.23","net1":"10.0.0.31"}'
GATEWAYS='["10.0.0.11","10.0.0.12"]'

CURRENT_CASE='case 1: every node answered'
run_case 'db1,db2,hv1,hv2,hv3,net1' 'hv1=9000,hv2=1500,hv3=9000'
assert_facts \
    'database_tier_hosts=["db1","db2"]' \
    'reachable_hypervisors~=["hv1","hv2","hv3"]' \
    'network_node_ip="10.0.0.31"' \
    "mariadb_gateway_hosts=${GATEWAYS}" \
    "all_mesh_hosts=${ALL_MESH}" \
    'max_hypervisor_mtu=1500'

CURRENT_CASE='case 2: a hypervisor and a database node did not answer'
# The 2026-07-20 sfcbr deploy. hv3 has no node_mtu, because validate_mtus
# only sets it on hosts which answered, so a reduction over the whole
# hypervisors group dereferences a fact that is not there and fails on
# every host. The static reductions must still carry hv3 and db2: a node
# down for this run still belongs in the mesh and in the gateway list.
run_case 'db1,hv1,hv2,net1' 'hv1=9000,hv2=1500'
assert_facts \
    'database_tier_hosts=["db1","db2"]' \
    'reachable_hypervisors~=["hv1","hv2"]' \
    'network_node_ip="10.0.0.31"' \
    "mariadb_gateway_hosts=${GATEWAYS}" \
    "all_mesh_hosts=${ALL_MESH}" \
    'max_hypervisor_mtu=1500'

CURRENT_CASE='case 3: an absent hypervisor reports the lowest MTU'
# The same shape as case 2, except hv3 carries an MTU in inventory rather
# than nothing. A reduction over the whole hypervisors group succeeds here
# and answers 1280, so this is the case which separates "reduce over the
# hosts which answered" from "reduce over the group and tolerate a missing
# fact". The mesh MTU of a node that is not in this run must not shrink
# the MTU the running nodes agree on.
run_case 'db1,db2,hv1,hv2,net1' 'hv1=9000,hv2=1500,hv3=1280'
assert_facts \
    'reachable_hypervisors~=["hv1","hv2"]' \
    "all_mesh_hosts=${ALL_MESH}" \
    'max_hypervisor_mtu=1500'

CURRENT_CASE='case 4: one hypervisor, and it is the whole reduction'
# A single-hypervisor cloud is the shape CI's slim topologies deploy, and
# min() over one element is where an off-by-one in the intersection would
# show as an empty-sequence error rather than a wrong number.
run_case 'db1,db2,hv1,net1' 'hv1=9000'
assert_facts \
    'reachable_hypervisors~=["hv1"]' \
    'max_hypervisor_mtu=9000' \
    "mariadb_gateway_hosts=${GATEWAYS}"

echo "cross-node reductions: all ${CASES_RUN} cases passed"
