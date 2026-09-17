#!/bin/bash
# Copyright 2019 Michael Still and contributors
#
# mutate-nested-sweep.sh -- break each structured-parameter schema and
# each handler guard on purpose, and check that the right row of
# shakenfist/tests/external_api/test_nested_sweep.py fails with the
# right message.
#
# Reading a guard cannot distinguish "this holds" from "this cannot
# fail". A sweep row which passes for the wrong reason is worse than a
# missing row, because it reads as evidence. So every property that
# file claims to pin is reverted here and the failure is asserted,
# which is the adversarial pass the pr-re-review skill asks for -- and
# it lives in a script so the set grows as the schemas do, instead of
# being re-improvised by the next person who needs it.
#
# Each mutation names the row that must fail. A mutation which leaves
# the sweep green means either the schema is not doing what the row
# says, or the row cannot fail; both are defects and both are reported
# here as MUTATION SURVIVED.
#
# This is a developer tool, not a CI job: it edits the tree in place,
# and it takes about a minute. Run it after changing ARGTYPES'
# structured schemas, the object branch of validation._field(), the
# nested finding flattener, or either of the two instance handler
# guards phase 7 added.
#
# Usage:
#
#   bash tools/mutate-nested-sweep.sh
#
# Run it from the root of a clean-ish worktree. Uncommitted work is
# safe: the four files it touches -- the vocabulary, the compiler, the
# handler and the sweep itself -- are restored from a copy taken before
# the first mutation, never with `git checkout`, because a
# directory-wide checkout discards uncommitted work in that directory
# and is painful to notice afterwards.

set -uo pipefail

PYTHON=".tox/py3/bin/python"
BASE="shakenfist/external_api/base.py"
VALIDATION="shakenfist/external_api/validation.py"
HANDLER="shakenfist/external_api/instance.py"
SWEEP="shakenfist/tests/external_api/test_nested_sweep.py"
FILES=("$BASE" "$VALIDATION" "$HANDLER" "$SWEEP")

if [ ! -x "$PYTHON" ]; then
    echo "No $PYTHON. Run tox once to build the test environment." >&2
    exit 1
fi

BACKUP=$(mktemp -d /tmp/mutate-nested-sweep-XXXXXX)
restore() {
    for f in "${FILES[@]}"; do
        cp "${BACKUP}/$(basename "${f}")" "${f}"
    done
}
cleanup() {
    restore
    rm -rf "${BACKUP}"
}
trap cleanup EXIT

for f in "${FILES[@]}"; do
    cp "${f}" "${BACKUP}/$(basename "${f}")"
done

# mutate <file> <old> <new> -- an exact, unique string replacement.
# Refuses anything but exactly one match, so a mutation which silently
# stopped applying (because the code it targets was reworded) is a loud
# failure rather than a survivor.
mutate() {
    "${PYTHON}" - "$@" <<'PYEOF'
import sys

path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    body = f.read()
if body.count(old) != 1:
    sys.stderr.write(
        'mutation target appears %d times in %s, expected exactly once:\n%s\n'
        % (body.count(old), path, old))
    sys.exit(1)
with open(path, 'w') as f:
    f.write(body.replace(old, new))
PYEOF
}

# The unmutated tree must be green before anything is broken on
# purpose. Without this the script has the failure mode it was written
# to catch, one level up: if the sweep is already red -- a rotted
# fixture, a half-finished edit -- then every mutation below is
# "caught" by a failure this script did not cause, and it exits 0
# having proved nothing.
echo "=== baseline: the sweep must pass before anything is mutated"
if ! "${PYTHON}" -m stestr run --no-subunit-trace 'test_nested_sweep' \
        > "${BACKUP}/baseline.log" 2>&1; then
    echo >&2
    echo "The sweep fails with no mutation applied, so no verdict below" >&2
    echo "would mean anything. Fix the tree, then run this again." >&2
    echo >&2
    tail -30 "${BACKUP}/baseline.log" >&2
    exit 1
fi
echo "  green"

SURVIVORS=0
CHECKED=0

