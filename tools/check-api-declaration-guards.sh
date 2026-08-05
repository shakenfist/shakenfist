#!/bin/bash
# Copyright 2019 Michael Still and contributors
#
# Mutation-test the API declaration guards: break each property the
# audit claims to enforce, confirm it fires, restore. A guard that
# passes on a deliberately broken tree is not a guard, and four of the
# five review rounds on PR #3620 found exactly that -- an assertion
# which held for a reason other than the one it was written for.
#
# Run from the repository root, with a built .tox/py3:
#
#     tools/check-api-declaration-guards.sh
#
# Exits non-zero unless every mutation was caught. Each one reports one
# of five verdicts, and only the first two are a pass:
#
#   caught          a named test failed
#   caught-import   swagger_helper() rejected it before the tests ran
#   NOT CAUGHT      the suite passed on a tree known to be broken
#   NO-OP           the mutation did not change the tree, so it proved
#                   nothing -- a pattern moved out from under a sed
#   HARNESS BROKEN  no test counts at all, so the run says nothing
#
# The last two exist because this script is subject to the failure it
# hunts. An earlier version inferred "caught" from the *absence* of
# "Failed: 0", so a mutation whose sed had silently stopped matching,
# or a run that died before reaching the tests, reported a catch it had
# never observed -- and one of the ten was in that state when it
# landed.
#
# Not wired into CI: it takes a couple of minutes and mutates the tree,
# so it belongs in the pre-push checklist rather than in a hook. It
# restores from a copy rather than with git, so uncommitted work
# survives a failure part-way through.
set -u

# Run from the repository root whatever the caller's directory. Every
# path below is relative and this script edits real source files, so
# starting anywhere else means the backup copies nothing, every sed
# fails, and there is no tree left to restore.
root=$(git rev-parse --show-toplevel) || exit 2
cd "${root}" || exit 2

PYTHON=.tox/py3/bin/python
if [ ! -x "${PYTHON}" ]; then
    echo "No ${PYTHON}: build it with 'tox -e py3 --notest' first." >&2
    exit 2
fi

# tox sets this in its own environments but this script calls the venv
# python directly, and a test run that writes __pycache__ into
# external_api/ after the backup was taken would make every subsequent
# NO-OP comparison report a difference that is not a mutation.
export PYTHONDONTWRITEBYTECODE=1

BACKUP=$(mktemp -d)
cp -a shakenfist/external_api/. "${BACKUP}"/
if [ -z "$(ls -A "${BACKUP}")" ]; then
    echo 'The backup of shakenfist/external_api is empty, so a mutation' >&2
    echo 'could not be undone. Refusing to mutate the tree.' >&2
    rm -rf "${BACKUP}"
    exit 2
fi
trap 'cp -a "${BACKUP}"/. shakenfist/external_api/; rm -rf "${BACKUP}"' EXIT

total=0
failures=0

run() { "${PYTHON}" -m stestr run test_parameter_declarations 2>&1; }

restore() { cp -a "${BACKUP}"/. shakenfist/external_api/; }

# Anything but a catch counts against the exit status, including the
# two verdicts which indict this script rather than a guard.
report() {  # verdict, name, detail
    total=$((total + 1))
    printf '%-15s %-46s %s\n' "$1" "$2" "$3"
    case "$1" in
        caught|caught-import) ;;
        *) failures=$((failures + 1)) ;;
    esac
}

check() {  # name
    local name="$1"
    local output counts

    # The mutation has to have landed before its result means anything.
    # Compared against the backup rather than against git, so unrelated
    # uncommitted work does not read as a mutation. Bytecode is
    # excluded as well as suppressed above: a developer tree may hold
    # __pycache__ from before the backup, and a stale .pyc recompiled
    # by the baseline run differs without any mutation having landed.
    if diff -rq -x '__pycache__' -x '*.pyc' "${BACKUP}" \
            shakenfist/external_api/ >/dev/null 2>&1; then
        report 'NO-OP' "${name}" 'the mutation did not change the tree'
        restore
        return
    fi

    output=$(run)
    counts=$(echo "${output}" | grep -E '^ - (Passed|Failed):' | tr '\n' ' ')

    if echo "${output}" | grep -q 'InvalidAPIDeclaration'; then
        report 'caught-import' "${name}" 'rejected by swagger_helper()'
    elif echo "${counts}" | grep -qE 'Failed: [1-9]'; then
        report 'caught' "${name}" "${counts}"
    elif echo "${counts}" | grep -q 'Failed: 0'; then
        report 'NOT CAUGHT' "${name}" "${counts}"
    else
        report 'HARNESS BROKEN' "${name}" 'the run produced no test counts'
    fi
    restore
}

