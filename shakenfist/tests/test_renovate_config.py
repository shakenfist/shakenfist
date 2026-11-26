# Copyright 2026 Michael Still and contributors

"""Tests for renovate.json.

Renovate runs centrally, from the fleet runner in
shakenfist/development, and the policy it applies lives once in the
fleet preset local>shakenfist/development:renovate-fleet. This
repository carries only what is particular to it.

Two properties of that arrangement fail silently, so they are pinned
here rather than left to the consistency audit, which only reports a
day later (issue 5079):

* The extends entry naming the preset. Without it the configuration
  still validates and Renovate still runs, on its own defaults rather
  than the fleet's -- no weekly window, and no pre-commit manager, so
  the hook revisions in .pre-commit-config.yaml become the one
  dependency file nobody is watching (issue 3757).
* The absence of a top-level key the preset also sets. A repository's
  own value replaces the preset's outright, so the fleet's policy
  quietly stops applying here while the configuration goes on looking
  correct.

A repository-local Renovate workflow is the third: the fleet runner
manages this repository, and a second Renovate instance over it races
the first on branch creation.
"""

import json
import os
import re
import tomllib

from shakenfist.tests import base


# The fleet preset that carries the whole Renovate policy. It lives in
# shakenfist/development as renovate-fleet.json, and Renovate reads it
# from that repository's default branch.
FLEET_PRESET = 'local>shakenfist/development:renovate-fleet'

# The preset's own top-level keys, less the three a repository may
# legitimately set alongside it. Keys the preset does not set --
# constraints, lockFileMaintenance, customManagers, a manager's own
# block such as github-actions -- are this repository's to use.
PRESET_OWNED_TOP_LEVEL_KEYS = frozenset([
    'assignees',
    'dependencyDashboard',
    'gitAuthor',
    'minimumReleaseAge',
    'pre-commit',
    'prConcurrentLimit',
    'prHourlyLimit',
    'rollbackPrs',
    'schedule',
    'timezone',
])


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

    def test_fleet_preset_is_extended(self):
        self.assertIn(
            FLEET_PRESET, self.config.get('extends', []),
            'renovate.json no longer extends %s, which carries the fleet '
            'Renovate policy: the cadence, the author and assignee, '
            'minimumReleaseAge, and the pre-commit manager that keeps the '
            'hook revisions in .pre-commit-config.yaml watched. Without it '
            'this configuration still validates and Renovate still runs, '
            'on its own defaults.' % FLEET_PRESET)

    def test_no_top_level_key_overrides_the_fleet_preset(self):
        # Renovate resolves a repository's own configuration after the
        # presets it extends, so a top-level key set here replaces the
        # preset's value rather than adding to it. $schema, extends and
        # packageRules are the three exceptions: the first two are how a
        # repository reaches the preset at all, and Renovate appends a
        # repository's packageRules to the preset's.
        #
        # The audit in shakenfist/development reads this set from the
        # preset itself and is authoritative; this list is a local copy
        # so the failure arrives with the commit rather than a day
        # later. A key added to the preset lands here when the audit
        # next reports it.
        overridden = sorted(
            set(self.config) & PRESET_OWNED_TOP_LEVEL_KEYS)
        self.assertEqual(
            [], overridden,
            'renovate.json sets top-level %s, which %s also sets. A '
            'repository\'s own value wins, so the fleet policy stops '
            'applying here while the configuration goes on looking '
            'correct. Delete the key, and if this repository genuinely '
            'needs something different put it in a packageRules entry '
            'scoped to what it is about.' % (
                ', '.join(overridden), FLEET_PRESET))

    def test_no_repository_local_renovate_workflow(self):
        # The fleet runner manages this repository. A copy of the old
        # per-repository workflow, enabled again, races it: two Renovate
        # instances over one repository race on branch creation and
        # produce duplicate or half-written pull requests. A disabled
        # copy is one click from running, so the file must not be here
        # at all.
        path = os.path.join(_root(), '.github', 'workflows', 'renovate.yml')
        self.assertFalse(
            os.path.exists(path),
            '.github/workflows/renovate.yml is back. Renovate runs from '
            'the fleet runner in shakenfist/development, and a copy here '
            'races it on branch creation.')

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
