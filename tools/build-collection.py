#!/usr/bin/env python3
# Copyright 2019 Michael Still and contributors
#
# Build the shakenfist.shakenfist ansible-galaxy collection.
#
# Derives the Shaken Fist version from setuptools_scm (the same source as
# util_general.get_version(), so the collection and the server package never
# drift), rewrites it into a staged copy of the collection's galaxy.yml as a
# valid semver string, and builds the collection tarball into
# dist-collection/.
#
# The checkout itself is never modified. galaxy.yml is a tracked file, so
# rewriting it in place left the tree dirty: every later setuptools_scm
# version, including the server wheel a CI deploy builds next, gained a
# .dYYYYMMDD suffix, and that rewritten galaxy.yml shipped inside the wheel
# (package-data takes deploy/**). A second deploy of the same commit then saw
# different installed files and restarted every daemon.
#
# Used by the build-collection job in .github/workflows/release.yml, and by
# shakenfist/actions' deploy-collection.sh. Run from the repository root.
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from packaging.version import Version


COLLECTION_DIR = pathlib.Path('shakenfist/deploy/collection')
OUTPUT_DIR = pathlib.Path('dist-collection')

NAMESPACE = 'shakenfist'
NAME = 'shakenfist'

# What has to be there once the tarball is installed. Not an inventory of
# the collection -- it is the three things an installed copy is useless
# without, chosen so this list does not have to be edited every time a
# role or module is added: the manifest Galaxy reads, one native module
# (so plugins/ was packaged at all), and one role's entry point (so
# roles/ was too). A build_ignore entry or a packaging change which drops
# either tree fails here instead of on an operator's control node.
REQUIRED_INSTALLED_PATHS = (
    pathlib.Path('MANIFEST.json'),
    pathlib.Path('plugins/modules/sf_instance.py'),
    pathlib.Path('roles/node/tasks/main.yml'),
)


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


def verify_installable(galaxy_bin, tarball, semver):
    """Install the tarball we just built, and check what comes out.

    The release workflow's publish-collection job has never run (issue
    #4613): no tag has been pushed since the job was added, so the only
    thing which has ever been established about the artifact is that
    ansible-galaxy was willing to build it. "Willing to build it" is not
    "an operator can install it and get the roles": a build_ignore entry,
    a packaging change or a rename can produce a tarball which builds
    cleanly and installs to something unusable, and the first person to
    find out would be whoever installed the first published release.

    Installing from a local tarball needs no network -- galaxy.yml
    declares no dependencies -- so this is a tar extraction and a few
    stats, and it runs wherever the collection is built: the release
    job, every pull request, and shakenfist/actions' deploy-collection.sh.
    It does not exercise the publish itself, which only a tag can.
    """
    with tempfile.TemporaryDirectory() as tmp:
        dest = pathlib.Path(tmp) / 'collections'
        # ANSIBLE_COLLECTIONS_PATH as well as --collections-path, only to
        # keep the log readable: without it ansible-galaxy warns that the
        # install path is not one of the configured collection paths,
        # which is true, intended, and exactly the sort of line that
        # misleads whoever is watching a release for the first time.
        env = dict(os.environ, ANSIBLE_COLLECTIONS_PATH=str(dest))
        subprocess.check_call([
            str(galaxy_bin), 'collection', 'install', str(tarball),
            '--collections-path', str(dest), '--force'], env=env)

        installed = dest / 'ansible_collections' / NAMESPACE / NAME
        missing = [str(path) for path in REQUIRED_INSTALLED_PATHS
                   if not (installed / path).exists()]
        if missing:
            raise SystemExit(
                'the built collection installed to %s without %s -- the '
                'tarball is not a usable collection'
                % (installed, ', '.join(sorted(missing))))

        manifest = json.loads((installed / 'MANIFEST.json').read_text())
        info = manifest.get('collection_info') or {}
        found = (info.get('namespace'), info.get('name'), info.get('version'))
        expected = (NAMESPACE, NAME, semver)
        if found != expected:
            raise SystemExit(
                'the installed collection calls itself %s but it was built '
                'as %s, so the version injected into the staged galaxy.yml '
                'did not reach the artifact' % (found, expected))

        print('Built collection installs as %s.%s %s' % expected)


def main():
    raw, semver = collection_version()
    print('Shaken Fist version %s -> collection version %s' % (raw, semver))

    # Use the ansible-galaxy that belongs to the interpreter running us (the
    # build venv), not whatever bare name happens to be on PATH -- running
    # venv/bin/python3 directly does not put the venv on PATH.
    galaxy_bin = pathlib.Path(sys.executable).parent / 'ansible-galaxy'
    if not galaxy_bin.exists():
        galaxy_bin = pathlib.Path('ansible-galaxy')

    OUTPUT_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staged = pathlib.Path(tmp) / 'collection'
        shutil.copytree(COLLECTION_DIR, staged)
        galaxy = staged / 'galaxy.yml'
        text = re.sub(
            r'(?m)^version:.*$', 'version: %s' % semver, galaxy.read_text())
        galaxy.write_text(text)

        subprocess.check_call([
            str(galaxy_bin), 'collection', 'build', str(staged),
            '--output-path', str(OUTPUT_DIR), '--force'])

    # Named rather than globbed: ansible-galaxy derives the file name from
    # the staged galaxy.yml, so naming it here is also a check that it
    # wrote the file we think it did. A glob would quietly verify a
    # leftover tarball from an earlier build instead.
    verify_installable(
        galaxy_bin,
        OUTPUT_DIR / ('%s-%s-%s.tar.gz' % (NAMESPACE, NAME, semver)),
        semver)


if __name__ == '__main__':
    main()
