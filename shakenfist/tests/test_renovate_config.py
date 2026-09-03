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
import re
import tomllib

from shakenfist.tests import base


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _renovate_path():
    return os.path.join(_root(), 'renovate.json')


def _package_name_matches(pattern, name):
    # matchPackageNames entries are exact names, or regexes wrapped in
    # slashes (https://docs.renovatebot.com/string-pattern-matching/).
    if pattern.startswith('/') and pattern.endswith('/'):
        return re.search(pattern[1:-1], name) is not None
    return pattern == name


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

    def test_oslo_packages_are_grouped_in_lockstep(self):
        # The OpenStack oslo libraries are released in lockstep and share
        # internal interfaces, so a partial update can fail dependency
        # resolution or break at runtime. Every oslo.* dependency in
        # pyproject.toml must be covered by a single grouped packageRules
        # entry, and that rule must not be restricted by matchUpdateTypes
        # (issue 4045).
        with open(os.path.join(_root(), 'pyproject.toml'), 'rb') as f:
            pyproject = tomllib.load(f)
        oslo_packages = sorted(
            re.split('[<>=!~ ;\\[]', dep)[0]
            for dep in pyproject['project']['dependencies']
            if dep.startswith('oslo.'))
        self.assertNotEqual(
            [], oslo_packages,
            'pyproject.toml no longer depends on any oslo libraries; '
            'remove this test and the oslo group in renovate.json.')

        oslo_rules = [
            rule for rule in self.config.get('packageRules', [])
            if rule.get('groupName') and any(
                _package_name_matches(pattern, package)
                for pattern in rule.get('matchPackageNames', [])
                for package in oslo_packages)]
        self.assertEqual(
            1, len({rule['groupName'] for rule in oslo_rules}),
            'renovate.json must group all oslo packages under exactly one '
            'groupName, found: %s' % oslo_rules)

        for package in oslo_packages:
            covering = [
                rule for rule in oslo_rules if any(
                    _package_name_matches(pattern, package)
                    for pattern in rule.get('matchPackageNames', []))]
            self.assertNotEqual(
                [], covering,
                '%s is not covered by the oslo group in renovate.json' % package)
            for rule in covering:
                self.assertNotIn(
                    'matchUpdateTypes', rule,
                    'the oslo group must apply to all update types, but the '
                    'rule covering %s is restricted by matchUpdateTypes' % package)

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
