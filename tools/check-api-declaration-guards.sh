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
#
# Requires GNU sed (the mutations use `sed -i` without a backup
# suffix, which BSD/macOS sed parses differently) and rsync (the
# restore path deletes files a mutation created).
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

# tox also sets LC_ALL for its environments, and this script does not
# get that either. The API sources contain non-ASCII, so under an
# ASCII locale every file read in the audit used to die with
# UnicodeDecodeError. The reads now pass encoding='utf-8' explicitly,
# but the interpreter itself still consults the locale for stdio.
export PYTHONUTF8=1

# Guarded like every other failure path here: an unchecked mktemp
# failure would leave BACKUP empty, and `cp -a shakenfist/external_api/.
# ""/` resolves to copying the API sources into the filesystem root.
BACKUP=$(mktemp -d) || exit 2
[ -n "${BACKUP}" ] || exit 2
cp -a shakenfist/external_api/. "${BACKUP}"/
if [ -z "$(ls -A "${BACKUP}")" ]; then
    echo 'The backup of shakenfist/external_api is empty, so a mutation' >&2
    echo 'could not be undone. Refusing to mutate the tree.' >&2
    rm -rf "${BACKUP}"
    exit 2
fi
# rsync --delete rather than cp: a copy restores modified files but
# leaves behind any file a mutation *created*, in a directory the
# audit globs. No current mutation adds a file, but this script exists
# to be extended.
trap 'rsync -a --delete "${BACKUP}"/ shakenfist/external_api/; rm -rf "${BACKUP}"' EXIT

total=0
failures=0

# Both modules, because the guard surface spans both: the import-time
# checks are asserted by test_parameter_declarations, but whether a
# declaration describes the shape the handler actually accepts can only
# be seen in the generated specification, which is
# test_openapi_spec's subject. A mutation caught by neither is a gap in
# the guards, not in the mutation.
run() {
    "${PYTHON}" -m stestr run \
        '(test_parameter_declarations|test_openapi_spec)' 2>&1
}