echo '=== baseline ==='
baseline=$(run)
if ! echo "${baseline}" | grep -qE '^ - Failed: 0'; then
    echo 'The clean tree does not pass, so no mutation result below would' >&2
    echo 'mean anything. Fix the tree first.' >&2
    echo "${baseline}" | tail -20 >&2
    exit 2
fi
echo 'clean tree passes'
echo
echo '=== mutations ==='

# 1. A path parameter declared as something else.
sed -i "s/('blob_uuid', 'path', 'uuid'/('blob_uuid', 'query', 'uuid'/" \
    shakenfist/external_api/blob.py
check 'path parameter declared query'

# 2. A body parameter declared as query (the R3 hole).
sed -i "s/('event_type', 'body'/('event_type', 'query'/" \
    shakenfist/external_api/blob.py
check 'body parameter declared query'

# 3. An emptied parameter list, swag_from left in place (the R5 hole).
python3 - <<'PY'
p = 'shakenfist/external_api/blob.py'
s = open(p).read()
s = s.replace("""        [
            ('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True),
            ('offset', 'query', 'integer',
             'The offset into the file to start reading from.', False),
            ('limit', 'query', 'integer',
             ('The maximum amount of data to return in one response. '
              '0 means no limit.'), False)
        ],""", '        [],', 1)
open(p, 'w').write(s)
PY
check 'emptied parameter list, swag_from present'

# 4. A handler with no declaration at all.
python3 - <<'PY'
p = 'shakenfist/external_api/snapshot.py'
s = open(p).read()
s = s.replace("""    @swag_from(api_base.swagger_helper(
        'instances', 'List the snapshots of an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(200, 'Information about the snapshots of an instance.', None),
         (404, 'Instance not found.', None)]))
""", '', 1)
open(p, 'w').write(s)
PY
check 'handler with no swag_from'

# 5. An accepted kwarg that is not declared.
sed -i "s/def get(self, instance_ref=None, instance_from_db=None):/def get(self, instance_ref=None, instance_from_db=None, sneaky=None):/" \
    shakenfist/external_api/snapshot.py
check 'undeclared handler kwarg'

# 6. A path parameter declared optional.
sed -i "s/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', False)/" \
    shakenfist/external_api/blob.py
check 'optional path parameter'

# 7. An unknown type token.
sed -i "s/('blob_uuid', 'path', 'uuid'/('blob_uuid', 'path', 'uuidd'/" \
    shakenfist/external_api/blob.py
check 'unknown type token'

# 8. A wrong-arity declaration tuple.
sed -i "s/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.')/" \
    shakenfist/external_api/blob.py
check 'four-element declaration tuple'

# 9. A route the derivation cannot read. The name has to be defined,
# or the mutation is a NameError at import and the resulting failure
# says nothing about whether the audit noticed the unreadable route.
sed -i "s@api.add_resource(api_blob.BlobEndpoint, '/blobs/<blob_uuid>')@BLOB_ROUTES = ('/blobs/<blob_uuid>',)\napi.add_resource(api_blob.BlobEndpoint, *BLOB_ROUTES)@" \
    shakenfist/external_api/app.py
check 'non-literal route argument'

# 10. An injected object declared as a parameter.
sed -i "s/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)/('blob_from_db', 'path', 'uuid', 'The UUID of the blob.', True)/" \
    shakenfist/external_api/blob.py
check 'decorator-injected object declared'

# There is no mutation here for two endpoint classes sharing a name,
# which a route lookup keyed on the bare name cannot tell apart. It
# cannot be expressed in this tree: flask_restful derives its endpoint
# name from the class name and refuses the second registration with
# "ValueError: This endpoint (nodeendpoint) is already set to the class
# NodeEndpoint", so any mutation that creates the collision breaks
# app.py at import and the run reports that instead of the audit's
# verdict. The audit reports it anyway, one step earlier and by name,
# and DerivationTestCase.test_colliding_class_names_are_reported covers
# that on constructed sources.

echo
if [ "${failures}" -ne 0 ]; then
    echo "${failures} of ${total} mutations were not caught."
    exit 1
fi
echo "All ${total} mutations caught."
