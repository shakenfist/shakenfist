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
# fix lives in `arg_is_*_ref`. This walker catches future regressions.
#
# The check is narrow: only `shakenfist/external_api/` is inspected,
# and only call sites whose second positional argument is the literal
# expression `request_namespace()` are flagged. We use an AST walk
# rather than a regex because the realistic bug shape is
# `from_db_by_ref(kwargs.get('foo'), request_namespace())` and the
# nested `)` defeated a previous regex (see PR #3247 review item 1).
# A self-test below asserts that the historical shape is detected so
# the guardrail can't silently rot.

set -eu

ROOT=$(git rev-parse --show-toplevel)
TARGET="${ROOT}/shakenfist/external_api"

if [ ! -d "${TARGET}" ]; then
    echo "skip: ${TARGET} does not exist" >&2
    exit 0
fi

python3 - "${TARGET}" <<'PY'
import ast
import pathlib
import sys


def call_is_from_db_by_ref(node):
    """Match `<anything>.from_db_by_ref(...)` calls."""
    func = node.func
    return (isinstance(func, ast.Attribute)
            and func.attr == 'from_db_by_ref')


def second_arg_is_request_namespace(node):
    """True if the second positional argument is `request_namespace()`."""
    if len(node.args) < 2:
        return False
    arg = node.args[1]
    if not isinstance(arg, ast.Call):
        return False
    func = arg.func
    if isinstance(func, ast.Name):
        return func.id == 'request_namespace'
    if isinstance(func, ast.Attribute):
        return func.attr == 'request_namespace'
    return False


def find_bad_calls(source, path):
    """Yield `path:line` for each offending call site in `source`."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        # Treat unparseable files as a configuration error so the
        # guardrail does not silently pass on broken syntax.
        raise SystemExit(f'error: cannot parse {path}: {exc}')
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not call_is_from_db_by_ref(node):
            continue
        if second_arg_is_request_namespace(node):
            yield f'{path}:{node.lineno}'


def selftest():
    """Assert the walker still catches the historical bug shape.

    These snippets are the shapes that existed before PR #3247 and
    that the v1 regex-based guardrail silently missed. If a future
    refactor of this script makes any of them stop matching, the
    self-test fails and the hook errors out before scanning the tree.
    """
    must_match = [
        # Shape that actually appeared in base.py.
        ("inst = Instance.from_db_by_ref(\n"
         "    kwargs.get('instance_ref'), request_namespace())"),
        # Single-line variant.
        "n = Network.from_db_by_ref('foo', request_namespace())",
        # Attribute access on request_namespace.
        ("a = Artifact.from_db_by_ref(\n"
         "    netdesc['x'], something.request_namespace())"),
    ]
    must_not_match = [
        # Resolved namespace (the fixed shape).
        ("inst = Instance.from_db_by_ref(\n"
         "    kwargs.get('instance_ref'), lookup_namespace)"),
        # Different function name entirely.
        "obj.from_db('foo', request_namespace())",
    ]
    for snippet in must_match:
        if not list(find_bad_calls(snippet, '<selftest>')):
            raise SystemExit(
                'guardrail self-test failed: pattern not detected:\n'
                f'{snippet}')
    for snippet in must_not_match:
        if list(find_bad_calls(snippet, '<selftest>')):
            raise SystemExit(
                'guardrail self-test failed: pattern wrongly detected:\n'
                f'{snippet}')


selftest()

root = pathlib.Path(sys.argv[1])
hits = []
for path in sorted(root.rglob('*.py')):
    hits.extend(find_bad_calls(path.read_text(), path))

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
