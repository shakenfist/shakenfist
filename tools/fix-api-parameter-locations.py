# Copyright 2019 Michael Still and contributors
"""Derive the correct location for every declared API parameter.

Written as a one-shot migration for phase 1 of
docs/plans/PLAN-api-input-validation.md, and kept as the derivation itself:

    python3 tools/fix-api-parameter-locations.py            # report only
    python3 tools/fix-api-parameter-locations.py --apply    # rewrite

Run from the repository root. Report mode exits non-zero when the tree
disagrees with the derivation, and runs on every commit touching
external_api/ via the check-api-parameter-locations pre-commit hook, so a
declaration cannot drift from the code that reads it. The equivalent
assertions live in shakenfist/tests/external_api/test_parameter_declarations.py
for CI; keep the two in step.

Four sources decide where a parameter really comes from, in order:

* a name appearing in a route the class is mounted on is in the ``path``;
* a name in a ``use_kwargs(..., location='query')`` schema is in the ``query``;
* a name the handler reads from ``flask.request.args`` is in the ``query``,
  even if it can also arrive in the body -- the published documentation and
  the query-string fallback phase 3 compiles must agree;
* everything else is in the ``body``, because ``log_request`` merges the JSON
  body into handler kwargs.

``header`` and ``formData`` declarations cannot be derived from any of those,
so they are reported and left alone rather than rewritten to ``body``.

Edits are applied at exact AST positions and assert the literal they replace,
so nothing is matched by text.
"""
import ast
import collections
import glob
import re
import sys


API_GLOB = 'shakenfist/external_api/*.py'
APP = 'shakenfist/external_api/app.py'

# Locations which say where a value comes from in a way no rule here can
# check. Report them and move on: deriving one of these to 'body' would
# turn a correct declaration into a wrong one.
UNDERIVABLE_LOCATIONS = frozenset(['header', 'formData'])


def route_parameters():
    """Path parameter names per endpoint class, from the mounted routes.

    Werkzeug routes may name a converter, as in ``<path:label_name>`` or
    ``<int(min=1):x>``, so the parameter name is whatever follows the last
    colon.
    """
    out = collections.defaultdict(set)
    for node in ast.walk(ast.parse(open(APP).read())):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, 'attr', '') != 'add_resource':
            continue
        cls = ast.unparse(node.args[0]).split('.')[-1]
        for arg in node.args[1:]:
            try:
                route = ast.literal_eval(arg)
            except ValueError:
                continue
            out[cls] |= {segment.split(':')[-1]
                         for segment in re.findall(r'<([^>]+)>', route)}
    return out


def query_parameters(cls):
    """Names a class parses from the query string via webargs."""
    out = set()
    for node in ast.walk(cls):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, 'id', '') == 'get_args' for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key in node.value.keys:
            try:
                out.add(ast.literal_eval(key))
            except ValueError:
                pass
    return out


def request_args_parameters(fn):
    """Names a handler reads straight out of the query string.

    ``ClusterOperationsEndpoint.get`` accepts its target parameters as
    body keys (via the ``log_request`` merge) but falls back to
    ``flask.request.args.get()`` for each, so a raw ``?target_...=`` GET
    keeps working -- which is the form AGENTS.md documents. A parameter
    read this way is a query parameter whatever else it also is.
    """
    out = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get'
                and is_request_args(node.func.value)
                and node.args):
            key = node.args[0]
        elif isinstance(node, ast.Subscript) and is_request_args(node.value):
            key = node.slice
        else:
            continue
        try:
            out.add(ast.literal_eval(key))
        except ValueError:
            pass
    return out


def is_request_args(node):
    """Is this node ``request.args``, however ``request`` was imported?"""
    return (isinstance(node, ast.Attribute) and node.attr == 'args'
            and ast.unparse(node.value).split('.')[-1] == 'request')


def declarations(fn):
    """Yield (name, location AST node) for each declared parameter."""
    for dec in fn.decorator_list:
        if 'swagger_helper' not in ast.unparse(dec):
            continue
        call = dec.args[0] if isinstance(dec, ast.Call) and dec.args else None
        if not (isinstance(call, ast.Call) and len(call.args) >= 3):
            continue
        if not isinstance(call.args[2], ast.List):
            continue
        for item in call.args[2].elts:
            if not (isinstance(item, ast.Tuple) and len(item.elts) >= 2):
                continue
            try:
                yield ast.literal_eval(item.elts[0]), item.elts[1]
            except ValueError:
                # A constant reference such as api_base.RAW_BODY_PARAMETER,
                # which documents the raw request body rather than a named
                # parameter.
                continue


def main(apply_edits):
    routes = route_parameters()
    total = 0
    unhandled = []

    for path in sorted(glob.glob(API_GLOB)):
        lines = open(path).read().splitlines(keepends=True)
        edits = []

        for cls in [n for n in ast.walk(ast.parse(''.join(lines)))
                    if isinstance(n, ast.ClassDef)]:
            in_path = routes.get(cls.name, set())
            in_query = query_parameters(cls)

            for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                from_args = request_args_parameters(fn)

                for name, node in declarations(fn):
                    try:
                        have = ast.literal_eval(node)
                    except ValueError:
                        continue

                    if have in UNDERIVABLE_LOCATIONS:
                        unhandled.append((cls.name, name, have))
                        continue

                    if name in in_path:
                        want = 'path'
                    elif name in in_query or name in from_args:
                        want = 'query'
                    else:
                        want = 'body'
                    if have != want:
                        edits.append((node, cls.name, name, have, want))

        for node, cls, name, have, want in sorted(
                edits, key=lambda e: (e[0].lineno, e[0].col_offset),
                reverse=True):
            assert node.lineno == node.end_lineno, (
                'multi-line location literal for %s' % name)
            line = lines[node.lineno - 1]
            assert line[node.col_offset:node.end_col_offset] == repr(have), (
                '%s:%d does not hold %r' % (path, node.lineno, have))
            lines[node.lineno - 1] = (line[:node.col_offset] + repr(want)
                                      + line[node.end_col_offset:])
            print('  %-38s %-18s %-6s -> %s' % (cls, name, have, want))
            total += 1

        if edits and apply_edits:
            open(path, 'w').write(''.join(lines))

    for cls, name, have in unhandled:
        print('  %-38s %-18s %-6s -> not derivable, left alone'
              % (cls, name, have))

    print('\n%d location(s) %s'
          % (total, 'rewritten' if apply_edits else 'would change'))
    return 1 if total and not apply_edits else 0


if __name__ == '__main__':
    sys.exit(main('--apply' in sys.argv))
