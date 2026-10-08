# Copyright 2026 Michael Still and contributors

"""Tests for tools/build-collection.py.

The script must never modify the checkout it builds from. galaxy.yml is a
tracked file, and rewriting its version in place left the tree dirty: every
later setuptools_scm version gained a .dYYYYMMDD suffix, including the server
wheel a CI deploy builds straight afterwards, and the rewritten galaxy.yml
shipped inside that wheel because package-data takes deploy/**. A second
deploy of the same commit then installed different files, and the node role's
restart-on-change signature restarted every daemon. The kerbside-slim-tier
deploy profile's redeploy check is what found it.
"""

import importlib.util
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

        def fake_build(argv):
            # What ansible-galaxy would have read, captured before the
            # staging directory is removed.
            source = pathlib.Path(argv[3])
            self.built['source'] = source
            self.built['galaxy'] = (source / 'galaxy.yml').read_text()
            self.built['output'] = argv[argv.index('--output-path') + 1]

        for patcher in [
                mock.patch.object(
                    script, 'collection_version',
                    return_value=('1.2.3rc4', '1.2.3-rc4')),
                mock.patch.object(
                    script.subprocess, 'check_call', side_effect=fake_build)]:
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
