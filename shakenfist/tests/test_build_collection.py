# Copyright 2026 Michael Still and contributors

"""Tests for tools/build-collection.py.

The script must never modify the checkout it builds from. galaxy.yml is a
tracked file, and rewriting its version in place left the tree dirty: every
later setuptools_scm version gained a .dYYYYMMDD suffix, including the server
wheel a CI deploy builds straight afterwards, and the rewritten galaxy.yml
shipped inside that wheel because package-data then took deploy/**. A second
deploy of the same commit then installed different files, and the node role's
restart-on-change signature restarted every daemon. The kerbside-slim-tier
deploy profile's redeploy check is what found it.

deploy/ no longer ships in the wheel, but a dirty tree still gives every
version built from it the .dYYYYMMDD suffix, so these tests still guard the
version a deploy installs.

The script also installs what it built and checks the result (issue
#4613), so the fake ansible-galaxy below answers both subcommands and
lays down a plausible installed tree. A fake which only answered "build"
would make every assertion about the verification pass against nothing.
"""

import importlib.util
import json
import os
import pathlib
import tempfile
from unittest import mock

from shakenfist.tests import base


SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', 'tools', 'build-collection.py')

spec = importlib.util.spec_from_file_location('build_collection', SCRIPT_PATH)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

GALAXY = '---\nnamespace: shakenfist\nname: shakenfist\nversion: 0.0.0\n'


class BuildCollectionTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.galaxy = self.root / script.COLLECTION_DIR / 'galaxy.yml'
        self.galaxy.parent.mkdir(parents=True)
        self.galaxy.write_text(GALAXY)

        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)

        self.built = {}
        self.installed = {}

        # What the installed copy will claim about itself, and which of
        # the required paths will be there. Tests which are about the
        # verification override these before calling main().
        self.manifest = {
            'collection_info': {
                'namespace': 'shakenfist',
                'name': 'shakenfist',
                'version': '1.2.3-rc4',
            },
        }
        self.install_paths = list(script.REQUIRED_INSTALLED_PATHS)

        def fake_build(argv):
            # What ansible-galaxy would have read, captured before the
            # staging directory is removed.
            source = pathlib.Path(argv[3])
            self.built['source'] = source
            self.built['galaxy'] = (source / 'galaxy.yml').read_text()
            self.built['output'] = argv[argv.index('--output-path') + 1]
            # The real build writes a tarball, and the verification step
            # names it rather than globbing for it, so the name has to
            # exist for the stand-in install below to be reached at all.
            output = pathlib.Path(self.built['output'])
            output.mkdir(parents=True, exist_ok=True)
            (output / 'shakenfist-shakenfist-1.2.3-rc4.tar.gz').write_text('')

        def fake_install(argv, env=None):
            self.installed['tarball'] = argv[3]
            self.installed['env'] = env
            self.installed['dest'] = argv[argv.index('--collections-path') + 1]
            dest = pathlib.Path(self.installed['dest'])
            root = dest / 'ansible_collections' / 'shakenfist' / 'shakenfist'
            for relative in self.install_paths:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('')
            manifest = root / 'MANIFEST.json'
            if manifest.exists():
                manifest.write_text(json.dumps(self.manifest))

        def fake_galaxy(argv, env=None):
            if argv[1:3] == ['collection', 'build']:
                return fake_build(argv)
            if argv[1:3] == ['collection', 'install']:
                return fake_install(argv, env=env)
            raise AssertionError('unexpected ansible-galaxy call: %s' % argv)

        for patcher in [
                mock.patch.object(
                    script, 'collection_version',
                    return_value=('1.2.3rc4', '1.2.3-rc4')),
                mock.patch.object(
                    script.subprocess, 'check_call', side_effect=fake_galaxy)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_checkout_is_not_modified(self):
        script.main()
        self.assertEqual(GALAXY, self.galaxy.read_text())

    def test_the_built_collection_carries_the_version(self):
        script.main()
        self.assertIn('version: 1.2.3-rc4\n', self.built['galaxy'])
        self.assertNotIn('version: 0.0.0', self.built['galaxy'])

    def test_the_build_reads_a_staged_copy(self):
        script.main()
        self.assertNotEqual(
            (self.root / script.COLLECTION_DIR).resolve(),
            self.built['source'].resolve())
        self.assertFalse(self.built['source'].exists())

    def test_the_tarball_lands_in_the_checkout(self):
        script.main()
        self.assertEqual(
            (self.root / script.OUTPUT_DIR).resolve(),
            pathlib.Path(self.built['output']).resolve())

    def test_the_built_tarball_is_installed_and_checked(self):
        """The artifact half of the untested publish job.

        publish-collection has never run, so until a tag is pushed the
        only evidence about the tarball is that it installs -- which is
        why the script checks, and why this asserts it checked the file
        it just built rather than whatever else is in the output
        directory.
        """
        script.main()
        self.assertEqual(
            'dist-collection/shakenfist-shakenfist-1.2.3-rc4.tar.gz',
            self.installed['tarball'])
        # The environment override is cosmetic -- it suppresses a
        # "not part of the configured collections paths" warning which is
        # true and intended -- but it has to name the same directory the
        # install was pointed at, or it suppresses nothing.
        self.assertEqual(
            self.installed['dest'],
            self.installed['env'].get('ANSIBLE_COLLECTIONS_PATH'))

    def test_a_leftover_tarball_is_not_what_gets_verified(self):
        """The verification names the tarball rather than globbing.

        dist-collection/ is not cleaned between builds, and a glob would
        pick whichever name sorts first -- so a build whose version
        rewrite had stopped working could be "verified" against an
        earlier tarball that was fine.
        """
        stale = self.root / script.OUTPUT_DIR
        stale.mkdir(parents=True, exist_ok=True)
        (stale / 'shakenfist-shakenfist-0.0.0.tar.gz').write_text('')

        script.main()
        self.assertEqual(
            'dist-collection/shakenfist-shakenfist-1.2.3-rc4.tar.gz',
            self.installed['tarball'])

    def test_an_installed_copy_missing_a_role_fails_the_build(self):
        self.install_paths = [
            path for path in script.REQUIRED_INSTALLED_PATHS
            if 'roles' not in str(path)]
        error = self.assertRaises(SystemExit, script.main)
        self.assertIn('roles/node/tasks/main.yml', str(error))

    def test_an_installed_copy_missing_a_module_fails_the_build(self):
        self.install_paths = [
            path for path in script.REQUIRED_INSTALLED_PATHS
            if 'plugins' not in str(path)]
        error = self.assertRaises(SystemExit, script.main)
        self.assertIn('plugins/modules/sf_instance.py', str(error))

    def test_a_version_which_did_not_reach_the_artifact_fails(self):
        """The failure the version rewrite is there to prevent.

        If the staged galaxy.yml edit stops working, the build still
        succeeds and ships a collection calling itself 0.0.0. Nothing
        downstream would notice: the tarball installs, and the
        placeholder is a valid semver.
        """
        self.manifest['collection_info']['version'] = '0.0.0'
        error = self.assertRaises(SystemExit, script.main)
        self.assertIn('0.0.0', str(error))
        self.assertIn('1.2.3-rc4', str(error))

    def test_a_collection_installed_under_another_name_fails(self):
        self.manifest['collection_info']['name'] = 'somethingelse'
        error = self.assertRaises(SystemExit, script.main)
        self.assertIn('somethingelse', str(error))
