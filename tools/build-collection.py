#!/usr/bin/env python3
# Copyright 2019 Michael Still and contributors
#
# Build the shakenfist.shakenfist ansible-galaxy collection.
#
# Derives the Shaken Fist version from setuptools_scm (the same source as
# util_general.get_version(), so the collection and the server package never
# drift), rewrites it into the collection's galaxy.yml as a valid semver
# string, and builds the collection tarball into dist-collection/.
#
# Used by the build-collection job in .github/workflows/release.yml. Run from
# the repository root.
import pathlib
import re
import subprocess
import sys

from packaging.version import Version


COLLECTION_DIR = pathlib.Path('shakenfist/deploy/collection')
OUTPUT_DIR = pathlib.Path('dist-collection')


def collection_version():
    """Return (pep440, semver) for the current checkout.

    ansible-galaxy validates galaxy.yml's version with the semantic_version
    library, which is stricter than PEP 440: a prerelease must be separated
    from the release with '-' (so 0.8.0rc5 -> 0.8.0-rc5), and the dev/local
    segments become dot-separated prerelease identifiers and '+' build
    metadata respectively. Decompose with packaging and reassemble as semver.
    """
    raw = subprocess.check_output(
        [sys.executable, '-m', 'setuptools_scm'], text=True).strip()
    v = Version(raw)

    core = '%d.%d.%d' % (v.major, v.minor, v.micro)
    prerelease = []
    if v.pre is not None:
        prerelease.append('%s%d' % (v.pre[0], v.pre[1]))
    if v.dev is not None:
        prerelease.append('dev%d' % v.dev)

    semver = core
    if prerelease:
        semver += '-' + '.'.join(prerelease)
    if v.local:
        semver += '+' + v.local

    return raw, semver


def main():
    raw, semver = collection_version()
    print('Shaken Fist version %s -> collection version %s' % (raw, semver))

    galaxy = COLLECTION_DIR / 'galaxy.yml'
    text = re.sub(
        r'(?m)^version:.*$', 'version: %s' % semver, galaxy.read_text())
    galaxy.write_text(text)

    # Use the ansible-galaxy that belongs to the interpreter running us (the
    # build venv), not whatever bare name happens to be on PATH -- running
    # venv/bin/python3 directly does not put the venv on PATH.
    galaxy_bin = pathlib.Path(sys.executable).parent / 'ansible-galaxy'
    if not galaxy_bin.exists():
        galaxy_bin = pathlib.Path('ansible-galaxy')

    OUTPUT_DIR.mkdir(exist_ok=True)
    subprocess.check_call([
        str(galaxy_bin), 'collection', 'build', str(COLLECTION_DIR),
        '--output-path', str(OUTPUT_DIR), '--force'])


if __name__ == '__main__':
    main()
