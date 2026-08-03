"""Rewrite declared parameter locations to 'path' where the route says so.

Ground truth is app.py's add_resource() calls: any declared name matching a
<segment> of a route the class is mounted on is a path parameter. Edits are
applied at exact AST positions, bottom-up, so nothing is matched by text.
"""
import ast
import collections
import glob
import re
import sys


def routes_by_class(app_path):
    out = collections.defaultdict(list)
    tree = ast.parse(open(app_path).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 'add_resource':
            cls = ast.unparse(node.args[0]).split('.')[-1]
            for a in node.args[1:]:
                try:
                    out[cls].append(ast.literal_eval(a))
                except Exception:
                    pass
    return out


def main(dry_run):
    routes = routes_by_class('shakenfist/external_api/app.py')
    total = 0

    for path in sorted(glob.glob('shakenfist/external_api/*.py')):
        src = open(path).read()
        lines = src.splitlines(keepends=True)
        tree = ast.parse(src)
        edits = []

        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            path_params = set()
            for r in routes.get(cls.name, []):
                path_params |= set(re.findall(r'<([a-z_]+)>', r))
            if not path_params:
                continue

            for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                for dec in fn.decorator_list:
                    if 'swagger_helper' not in ast.unparse(dec):
                        continue
                    call = dec.args[0] if isinstance(dec, ast.Call) and dec.args else None
                    if not (isinstance(call, ast.Call) and len(call.args) >= 3):
                        continue
                    plist = call.args[2]
                    if not isinstance(plist, ast.List):
                        continue
                    for item in plist.elts:
                        if not (isinstance(item, ast.Tuple) and len(item.elts) >= 2):
                            continue
                        try:
                            name = ast.literal_eval(item.elts[0])
                            loc_node = item.elts[1]
                            loc = ast.literal_eval(loc_node)
                        except Exception:
                            continue
                        if name not in path_params or loc == 'path':
                            continue
                        edits.append((loc_node.lineno, loc_node.col_offset,
                                      loc_node.end_lineno, loc_node.end_col_offset,
                                      cls.name, name, loc))

        if not edits:
            continue

        # Apply bottom-up so earlier positions stay valid.
        for lineno, col, end_lineno, end_col, cls, name, loc in sorted(
                edits, reverse=True):
            assert lineno == end_lineno, 'multi-line location literal: %s' % name
            line = lines[lineno - 1]
            assert line[col:end_col] == repr(loc), (
                '%s:%d expected %r got %r' % (path, lineno, repr(loc), line[col:end_col]))
            lines[lineno - 1] = line[:col] + "'path'" + line[end_col:]
            print('  %-34s %-18s %-8s -> path' % (cls, name, loc))
            total += 1

        if not dry_run:
            open(path, 'w').write(''.join(lines))

    print('\n%d location(s) %s' % (total, 'would change' if dry_run else 'rewritten'))


if __name__ == '__main__':
    main('--apply' not in sys.argv)
