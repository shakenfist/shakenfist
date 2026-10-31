# Copyright 2026 Michael Still and contributors
"""No play in the shared site.yml may declare a literal default.

Play-level vars outrank inventory group_vars in Ansible's precedence, so a
play which declares ``extra_config: "[]"`` as a default shadows the
``extra_config`` an operator set in ``group_vars/all.yml``, which is where
the installation guide says to put it. Only ``-e`` on the command line gets
past it. The cluster configuration play did exactly that for ``extra_config``
and ``http_proxy``, and the two local-wheel plays for ``sf_build_local_wheels``,
so sfcbr's inventory ``extra_config`` was never applied and local wheels could
only be enabled with ``-e``. Defaults belong where the variable is read, with
the ``default`` filter.

A play var is therefore always a template over other variables, never a
literal. This test pins that for every play in the file.
"""

import os

import yaml

from shakenfist.tests import base


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


SITE_YML = os.path.join(_repo_root(), 'examples', '_shared', 'site.yml')

# The variables the operator sets in the inventory which plays used to
# shadow. Named so that the failure for a reintroduced default says why it
# matters, and so that the test is seen to cover the original defect rather
# than only the general rule.
INVENTORY_VARIABLES = ('extra_config', 'http_proxy', 'sf_build_local_wheels')


def _is_template(value):
    return isinstance(value, str) and ('{{' in value or '{%' in value)


class SiteYmlPlayVarsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        with open(SITE_YML) as f:
            self.plays = yaml.safe_load(f)
        self.assertIsInstance(self.plays, list)
        self.assertTrue(self.plays)

    def test_no_play_declares_a_literal_var(self):
        literals = []
        for play in self.plays:
            for name, value in (play.get('vars') or {}).items():
                if not _is_template(value):
                    literals.append((play.get('name'), name, value))
        self.assertEqual(
            [], literals,
            'play vars outrank inventory group_vars, so a literal play var '
            'silently shadows whatever the operator set; apply the default '
            'where the variable is read instead: %r' % literals)

    def test_the_inventory_variables_are_not_play_vars(self):
        for play in self.plays:
            for name in INVENTORY_VARIABLES:
                self.assertNotIn(
                    name, play.get('vars') or {},
                    '%s is an inventory variable; the play %r must not '
                    'declare it' % (name, play.get('name')))

    def test_the_inventory_variables_are_still_read(self):
        # The guard above would also pass if the variables stopped being
        # used at all. Each is still read somewhere in the file, with a
        # default applied at that point.
        with open(SITE_YML) as f:
            text = f.read()
        self.assertIn("http_proxy | default('')", text)
        self.assertIn("extra_config | default('[]')", text)
        self.assertIn('sf_build_local_wheels | default(false)', text)
