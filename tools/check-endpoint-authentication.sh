#!/bin/bash
# Guardrail: every REST endpoint method authenticates unless it is
# explicitly and deliberately marked public.
#
# Background: authentication used to be opt-in, with each of the 124
# resource methods carrying its own `@api_base.verify_token`. 120 did,
# and the four which did not were the correct four -- but the failure
# mode was wrong. Forgetting the decorator on a new endpoint left it
# silently reachable with no credential, and nothing would have caught
# it. Phase 3 of the auth federation plan moved authentication onto
# `api_base.Resource.method_decorators` so it applies by default, with
# `@api_base.public` as the only way out.
#
# This walker enforces two things the runtime cannot:
#
#   1. A resource class must inherit from `api_base.Resource`. One
#      subclassing `flask_restful.Resource` directly would miss
#      `method_decorators` entirely and be silently open.
#   2. `@api_base.public` must be the first (outermost) decorator on
#      its method. The marker is an attribute read off the bound method
#      at dispatch, and several decorators in base.py predate
#      functools.wraps and so do not propagate attributes -- a `@public`
#      buried under one of them would be invisible, and the endpoint
#      would authenticate when the author believed it would not. That
#      direction fails closed, but it is still a lie in the source.
#
# The complementary runtime assertion -- that the *set* of public
# endpoints is exactly the expected four -- lives in
# shakenfist/tests/external_api/test_auth_universal.py, because it needs
# the routing table. This script is the static half.
#
# An AST walk rather than a regex, for the same reason as
# check-from-db-by-ref-namespace.sh: decorator expressions span lines
# and nest parentheses. A self-test below asserts both the bad shapes
# are caught and the good shape is not, so the guardrail cannot rot.

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

HTTP_METHODS = {'get', 'post', 'put', 'delete', 'patch', 'head'}


def _decorator_name(node):
    """Dotted name of a decorator expression, or None."""
    if isinstance(node, ast.Call):
        node = node.func
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return '.'.join(reversed(parts))
    return None


def _is_resource(cls):
    """Does this class look like a REST resource of ours?"""
    for base in cls.bases:
        name = _decorator_name(base)
        if name in ('api_base.Resource', 'Resource',
                    'flask_restful.Resource'):
            return name
    return None


def find_problems(source, path):
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        yield f'{path}: could not parse: {e}'
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base = _is_resource(node)
        if base is None:
            continue

        # api_base.Resource is itself the one legitimate subclass of
        # flask_restful.Resource -- it is the base that adds the
        # authenticating method_decorators in the first place.
        defines_the_base = (node.name == 'Resource'
                            and str(path).endswith('external_api/base.py'))

        if base == 'flask_restful.Resource' and not defines_the_base:
            yield (f'{path}:{node.lineno}: {node.name} subclasses '
                   f'flask_restful.Resource directly; use '
                   f'api_base.Resource so it authenticates')

        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name not in HTTP_METHODS:
                continue
            names = [_decorator_name(d) for d in item.decorator_list]
            public = [n for n in names
                      if n in ('api_base.public', 'public')]
            if not public:
                continue
            if names[0] not in ('api_base.public', 'public'):
                yield (f'{path}:{item.lineno}: {node.name}.{item.name} has '
                       f'@public but it is not the outermost decorator '
                       f'(found {names[0]!r} above it)')


GOOD = '''
class Thing(api_base.Resource):
    @api_base.public
    @swag_from({})
    def get(self):
        pass

    def post(self):
        pass
'''

BAD_NOT_OUTERMOST = '''
class Thing(api_base.Resource):
    @swag_from({})
    @api_base.public
    def get(self):
        pass
'''

BAD_WRONG_BASE = '''
class Thing(flask_restful.Resource):
    def get(self):
        pass
'''

# api_base.Resource itself is exempt; anything else with that base is
# not. Both halves of that rule are self-tested.
BASE_DEFINITION = '''
class Resource(flask_restful.Resource):
    method_decorators = []
'''


def selftest():
    if list(find_problems(GOOD, '<selftest>')):
        raise SystemExit(
            'guardrail self-test failed: the good shape was flagged')
    for name, snippet in (('public not outermost', BAD_NOT_OUTERMOST),
                          ('wrong base class', BAD_WRONG_BASE)):
        if not list(find_problems(snippet, '<selftest>')):
            raise SystemExit(
                f'guardrail self-test failed: {name} was not detected')
    if list(find_problems(BASE_DEFINITION, 'x/external_api/base.py')):
        raise SystemExit(
            'guardrail self-test failed: api_base.Resource itself was '
            'flagged')
    if not list(find_problems(BASE_DEFINITION, 'x/external_api/blob.py')):
        raise SystemExit(
            'guardrail self-test failed: the base-class exemption is not '
            'restricted to base.py')


selftest()

root = pathlib.Path(sys.argv[1])
problems = []
for path in sorted(root.rglob('*.py')):
    problems.extend(find_problems(path.read_text(), path))

if problems:
    sys.stderr.write(
        'error: endpoint authentication guardrail failed.\n'
        '       Authentication is applied to every resource method by\n'
        '       api_base.Resource.method_decorators. @api_base.public is\n'
        '       the only opt out, must be the outermost decorator, and\n'
        '       every use of it is a security decision.\n\n')
    sys.stderr.write('\n'.join(problems) + '\n')
    sys.exit(1)
PY
