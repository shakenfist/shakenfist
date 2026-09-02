# Copyright 2026 Michael Still and contributors

"""Tests for the node role's /etc/sf/config template.

Nothing else in this repository renders
``deploy/collection/roles/node/templates/config``. It is rendered by ansible
onto every node, so a lost variable or a conditional block which leaks when
it should be off reaches a real cluster before anything notices. The renders
below guard the properties issues have already been filed about:

* Optional integrations (Loki shipping, federation trust anchors, the
  Kerbside VDI console proxy) must stay entirely absent from the rendered
  file when unset, and render their operator-supplied value verbatim when
  set. Verbatim matters for ``kerbside_url`` in particular: the value is the
  console token audience and must equal kerbside's
  ``SF_CONSOLE_TOKEN_AUDIENCE`` exactly (issue 4004).
* The direct-MariaDB block, which contains a password, is rendered only on
  database-tier nodes.

The template is rendered with StrictUndefined over the role's own defaults,
so a template variable added without a matching default fails here rather
than at deploy time.
"""

import os

import jinja2
import yaml

from shakenfist.tests import base


ROLE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'roles', 'node'))
TEMPLATE_PATH = os.path.join(ROLE_PATH, 'templates', 'config')
DEFAULTS_PATH = os.path.join(ROLE_PATH, 'defaults', 'main.yml')
ARGUMENT_SPECS_PATH = os.path.join(ROLE_PATH, 'meta', 'argument_specs.yml')


# The conditional, off-by-default integrations: role variable, the rendered
# environment variable, and a representative operator-supplied value.
OPTIONAL_VARIABLES = [
    ('loki_base_url', 'SHAKENFIST_LOKI_BASE_URL', 'https://loki.example.com'),
    ('loki_tenant', 'SHAKENFIST_LOKI_TENANT', 'tenant-one'),
    ('loki_auth_header', 'SHAKENFIST_LOKI_AUTH_HEADER', 'Basic c2VjcmV0'),
    ('federation_jwks_ca_bundle', 'SHAKENFIST_FEDERATION_JWKS_CA_BUNDLE',
     '/etc/sf/jwks-ca.pem'),
    ('kerbside_url', 'SHAKENFIST_KERBSIDE_URL', 'https://kerbside.example.com'),
]


def load_defaults():
    with open(DEFAULTS_PATH) as f:
        return yaml.safe_load(f)


def render(**overrides):
    # trim_blocks and keep_trailing_newline match ansible's template module;
    # StrictUndefined turns a template variable with no role default into a
    # test failure instead of a deploy-time one.
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined, trim_blocks=True,
        keep_trailing_newline=True)
    with open(TEMPLATE_PATH) as f:
        template = env.from_string(f.read())
    context = load_defaults()
    context.update(overrides)
    return template.render(**context)


class NodeConfigTemplateTestCase(base.ShakenFistTestCase):
    def test_optional_integrations_absent_by_default(self):
        rendered = render()
        for role_var, env_var, _ in OPTIONAL_VARIABLES:
            self.assertNotIn(
                env_var, rendered,
                f'{env_var} rendered with {role_var} at its empty default, '
                'so the integration is no longer off-by-default')

    def test_optional_integrations_render_verbatim(self):
        for role_var, env_var, value in OPTIONAL_VARIABLES:
            rendered = render(**{role_var: value})
            self.assertIn(
                f'{env_var}="{value}"\n', rendered,
                f'{role_var} did not render verbatim as {env_var}')

    def test_direct_mariadb_only_on_database_nodes(self):
        self.assertNotIn('SHAKENFIST_MARIADB_PASSWORD', render())
        rendered = render(node_is_database_node=True)
        self.assertIn('SHAKENFIST_MARIADB_PASSWORD="unknown"\n', rendered)
        self.assertIn('SHAKENFIST_NODE_IS_DATABASE_NODE=True\n', rendered)

    def test_defaults_are_documented_in_argument_specs(self):
        with open(ARGUMENT_SPECS_PATH) as f:
            specs = yaml.safe_load(f)
        options = specs['argument_specs']['main']['options']
        for name in load_defaults():
            self.assertIn(
                name, options,
                f'role default {name} is not documented in argument_specs.yml')
