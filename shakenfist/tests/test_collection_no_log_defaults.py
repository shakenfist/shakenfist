# Copyright 2026 Michael Still and contributors

"""The default of a no_log role option must be an unmistakable string.

An option declared ``no_log: true`` in a role's ``meta/argument_specs.yml``
does more than keep its own value out of one task's output. Ansible registers
whatever value the argument spec validated as a secret for the rest of the
run, and from ansible-core 2.22 every later occurrence of that exact string
anywhere in the output -- task names, assert messages, unrelated variables --
is replaced with ``$REDACTED$``. The role's own defaults are what gets
validated on any run which does not override them, so a default which is also
an ordinary word redacts that word from a whole deploy's output. That is
issue 4773: the node role defaulted ``system_key``, which the
kerbside_credentials entry point declares ``no_log``, to ``"unknown"``, and
the same bug in the ``shakenfist.kerbside`` collection (kerbside#687) is what
turned a passing task header into ``TASK [$REDACTED$.kerbside.kerbside :
...]`` and failed the Kerbside wiring test on every pull request.

So the default of a no_log option has to be a string which cannot occur
anywhere else: either empty, which the masker ignores, or one of the
``change-me-`` placeholders the example inventories use. Where
``defaults/main.yml`` also sets the variable, that is the value a run actually
validates, so it has to agree with the spec rather than quietly reintroducing
a word.
"""

import os

import yaml

from shakenfist.tests import base


ROLES_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'roles'))

# The placeholder vocabulary the example inventories already use, as in
# examples/cluster/group_vars/all.yml.
PLACEHOLDER_PREFIX = 'change-me-'


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def no_log_options():
    """Yield every no_log option declared by any role in the collection.

    Each item is (role, entry point, option name, option, role defaults).
    An option can be declared in more than one entry point, and each
    declaration is checked: they are independent, and only the ones which
    carry no_log register their value.
    """
    for role in sorted(os.listdir(ROLES_PATH)):
        specs_path = os.path.join(
            ROLES_PATH, role, 'meta', 'argument_specs.yml')
        if not os.path.exists(specs_path):
            continue
        specs = load_yaml(specs_path)

        defaults_path = os.path.join(ROLES_PATH, role, 'defaults', 'main.yml')
        defaults = {}
        if os.path.exists(defaults_path):
            defaults = load_yaml(defaults_path)

        for entry_point, spec in (specs.get('argument_specs') or {}).items():
            for name, option in (spec.get('options') or {}).items():
                if option.get('no_log'):
                    yield role, entry_point, name, option, defaults


class CollectionNoLogDefaultsTestCase(base.ShakenFistTestCase):
    def test_the_collection_declares_no_log_options(self):
        # Without this, every assertion below passes by iterating nothing --
        # a renamed roles directory would read as compliance.
        self.assertNotEqual(
            [], list(no_log_options()),
            f'no no_log role options found under {ROLES_PATH}, so the checks '
            'in this file no longer check anything')

    def test_no_log_defaults_cannot_occur_in_output(self):
        for role, entry_point, name, option, _ in no_log_options():
            default = option.get('default', '')
            self.assertTrue(
                default == '' or default.startswith(PLACEHOLDER_PREFIX),
                f'{role} role, {entry_point} entry point: the no_log option '
                f'{name} defaults to "{default}", which ansible-core 2.22 '
                'registers as a secret and redacts from the whole run. Use '
                f'an empty default or a "{PLACEHOLDER_PREFIX}" placeholder '
                'which appears nowhere else')

    def test_role_defaults_agree_with_no_log_specs(self):
        for role, entry_point, name, option, defaults in no_log_options():
            if name not in defaults:
                continue
            self.assertEqual(
                option.get('default', ''), defaults[name],
                f'{role} role: defaults/main.yml sets {name} to '
                f'"{defaults[name]}" but the {entry_point} entry point '
                'declares a different default for it. The role default is '
                'what a run validates under no_log, so the two must match')

    def test_secret_placeholders_are_not_shared(self):
        # Two no_log options sharing one placeholder would make a redaction
        # ambiguous to diagnose, and the whole point of the placeholder is
        # that it identifies exactly one variable.
        seen = {}
        for role, _, name, option, _ in no_log_options():
            default = option.get('default', '')
            if default == '':
                continue
            seen.setdefault(default, set()).add(f'{role}:{name}')
        for default, owners in seen.items():
            self.assertEqual(
                1, len(owners),
                f'the placeholder "{default}" is the default of more than one '
                f'no_log option ({", ".join(sorted(owners))})')
