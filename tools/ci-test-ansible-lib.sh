# Copyright 2026 Michael Still and contributors
#
# ci-test-ansible-lib.sh -- the scaffolding every rootless ansible test in
# tools/ needs before it can run a play: a failure reporter, a PATH check,
# a working directory, and an ansible environment isolated from whatever
# the developer running it has installed.
#
# Sourced, never executed. It defines things and changes the environment;
# running it does nothing.
#
# It exists because four scripts had the same forty lines. The kerbside
# deployer push audit found three of them (issue #4708) and the fourth
# would have been a new copy: tools/ci-test-cross-node-reductions.sh was
# written against this instead (issue #3311). Kerbside carries a fifth,
# in its own repository, which this cannot reach.
#
# Everything here is deliberately not a test framework. Each script's own
# run_play / assert_* helpers differ because each drives a different thing
# -- certificates, a stub sf-ctl, site.yml's play selection, computed
# facts -- and folding those together would make one abstraction that fits
# none of them. What is shared is only the sandbox.
#
#     source "$(dirname "${BASH_SOURCE[0]}")/ci-test-ansible-lib.sh"
#
# and then, in order:
#
#     need ansible-playbook 'install ansible-core'
#     sf_test_workdir ci-test-something
#     sf_test_link_collection "${WORK}/collections"
#     sf_test_ansible_env "${WORK}/collections"

# Sourcing twice would reset CURRENT_CASE and LAST_LOG in the middle of a
# run, which would make a later failure report the wrong case.
if [ -n "${SF_TEST_LIB_SOURCED:-}" ]; then
    return 0
fi
SF_TEST_LIB_SOURCED=1

# The repository, from this file's location rather than from the caller's,
# so a script which is invoked through a symlink or from another directory
# still finds the collection it is supposed to test.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COLLECTION_DIR="${REPO_ROOT}/shakenfist/deploy/collection"

# The case a failure will be attributed to, and the log it will quote.
# Callers set both as they go; a failure during setup says so.
CURRENT_CASE='setup'
LAST_LOG=''

# A hook called by fail(), for a script which has more to say than the
# log -- ci-test-kerbside-cluster-config.sh dumps its stub sf-ctl's log.
# Named rather than defined, so a script which sets it before the function
# exists still works.
FAIL_EXTRA=''

fail() {
    echo "FAIL [${CURRENT_CASE}]: $*" >&2
    if [ -n "${LAST_LOG}" ] && [ -f "${LAST_LOG}" ]; then
        echo "---- last 40 lines of ${LAST_LOG##*/} ----" >&2
        tail -n 40 "${LAST_LOG}" >&2
    fi
    # Only when it is a function that exists. A failure during setup --
    # a missing tool, for instance -- happens before the script has
    # defined its own helpers, and a "command not found" there would
    # replace the message explaining what actually went wrong.
    if [ -n "${FAIL_EXTRA}" ] && declare -F "${FAIL_EXTRA}" > /dev/null; then
        "${FAIL_EXTRA}" >&2
    fi
    exit 1
}

need() {
    command -v "$1" > /dev/null || fail "$1 not found on PATH: $2"
}

# A private working directory, removed on exit unless KEEP_WORK is set.
# Sets WORK, and makes the two subdirectories every caller wants.
#
# KEEP_WORK used to be honoured by ci-test-kerbside-wiring.sh alone. It is
# honoured for all of them now: a test which deletes the evidence on the
# way out is the hardest kind to debug, and nothing reads the directory
# after the script exits unless someone asked for it.
sf_test_workdir() {
    local name="$1"
    WORK="$(mktemp -d "${TMPDIR:-/tmp}/${name}.XXXXXX")"
    # shellcheck disable=SC2064  # WORK is expanded now, deliberately: the
    # trap must name this directory even if a later call reassigns WORK.
    trap "[ -n \"\${KEEP_WORK:-}\" ] || rm -rf \"${WORK}\"" EXIT
    mkdir -p "${WORK}/logs" "${WORK}/ansible-tmp"
}

# The collection under test, reachable as shakenfist.shakenfist.
#
# A symlink rather than a copy, so what runs is the working tree: an
# edit is tested without a build step, which is the whole point of these
# scripts being runnable by hand.
#
# Where this is pointed matters more than it looks. Ansible searches a
# "collections" directory beside the playbook, so linking into
# "${WORK}/collections" with the play at "${WORK}/play.yml" makes the
# collection reachable whether or not ANSIBLE_COLLECTIONS_PATH names it.
# That was measured, not assumed: pointing the variable at an empty
# directory leaves ci-test-internal-ca.sh passing. Both are kept
# deliberately -- the variable for a script whose play is written
# somewhere else, the adjacency for one which sets the variable per run,
# as ci-test-kerbside-wiring.sh does -- but it does mean breaking either
# one alone goes unnoticed.
sf_test_link_collection() {
    local root="$1"
    mkdir -p "${root}/ansible_collections/shakenfist"
    ln -s "${COLLECTION_DIR}" \
        "${root}/ansible_collections/shakenfist/shakenfist"
}

# An ansible environment which ignores everything outside WORK.
#
# Without this a developer's ~/.ansible.cfg, or an installed copy of the
# collection under ~/.ansible/collections, decides what runs -- and the
# installed copy is the likely one, because the dev-install instructions
# symlink it there. The test would then pass or fail against code the
# checkout does not contain.
#
# The collections path is optional because ci-test-kerbside-wiring.sh
# sets it per run: half its cases are about what happens when
# shakenfist.kerbside is *not* installed.
sf_test_ansible_env() {
    local collections_path="${1:-}"

    : > "${WORK}/ansible.cfg"
    export ANSIBLE_CONFIG="${WORK}/ansible.cfg"
    export ANSIBLE_HOME="${WORK}/ansible-home"
    export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=false
    export ANSIBLE_LOCAL_TEMP="${WORK}/ansible-tmp"
    export ANSIBLE_REMOTE_TMP="${WORK}/ansible-tmp"
    export ANSIBLE_NOCOLOR=1
    export ANSIBLE_STDOUT_CALLBACK=default

    if [ -n "${collections_path}" ]; then
        export ANSIBLE_COLLECTIONS_PATH="${collections_path}"
    fi
}
