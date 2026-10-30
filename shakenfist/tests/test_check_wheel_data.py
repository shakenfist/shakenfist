# Copyright 2026 Michael Still and contributors

"""Tests for tools/check-wheel-data.py.

The script runs in the release workflow and fails the release unless every
built wheel carries each shakenfist/data/*.yaml file. It has four exit
paths and, until these tests, nothing exercised any of them before a
release did (issue 4622). Each case below builds a fake repository root --
the data files the script globs for and a dist/ of small zip files named
as wheels -- and asserts on main()'s return value from inside that root.
"""

import contextlib
import importlib.util
import io
import os
import tempfile
import zipfile
from unittest import mock

from shakenfist.tests import base


SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', 'tools', 'check-wheel-data.py')


def _load_script():
    # A standalone script rather than an importable module, as the other
    # tools/*.py checks are, so it is loaded by path.
    spec = importlib.util.spec_from_file_location(
        'check_wheel_data', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_wheel_data = _load_script()


class CheckWheelDataTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.dist = os.path.join(self.root, 'dist')
        os.makedirs(self.dist)
        os.makedirs(os.path.join(self.root, 'shakenfist', 'data'))

        # The script globs relative to the current directory and takes
        # the dist directory as its only argument, so each test runs it
        # from inside the fake root.
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)

    def _data_file(self, name):
        with open(os.path.join('shakenfist', 'data', name), 'w') as f:
            f.write('budget: 1\n')

    def _wheel(self, name, members):
        path = os.path.join(self.dist, name)
        with zipfile.ZipFile(path, 'w') as z:
            for member in members:
                z.writestr(member, 'x')
        return path

    def _run(self):
        out = io.StringIO()
        with mock.patch('sys.argv', ['check-wheel-data.py', 'dist']), \
                contextlib.redirect_stdout(out):
            rc = check_wheel_data.main()
        return rc, out.getvalue()

    def test_a_wheel_carrying_every_data_file_passes(self):
        self._data_file('budget.yaml')
        self._data_file('other.yaml')
        self._wheel('shakenfist-1.0-py3-none-any.whl',
                    ['shakenfist/__init__.py',
                     'shakenfist/data/budget.yaml',
                     'shakenfist/data/other.yaml'])

        rc, out = self._run()

        self.assertEqual(0, rc, out)
        self.assertIn('carries all 2 data files', out)

    def test_a_wheel_missing_a_data_file_fails_and_names_it(self):
        self._data_file('budget.yaml')
        self._data_file('other.yaml')
        self._wheel('shakenfist-1.0-py3-none-any.whl',
                    ['shakenfist/__init__.py',
                     'shakenfist/data/budget.yaml'])

        rc, out = self._run()

        self.assertEqual(1, rc, out)
        self.assertIn(
            'shakenfist-1.0-py3-none-any.whl: missing shakenfist/data/other.yaml',
            out)

    def test_one_failing_wheel_fails_the_whole_run(self):
        # The release builds more than one wheel, and a run which stopped
        # at the first good one, or reported only the last one it saw,
        # would let a bad wheel ship. The bad wheel sorts first so that
        # a last-wheel-wins verdict is the one this catches.
        self._data_file('budget.yaml')
        self._wheel('shakenfist-0.9-py3-none-any.whl',
                    ['shakenfist/__init__.py'])
        self._wheel('shakenfist-1.0-py3-none-any.whl',
                    ['shakenfist/data/budget.yaml'])

        rc, out = self._run()

        self.assertEqual(1, rc, out)
        self.assertIn('shakenfist-0.9-py3-none-any.whl: missing', out)
        self.assertIn('shakenfist-1.0-py3-none-any.whl: carries all', out)

    def test_no_wheels_in_the_dist_directory_fails(self):
        # A release step whose build produced nothing must not pass the
        # check that guards what the build produced.
        self._data_file('budget.yaml')

        rc, out = self._run()

        self.assertEqual(1, rc, out)
        self.assertIn('No wheels found in dist', out)

    def test_no_data_files_under_the_current_directory_fails(self):
        # Run from outside the repository root the glob finds nothing,
        # and a wheel then trivially carries every one of no files. That
        # has to be a failure rather than a pass.
        self._wheel('shakenfist-1.0-py3-none-any.whl',
                    ['shakenfist/data/budget.yaml'])

        rc, out = self._run()

        self.assertEqual(1, rc, out)
        self.assertIn('run this from the repository root', out)