# check <name> <expected row substring>
# Runs the sweep against the mutated tree and requires the named row to
# be in the failure output.
check() {
    local name="$1"
    local expected="$2"
    local output
    local status

    CHECKED=$((CHECKED + 1))
    output=$("${PYTHON}" -m stestr run --no-subunit-trace \
        'test_nested_sweep' 2>&1)
    status=$?

    # The run has to have failed, not merely to have mentioned the row.
    # Grepping alone would sign off a mutation whose row id appeared in
    # a passing run's output for any other reason.
    if [ "${status}" -eq 0 ]; then
        SURVIVORS=$((SURVIVORS + 1))
        echo "  *** MUTATION SURVIVED: ${name}"
        echo "  *** the sweep passed with the mutation applied"
        restore
        return
    fi

    if echo "${output}" | grep -q -- "${expected}"; then
        echo "  caught by: ${expected}"
        echo "$(echo "${output}" | grep -m1 -- "${expected}" | sed 's/^ *//')"
    else
        SURVIVORS=$((SURVIVORS + 1))
        echo "  *** MUTATION SURVIVED: ${name}"
        echo "  *** expected a failure mentioning: ${expected}"
        echo "${output}" | tail -20
    fi
    restore
}

run() {
    echo
    echo "=== $1"
}

# ---------------------------------------------------------------------
# 1. A required key is no longer required.
# ---------------------------------------------------------------------
run "networkspec drops network_uuid from its required list (D44)"
mutate "${BASE}" \
    "    'required': ['network_uuid']," \
    "    'required': []," || exit 1
check "networkspec required list" "net.uuid.null"

# ---------------------------------------------------------------------
# 2. Unknown keys are accepted again.
# ---------------------------------------------------------------------
run "diskspec accepts additional properties (D41, issue #936)"
mutate "${BASE}" \
    "issue #936.
    'additionalProperties': False," \
    "issue #936.
    'additionalProperties': True," || exit 1
check "diskspec additionalProperties" "disk.unknown_key"

# ---------------------------------------------------------------------
# 3. An enum is widened past what the server backs.
# ---------------------------------------------------------------------
run "disk type enum is widened (D43, D50)"
mutate "${BASE}" \
    "'enum': ['disk', 'cdrom']," \
    "'enum': ['disk', 'cdrom', 'nonsense']," || exit 1
check "disk type enum" "disk.type.nonsense"

run "video vdi enum is widened (D43 -- the one enum which is the enforcement)"
mutate "${BASE}" \
    "'enum': ['vnc', 'spice', 'spiceconcurrent', 'spicedebug']," \
    "'enum': ['vnc', 'spice', 'spiceconcurrent', 'spicedebug', 'nonsense']," \
    || exit 1
check "video vdi enum" "video.vdi.nonsense"

# ---------------------------------------------------------------------
# 4. A bound is removed.
# ---------------------------------------------------------------------
run "disk size loses its minimum (D45 -- a negative size corrupts the ledger)"
mutate "${BASE}" \
    "            'minimum': 0,
" "" || exit 1
check "disk size minimum" "disk.size.negative"

# ---------------------------------------------------------------------
# 5. A pattern is removed.
# ---------------------------------------------------------------------
run "netdesc macaddress loses its pattern (PR #4183)"
mutate "${BASE}" \
    "            'pattern': util_network.MACADDR_PATTERN,
" "" || exit 1
check "macaddress pattern" "net.macaddress.malformed"

# ---------------------------------------------------------------------
# 6. The _schema sentinel collapse is reverted -- the regression step 4
#    found and fixed while wrapping a spec in fields.Nested.
# ---------------------------------------------------------------------
run "the _schema sentinel leaks into the message again (step 4's regression)"
mutate "${VALIDATION}" \
    "            elif key == SCHEMA_LEVEL_KEY and not isinstance(" \
    "            elif False and key == SCHEMA_LEVEL_KEY and not isinstance(" || exit 1
check "_schema sentinel collapse" "element_not_a_mapping"

# ---------------------------------------------------------------------
# 6b. The other half of the same discrimination, added in review: the
#     collapse fires on the key alone again, so a caller who sends a
#     key literally named _schema is told about a field they did not
#     send. The two halves need separate mutations because each is
#     caught by a different row.
# ---------------------------------------------------------------------
run "the _schema collapse stops looking at the element (review item 3)"
mutate "${VALIDATION}" \
    "            elif key == SCHEMA_LEVEL_KEY and not isinstance(
                    _element_value(container, path), Mapping):" \
    "            elif key == SCHEMA_LEVEL_KEY:" || exit 1
check "_schema literal key" "disk.schema_key"

