#!/usr/bin/env python3
"""Fail unless every built wheel carries the package's runtime data files.

pyproject.toml declares shakenfist/data/*.yaml as package data, and
load_budget() reads it through importlib.resources on a deployed node. No unit
test can show the declaration works, because a source checkout resolves
shakenfist.data as a namespace package whatever the packaging says, so this
reads the wheels the release built. Run it from the repository root, with the
directory holding the wheels as its argument.
"""

import glob
import os
import sys
import zipfile


DATA_GLOB = 'shakenfist/data/*.yaml'


def main():
    dist = sys.argv[1] if len(sys.argv) > 1 else 'dist'
    wanted = sorted(glob.glob(DATA_GLOB))
    if not wanted:
        print(f'No {DATA_GLOB} found; run this from the repository root.')
        return 1

    wheels = sorted(glob.glob(os.path.join(dist, '*.whl')))
    if not wheels:
        print(f'No wheels found in {dist}.')
        return 1

    failed = False
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as z:
            members = set(z.namelist())
        missing = [w for w in wanted if w not in members]
        for m in missing:
            print(f'{os.path.basename(wheel)}: missing {m}')
        if missing:
            failed = True
        else:
            print(f'{os.path.basename(wheel)}: carries all {len(wanted)} data files')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
