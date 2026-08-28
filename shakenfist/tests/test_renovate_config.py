# Copyright 2026 Michael Still and contributors

"""Tests for renovate.json.

Renovate's pre-commit manager ships disabled, so unless the config opts
in, the hook revisions in .pre-commit-config.yaml are the one dependency
file nobody is watching and they drift silently (issue 3757). Pin the
opt-in here, accepting any of the three forms Renovate documents, so a
future config cleanup cannot quietly turn the manager back off.
"""

import json
import os

from shakenfist.tests import base


def _renovate_path():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, 'renovate.json')


class RenovateConfigTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        with open(_renovate_path()) as f:
            self.config = json.load(f)

    def test_pre_commit_manager_is_enabled(self):
        enabled = (
            self.config.get('pre-commit', {}).get('enabled') is True or
            'pre-commit' in self.config.get('enabledManagers', []) or
            ':enablePreCommit' in self.config.get('extends', []))
        self.assertTrue(
            enabled,
            'renovate.json no longer enables the pre-commit manager, so '
            'the hook revisions in .pre-commit-config.yaml are unmanaged. '
            'Re-enable it with one of: {"pre-commit": {"enabled": true}}, '
            '"pre-commit" in enabledManagers, or ":enablePreCommit" in '
            'extends.')

    def test_enabled_managers_is_not_an_allowlist_hiding_others(self):
        # enabledManagers is an allowlist: naming any manager disables
        # every manager not named. If it is used at all, pip managers
        # must still be present or pyproject.toml goes unwatched instead.
        managers = self.config.get('enabledManagers')
        if managers is not None:
            self.assertIn(
                'pep621', managers,
                'enabledManagers disables every unlisted manager; listing '
                'it without pep621 stops Renovate watching pyproject.toml.')