# ---------------------------------------------------------------------
# 7. D44's mechanism: a required nested field goes back to accepting a
#    null, which is how finding F7 stayed reachable through a typed
#    schema.
# ---------------------------------------------------------------------
run "a required nested property accepts null again (D44)"
mutate "${VALIDATION}" \
    "'required': required, 'allow_none': not required}" \
    "'required': required, 'allow_none': True}" || exit 1
check "nested required null" "net.uuid.null"

# ---------------------------------------------------------------------
# 8. _ExactInteger goes back to marshmallow's truncating Integer.
# ---------------------------------------------------------------------
run "integer fields truncate again (D46, finding F8)"
mutate "${VALIDATION}" \
    "    'integer': _ExactInteger," \
    "    'integer': fields.Integer," || exit 1
check "_ExactInteger" "disk.size.fractional"

# ---------------------------------------------------------------------
# 9. The netdesc guard goes back to testing presence rather than value.
#    This one must be caught at warn and at off, not at enforce: the
#    schema still refuses the null there, which is exactly why the
#    guard is needed and exactly why a rollback class exists.
# ---------------------------------------------------------------------
run "the netdesc guard tests presence again (issue #4223, the warn rollback)"
mutate "${HANDLER}" \
    "    if netdesc.get('network_uuid') is None:" \
    "    if 'network_uuid' not in netdesc:" || exit 1
check "netdesc null guard" "hotplug.uuid.null"

# ---------------------------------------------------------------------
# 10. The size-or-base guard is removed.
# ---------------------------------------------------------------------
run "a diskspec may ask for nothing again (D45)"
mutate "${HANDLER}" \
    "            if (_diskspec_value_absent(d, 'size')" \
    "            if (False and _diskspec_value_absent(d, 'size')" || exit 1
check "size or base guard" "disk.empty"

# ---------------------------------------------------------------------
# 11. The videospec guards test presence again, which is what they did
#     until the review of #4232 -- so an explicit null passes them, is
#     stored on the instance, and is rendered into the domain XML as
#     type='None'. Caught at every mode, since it is a handler guard.
# ---------------------------------------------------------------------
run "the videospec guard tests presence again (review item 1)"
mutate "${HANDLER}" \
    "            if video.get('model') is None:" \
    "            if 'model' not in video:" || exit 1
check "videospec null guard" "video.model.null"

# ---------------------------------------------------------------------
# 12. `float` is read with a bare truthiness test again, so the string
#     "false" floats the interface the schema just called false. The
#     status code cannot see this, which is why the row that catches it
#     is a test of its own rather than a line of the table.
# ---------------------------------------------------------------------
run "float is read truthily again (review item 2)"
mutate "${HANDLER}" \
    "        if validation.declared_boolean(netdesc.get('float')):" \
    "        if netdesc.get('float'):" || exit 1
check "declared_boolean" "test_a_falsy_float_spelling_does_not_float"

# ---------------------------------------------------------------------
# 13. _diskspec_value_absent stops reading util_general.noneish, so a
#     base of the literal string "none" -- which usage.md documents as
#     meaning no base -- reads as a base and the size-or-base guard
#     lets the spec through.
# ---------------------------------------------------------------------
run "a base of \"none\" counts as a base again (D45)"
mutate "${HANDLER}" \
    "    return util_general.noneish(value)" \
    "    return value is None" || exit 1
check "noneish base" "disk.base_none_only"

# ---------------------------------------------------------------------
# 14. The two derived coverage tests, which are the ones most at risk of
#     being vacuous: they are computed from the table rather than from a
#     request, so nothing else in this script would notice if they had
#     stopped meaning anything. Renaming one refused row's key invents a
#     netdesc key with no accepted row and no hotplug counterpart, which
#     both of them must refuse to sign off.
# ---------------------------------------------------------------------
run "a swept key with no accepted row and only one route (coverage tests)"
mutate "${SWEEP}" \
    "    Case('net.model.int', CREATE, 'networkspec', 'model'," \
    "    Case('net.model.int', CREATE, 'networkspec', 'wombat'," || exit 1
check "accepted-value coverage" "only ever refused in the table"

echo
echo "====================================================================="
echo "${CHECKED} mutations applied, ${SURVIVORS} survived."
if [ "${SURVIVORS}" -ne 0 ]; then
    echo "A surviving mutation means the sweep is not pinning what it says"
    echo "it pins. Fix the row, not the mutation."
    exit 1
fi
echo "Every mutation was caught by the row that claims to pin it."
