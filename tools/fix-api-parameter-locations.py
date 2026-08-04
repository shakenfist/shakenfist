# Copyright 2019 Michael Still and contributors
"""Rewrite each declared API parameter location to the derived one.

Written as a one-shot migration for phase 1 of
docs/plans/PLAN-api-input-validation.md, and kept as the corrector for a
property CI enforces:

    python3 tools/fix-api-parameter-locations.py            # report only
    python3 tools/fix-api-parameter-locations.py --apply    # rewrite

Run from the repository root. Report mode exits non-zero when the tree
disagrees with the derivation, and runs on every commit touching
external_api/ via the check-api-parameter-locations pre-commit hook.

The derivation itself lives in shakenfist/external_api/declarations.py,
which shakenfist/tests/external_api/test_parameter_declarations.py also
imports -- so what this script would rewrite is exactly what CI fails
on, rather than two implementations of the same idea kept in step by
hand.

Edits are applied at exact AST positions and check the literal they
replace, so nothing is matched by text.
"""
import collections
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shakenfist.external_api import declarations  # noqa: E402


def main(apply_edits, api_dir=declarations.API_DIR, app=None):
    drifted, underivable = declarations.audit(api_dir, app)

    by_file = collections.defaultdict(list)
    for declared, want in drifted:
        by_file[declared.path].append((declared, want))

    for path, edits in sorted(by_file.items()):
        with open(path) as f:
            lines = f.read().splitlines(keepends=True)

        # Bottom-up, so an edit cannot move the position of one not yet
        # applied.
        for declared, want in sorted(
                edits, key=lambda e: (e[0].location_node.lineno,
                                      e[0].location_node.col_offset),
                reverse=True):
            node = declared.location_node
            line = lines[node.lineno - 1]
            # Guards, not asserts: python -O strips asserts, and this
            # splices bytes into source at an offset.
            if node.lineno != node.end_lineno:
                raise SystemExit(
                    'multi-line location literal for %s' % declared.name)
            if line[node.col_offset:node.end_col_offset] != repr(
                    declared.location):
                raise SystemExit(
                    '%s:%d does not hold %r'
                    % (path, node.lineno, declared.location))

            lines[node.lineno - 1] = (line[:node.col_offset] + repr(want)
                                      + line[node.end_col_offset:])
            print('  %-38s %-18s %-6s -> %s'
                  % (declared.cls, declared.name, declared.location, want))

        if apply_edits:
            with open(path, 'w') as f:
                f.write(''.join(lines))

    for declared, _ in underivable:
        print('  %-38s %-18s %-6s -> not derivable, left alone'
              % (declared.cls, declared.name, declared.location))

    print('\n%d location(s) %s'
          % (len(drifted), 'rewritten' if apply_edits else 'would change'))
    return 1 if drifted and not apply_edits else 0


if __name__ == '__main__':
    sys.exit(main('--apply' in sys.argv))
