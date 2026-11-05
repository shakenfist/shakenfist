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


def _manager_file_patterns(manager):
    # managerFilePatterns replaced fileMatch in Renovate 41. Renovate
    # still migrates the old key at run time, so accept both here rather
    # than silently finding no manager when a config has not caught up.
    return manager.get('managerFilePatterns', manager.get('fileMatch', []))


def _file_pattern_matches(pattern, path):
    # A managerFilePatterns entry is a plain path, or a regex wrapped in
    # slashes (https://docs.renovatebot.com/string-pattern-matching/).
    if pattern.startswith('/') and pattern.endswith('/') and len(pattern) > 1:
        return re.search(pattern[1:-1], path) is not None
    return pattern == path


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

    def test_ansible_pins_are_matched_by_a_custom_manager(self):
        # The sanity job pins ansible-core and ansible-lint exactly, and a
        # customManagers entry is the only thing that moves them: Renovate
        # has no manager for a uv command line in a workflow. A custom
        # manager that stops matching fails silently -- the pin simply
        # never moves and no job errors -- so assert the regex, as written
        # in renovate.json, still extracts exactly the pins the workflow
        # carries (issue #4773).
        relative = '.github/workflows/functional-tests.yml'
        with open(os.path.join(_root(), *relative.split('/'))) as f:
            text = f.read()

        pinned = {
            (tool, version) for tool, version
            in re.findall(r'\b(ansible-core|ansible-lint)==([^\s"\']+)',
                          text)}
        self.assertNotEqual(
            set(), pinned,
            'functional-tests.yml no longer pins ansible-core or '
            'ansible-lint. Un-pinning means the next release of either '
            'breaks whichever pull request runs next, whatever it changed; '
            'if that is intended, remove this test and the Ansible entry '
            'in renovate.json customManagers.')

        managers = [
            manager for manager in self.config.get('customManagers', [])
            if any(_file_pattern_matches(pattern, relative)
                   for pattern in _manager_file_patterns(manager))]
        self.assertEqual(
            1, len(managers),
            'exactly one customManagers entry must cover '
            '.github/workflows/functional-tests.yml, found %d. Note that '
            'the key is managerFilePatterns: fileMatch was renamed in '
            'Renovate 41 and only survives today because Renovate migrates '
            'it at run time.' % len(managers))

        self.assertIn(
            'managerFilePatterns', managers[0],
            'the Ansible customManagers entry still keys its file list as '
            'fileMatch. That option was renamed in Renovate 41 and only '
            'keeps working because Renovate migrates it at run time, '
            'logging "Config migration necessary" on every run; it will '
            'stop working in a future major. Use managerFilePatterns, with '
            'the regex wrapped in slashes.')

        self.assertEqual(
            'pypi', managers[0].get('datasourceTemplate'),
            'the Ansible customManagers entry must look its versions up on '
            'pypi. Any other datasource still matches the file and still '
            'extracts the pins, then finds no versions and opens no pull '
            'request, which looks exactly like there being nothing to do.')

        matched = set()
        for pattern in managers[0].get('matchStrings', []):
            # Renovate's regexes are JavaScript, which spells a named
            # group (?<name>...) where Python spells it (?P<name>...).
            for match in re.finditer(pattern.replace('(?<', '(?P<'), text):
                groups = match.groupdict()
                self.assertEqual(
                    {'depName', 'currentValue'}, set(groups),
                    'a matchStrings regex must capture exactly depName and '
                    'currentValue; Renovate names the dependency and its '
                    'version from those groups, and %r captures %s.' % (
                        pattern, sorted(groups)))
                matched.add((groups['depName'], groups['currentValue']))

        self.assertEqual(
            pinned, matched,
            'renovate.json customManagers does not extract exactly the '
            'Ansible pins in functional-tests.yml. Pinned but unmatched '
            'versions never move; matched but unpinned ones mean the regex '
            'has outlived the thing it watched. Pinned: %s, matched: %s' % (
                sorted(pinned), sorted(matched)))
