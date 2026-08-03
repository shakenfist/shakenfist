# Copyright 2019 Michael Still and contributors
"""Derive the correct location for every declared API parameter.

A one-shot migration for phase 1 of docs/plans/PLAN-api-input-validation.md,
already applied to the tree. Kept because it is the record of how ~130
single-token edits were derived, and because it can be re-run to check that
the tree still agrees with it:

    python3 tools/fix-api-parameter-locations.py            # report only
    python3 tools/fix-api-parameter-locations.py --apply    # rewrite

Run from the repository root. The permanent guard is
shakenfist/tests/external_api/test_parameter_declarations.py; this script is
the fixer, that test is the detector.

Three sources decide where a parameter really comes from, in order:

* a name appearing in a route the class is mounted on is in the ``path``;
* a name in a ``use_kwargs(..., location='query')`` schema is in the ``query``;
* everything else is in the ``body``, because ``log_request`` merges the JSON
  body into handler kwargs and nothing reads the query string.

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

    for path in sorted(glob.glob(API_GLOB)):
        lines = open(path).read().splitlines(keepends=True)
        edits = []

        for cls in [n for n in ast.walk(ast.parse(''.join(lines)))
                    if isinstance(n, ast.ClassDef)]:
            in_path = routes.get(cls.name, set())
            in_query = query_parameters(cls)

            for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                for name, node in declarations(fn):
                    if name in in_path:
                        want = 'path'
                    elif name in in_query:
                        want = 'query'
                    else:
                        want = 'body'
                    try:
                        have = ast.literal_eval(node)
                    except ValueError:
                        continue
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

    print('\n%d location(s) %s'
          % (total, 'rewritten' if apply_edits else 'would change'))
    return 1 if total and not apply_edits else 0


if __name__ == '__main__':
    sys.exit(main('--apply' in sys.argv))