restore() { rsync -a --delete "${BACKUP}"/ shakenfist/external_api/; }

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

    # Counts first, and the exception-name grep only when there are no
    # counts at all. Assertion messages and tracebacks in a *test*
    # failure can also contain the string 'InvalidAPIDeclaration', so
    # consulting the grep first mislabels a named test failure as an
    # import-time rejection -- inferring the verdict from a substring
    # rather than observing it, which is the defect this script exists
    # to hunt. An import-time abort is recognised by what it prevents:
    # a test run.
    if echo "${counts}" | grep -qE 'Failed: [1-9]'; then
        report 'caught' "${name}" "${counts}"
    elif echo "${counts}" | grep -q 'Failed: 0'; then
        report 'NOT CAUGHT' "${name}" "${counts}"
    elif echo "${output}" | grep -q 'InvalidAPIDeclaration'; then
        report 'caught-import' "${name}" 'rejected before the tests could run'
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
            ('offset', 'query', 'unsignedinteger',
             'The offset into the file to start reading from.', False),
            ('limit', 'query', 'unsignedinteger',
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

# 11. A parameter declared at an underivable location. `header` is a
# valid OpenAPI 2.0 location so swagger_helper() accepts it, and until
# the UNDERIVABLE_BY_DESIGN canary this was a silent opt-out from the
# whole audit: the fixer printed it and exited zero, and no test read
# the underivable list.
sed -i "s/('blob_uuid', 'path', 'uuid'/('blob_uuid', 'header', 'uuid'/" \
    shakenfist/external_api/blob.py
check 'underivable location declared'

# 12. Two endpoint classes sharing a name, which a route lookup keyed
# on the bare name cannot tell apart. An earlier revision claimed this
# was unreachable in a running sf-api because flask_restful derives
# its endpoint name from the class name and refuses the second
# registration -- but that only holds for the default name, and app.py
# itself passes endpoint='healthz' to mount Readyz twice, so an
# explicit endpoint= registers a same-named class from another module
# just fine. The audit's by-name report is a genuine defence, not a
# redundant early warning. A *registered* collision still breaks
# import, so the mutation defines the duplicate without registering
# it; the class is otherwise fully compliant (declared, empty
# parameter list, no kwargs) so the collision report is the only
# guard it can trip. Note the endpoint/route symmetry test cannot see
# it: that check compares class *names*, and this name is mounted --
# which is exactly the blindness the by-name collision report exists
# to cover. DerivationTestCase.test_colliding_class_names_are_reported
# covers the message shape on constructed sources.
python3 - <<'PY'
p = 'shakenfist/external_api/blob.py'
s = open(p).read()
s += '''

class NodeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'nodes', 'A deliberate name collision with node.py.', [],
        [(200, 'Nothing.', None)]))
    def get(self):
        return None
'''
open(p, 'w').write(s)
PY
check 'two endpoint classes sharing a name'

# 13. An unknown constraint key. The blob events limit carries the
# tree's canonical constraints element, so the constraint mutations
# all target it or its neighbours in blob.py.
sed -i "s/{'minimum': 1, 'maximum': 1000})/{'minimom': 1, 'maximum': 1000})/" \
    shakenfist/external_api/blob.py
check 'unknown constraint key'

# 14. A constraints element which is not a dictionary.
sed -i "s/, {'minimum': 1, 'maximum': 1000})/, 'soon')/" \
    shakenfist/external_api/blob.py
check 'non-dict constraints element'

# 15. A pattern which does not compile. uuid renders as a string
# type, so this passes the pattern-on-a-string check and must be
# caught by the compile check specifically.
sed -i "s/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True, {'pattern': '('})/" \
    shakenfist/external_api/blob.py
check 'uncompilable pattern constraint'

# 16. A numeric bound on a non-numeric type.
sed -i "s/('event_type', 'body', 'string', 'The type of event to return.', False)/('event_type', 'body', 'string', 'The type of event to return.', False, {'minimum': 1})/" \
    shakenfist/external_api/blob.py
check 'minimum on a string type'

# 17. An array of objects outside the body. Every use in the tree is
# body-located, so this guard is a no-op today and exists for the next
# declaration; the mutation is how we know it is real. A query
# location is used rather than path because a path parameter would
# also trip the route-symmetry check, and a mutation which trips two
# guards does not prove the one it was written for.
sed -i "s/('disk', 'body', 'arrayofdict',/('disk', 'query', 'arrayofdict',/" \
    shakenfist/external_api/instance.py
check 'array of objects outside the body'

# 18. A bare object outside the body, which is the same defect without
# the array wrapper and is guarded by the other half of the same
# condition.
sed -i "s/('video', 'body', 'dict',/('video', 'query', 'dict',/" \
    shakenfist/external_api/instance.py
check 'object outside the body'

# 19. A fractional bound on an integer type. Renders as valid JSON
# Schema, so nothing downstream would notice; it is refused because it
# is typo-shaped, like minimum=True.
sed -i "s/{'minimum': 1, 'maximum': 1000})/{'minimum': 1.5, 'maximum': 1000})/" \
    shakenfist/external_api/blob.py
check 'fractional bound on an integer'

# 20. instance create metadata retyped back to an array. The handler
# answers 400 to anything but a dictionary, so this publishes -- and
# would later compile -- a shape which cannot work. Caught by
# test_openapi_spec.py rather than at import time: both tokens are
# legal in a body, so only the endpoint's own semantics distinguish
# them.
sed -i "s/('metadata', 'body', 'dict',/('metadata', 'body', 'arrayofdict',/" \
    shakenfist/external_api/instance.py
check 'instance metadata published as an array'

# 21. Console length retyped to unsignedinteger, which publishes
# minimum 0 while -1 is a supported sentinel meaning "the whole log"
# that the functional suite itself uses. Like mutation 20 this is a
# token both legal in a body, so only the endpoint's semantics
# distinguish them and only the specification pin can see it.
sed -i "s/('length', 'body', 'integer',/('length', 'body', 'unsignedinteger',/" \
    shakenfist/external_api/instance.py
check 'console length published as unsigned'

# 22. A pattern using a Python-only regex construct. Compiles fine, so
# only the dialect check refuses it.
sed -i "s/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True, {'pattern': '(?P<u>.*)'})/" \
    shakenfist/external_api/blob.py
check 'python only regex construct'

# 23. A bound on a parameter nobody registered. Legal at import time
# and legal OpenAPI, so the only thing which can see it is the derived
# completeness assertion -- which is the point of that assertion: the
# STRUCTURED_PARAMETERS table is written by hand and would otherwise
# fall behind the tree silently, which is the same failure mode as the
# prose types it replaced. The rule update endpoint is used because its
# key_name_prefix is the one declaration in the tree whose final line
# is unique enough to sed without also matching its sibling on create.
sed -i "s/'Prefix for minted key names.', True)/'Prefix for minted key names.', True, {'pattern': '^[a-z]+$'})/" \
    shakenfist/external_api/auth.py
check 'bound on an unregistered parameter'

# 24. A bound on a parameter which IS registered, and registered
# precisely to assert that nothing bounds it: -1 is a supported
# sentinel on console length. Distinct from mutation 21, which retypes
# the token; this leaves the token alone and adds a maximum, so it is
# caught only by an entry describing the published shape in full
# rather than just its minimum.
sed -i "s/'to fetch the entire console log.', False)/'to fetch the entire console log.', False, {'maximum': 65536})/" \
    shakenfist/external_api/instance.py
check 'spurious maximum on a registered parameter'

# 25. A variadic handler. log_request merges the whole JSON body into
# a handler's kwargs, so `**kwargs` accepts every body key a caller
# sends while the enumeration returns only the named arguments. The
# mutation declares nothing extra, so no drift or completeness check
# can see it: the only thing which can catch it is the signature being
# reported as unreadable.
sed -i "s/def get(self, instance_ref=None, instance_from_db=None):/def get(self, instance_ref=None, instance_from_db=None, **kwargs):/" \
    shakenfist/external_api/snapshot.py
check 'variadic handler signature'

echo
if [ "${failures}" -ne 0 ]; then
    echo "${failures} of ${total} mutations were not caught."
    exit 1
fi
echo "All ${total} mutations caught."
