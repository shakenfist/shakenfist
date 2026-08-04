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
# Every line should read "caught". A "NOT CAUGHT" means the guard has a
# hole; an empty result means the mutation aborts test collection,
# which is how swagger_helper()'s import-time rejections surface.
#
# Not wired into CI: it takes a couple of minutes and mutates the tree,
# so it belongs in the pre-push checklist rather than in a hook. It
# restores from a copy rather than with git, so uncommitted work
# survives a failure part-way through.
set -u

BACKUP=$(mktemp -d)
cp -a shakenfist/external_api/. "$BACKUP"/
trap 'cp -a "$BACKUP"/. shakenfist/external_api/; rm -rf "$BACKUP"' EXIT

run() { .tox/py3/bin/python -m stestr run test_parameter_declarations 2>&1 \
        | grep -E "^ - (Passed|Failed):" | tr '\n' ' '; }

check() {  # name, expectation
    printf '%-52s ' "$1"
    result=$(run)
    if echo "$result" | grep -q 'Failed: 0'; then
        echo "NOT CAUGHT   ($result)"
    else
        echo "caught       ($result)"
    fi
    cp -a "$BACKUP"/. shakenfist/external_api/
}

echo "=== baseline (expect Failed: 0) ==="
printf '%-52s %s\n' "clean tree" "$(run)"
echo
echo "=== mutations (expect all caught) ==="

# 1. A path parameter declared as something else.
sed -i "s/('blob_uuid', 'path', 'uuid'/('blob_uuid', 'query', 'uuid'/" \
    shakenfist/external_api/blob.py
check "path parameter declared query"

# 2. A body parameter declared as query (the R3 hole).
sed -i "271s/'event_type', 'body'/'event_type', 'query'/" \
    shakenfist/external_api/blob.py
check "body parameter declared query"

# 3. An emptied parameter list, swag_from left in place (the R5 hole).
python3 - <<'PY'
import re
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
check "emptied parameter list, swag_from present"

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
check "handler with no swag_from"

# 5. An accepted kwarg that is not declared.
sed -i "s/def get(self, instance_ref=None, instance_from_db=None):/def get(self, instance_ref=None, instance_from_db=None, sneaky=None):/" \
    shakenfist/external_api/snapshot.py
check "undeclared handler kwarg"

# 6. A path parameter declared optional.
sed -i "s/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', False)/" \
    shakenfist/external_api/blob.py
check "optional path parameter"

# 7. An unknown type token.
sed -i "s/('blob_uuid', 'path', 'uuid'/('blob_uuid', 'path', 'uuidd'/" \
    shakenfist/external_api/blob.py
check "unknown type token"

# 8. A wrong-arity declaration tuple.
sed -i "s/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.')/" \
    shakenfist/external_api/blob.py
check "four-element declaration tuple"

# 9. A route the derivation cannot read.
sed -i "s@api.add_resource(api_blob.BlobEndpoint, '/blobs/<blob_uuid>')@api.add_resource(api_blob.BlobEndpoint, *BLOB_ROUTES)@" \
    shakenfist/external_api/app.py
grep -q 'BLOB_ROUTES' shakenfist/external_api/app.py || echo '  (mutation did not apply!)'
check "non-literal route argument"

# 10. An injected object declared as a parameter.
sed -i "s/('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)/('blob_from_db', 'path', 'uuid', 'The UUID of the blob.', True)/" \
    shakenfist/external_api/blob.py
check "decorator-injected object declared"

echo
git status --short shakenfist/external_api/ | head
