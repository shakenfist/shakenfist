#!/bin/bash
# Guardrail: callers of `from_db_by_ref` inside `shakenfist/external_api/`
# must pass a namespace that was first resolved by
# `api_base.resolve_lookup_namespace(...)` rather than handing
# `request_namespace()` straight through.
#
# Background: the namespace='system' sentinel inside `from_db_by_ref`
# means "search every namespace". A REST endpoint that passes
# `request_namespace()` (which is 'system' for admin tokens) without
# first consulting the request body's `namespace` field can return an
# object from a namespace the caller never asked for. That class of
# bug bit us once already (see release_notes/) and the decorator-level
# fix lives in `arg_is_*_ref`. This grep catches future regressions.
#
# The check is narrow: only `shakenfist/external_api/` is inspected,
# and only the literal `from_db_by_ref(..., request_namespace())`
# pattern is flagged. Multi-line calls are supported via `pcre2grep`
# when available, otherwise we fall back to a Python one-liner.

set -eu

ROOT=$(git rev-parse --show-toplevel)
TARGET="${ROOT}/shakenfist/external_api"

if [ ! -d "${TARGET}" ]; then
    echo "skip: ${TARGET} does not exist" >&2
    exit 0
fi

python3 - "${TARGET}" <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])

# Pattern: `*.from_db_by_ref(<any whitespace, any args>, request_namespace())`
# where `request_namespace()` is the *second* argument and is therefore the
# raw caller namespace rather than one resolved against the request body.
pattern = re.compile(
    r'\.from_db_by_ref\s*\([^)]*?,\s*request_namespace\(\)\s*\)',
    re.DOTALL,
)

hits = []
for path in sorted(root.rglob('*.py')):
    text = path.read_text()
    for m in pattern.finditer(text):
        line_no = text.count('\n', 0, m.start()) + 1
        hits.append(f'{path}:{line_no}: {m.group(0).strip()}')

if hits:
    sys.stderr.write(
        'error: from_db_by_ref called with request_namespace() directly.\n'
        '       Use api_base.resolve_lookup_namespace(...) first so the\n'
        '       request body\'s namespace field is honoured for system\n'
        '       callers. See arg_is_instance_ref in external_api/base.py\n'
        '       for the canonical pattern.\n\n')
    sys.stderr.write('\n'.join(hits) + '\n')
    sys.exit(1)
PY
