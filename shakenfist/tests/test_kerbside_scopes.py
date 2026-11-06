# Copyright 2026 Michael Still and contributors

# The scopes the deploy mints the Kerbside credential with are the scopes the
# endpoints Kerbside scrapes actually require.
#
# kerbside_credentials.yml hard-codes four scopes in an ansible command string,
# and nothing downstream validates them: scopes are free-form strings,
# required_scope() derives the family mechanically from the resource class
# name, and add_key() records whatever it is handed. So a rename of one of
# those classes, a new scope_family override, or a typo in the deploy would
# mint a key which no longer satisfies the scrape, and the only signal would be
# Kerbside answering 403 after the next deploy -- caught, at best, by a weekly
# functional lane. This test reads the scopes out of the deploy and derives the
# requirement from the endpoint classes, so neither copy can drift (#4699).

import os
import re

import yaml

from shakenfist.external_api import admin as api_admin
from shakenfist.external_api import instance as api_instance
from shakenfist.external_api import node as api_node
from shakenfist.external_api import scopes
from shakenfist.tests import base


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


CREDENTIALS_TASKS = os.path.join(
    _repo_root(), 'shakenfist', 'deploy', 'collection', 'roles', 'node', 'tasks',
    'kerbside_credentials.yml')
# Operators of clusters not deployed from the collection mint the credential by
# hand, from this page, which says it is "the same way the deploy does".
VDI_CONSOLE_TOKENS_DOC = os.path.join(
    _repo_root(), 'docs', 'operator_guide', 'vdi_console_tokens.md')

# The endpoints Kerbside reads while scraping a Shaken Fist cluster: the CA and
# the console token signing key's public half, then the nodes and instances it
# builds its console list from.
SCRAPED = [
    api_admin.AdminClusterCaCertificateEndpoint,
    api_admin.AdminVDITokenPublicKeyEndpoint,
    api_node.NodesEndpoint,
    api_instance.InstancesEndpoint,
]

# Kerbside only ever reads. Minting a key which satisfies one of these would
# mean the deploy had handed the console proxy the ability to change the
# cluster, which is the privilege this credential exists to not have.
NOT_SCRAPED = [
    (api_instance.InstancesEndpoint, 'post'),
    (api_node.NodeEndpoint, 'delete'),
]


def _minted_scopes():
    """The scopes kerbside_credentials.yml mints the "kerbside" key with."""
    with open(CREDENTIALS_TASKS) as f:
        tasks = yaml.safe_load(f)
    for task in tasks:
        command = str(task.get('ansible.builtin.command') or '')
        if 'bootstrap-system-key' in command and 'kerbside' in command:
            return re.findall(r'--scope\s+(\S+)', command)
    raise AssertionError(
        'kerbside_credentials.yml no longer runs bootstrap-system-key for the '
        '"kerbside" key; this test can no longer pin the scope contract.')


def _documented_scopes():
    """The scopes the operator guide's hand-mint command passes."""
    with open(VDI_CONSOLE_TOKENS_DOC) as f:
        doc = f.read()
    commands = [block for block in re.findall(r'```bash\n(.*?)```', doc, re.S)
                if 'bootstrap-system-key' in block and 'kerbside' in block]
    if len(commands) != 1:
        raise AssertionError(
            'docs/operator_guide/vdi_console_tokens.md has %d bash blocks running '
            'bootstrap-system-key for the "kerbside" key, not one; this test can '
            'no longer pin the documented command.' % len(commands))
    return re.findall(r'--scope\s+(\S+)', commands[0])


def _required(resource_class, method):
    """The scope a request to resource_class needs, as the API derives it."""
    override = getattr(getattr(resource_class, method), '_sf_scope', None)
    return scopes.required_scope(resource_class, method, override)


class KerbsideScopeContractTestCase(base.ShakenFistTestCase):
    def test_minted_scopes_are_recorded_at_all(self):
        # An empty list is what sf-ctl collapses back to None, the historical
        # wildcard, so losing the --scope options silently restores a full
        # cluster-administrator credential rather than failing.
        self.assertNotEqual(
            [], _minted_scopes(),
            'kerbside_credentials.yml mints the "kerbside" key with no --scope '
            'options, which records no scopes -- a wildcard cluster-administrator '
            'key. Restore the least-privilege scope list.')

    def test_minted_scopes_satisfy_every_scraped_endpoint(self):
        minted = _minted_scopes()
        for resource_class in SCRAPED:
            required = _required(resource_class, 'get')
            self.assertIsNotNone(
                required,
                'No scope can be derived for GET %s, so a scoped token is '
                'refused by default. Give it a scope_family or an explicit '
                '@scope.' % resource_class.__name__)
            self.assertTrue(
                scopes.satisfies(minted, required),
                'Kerbside scrapes GET %s, which requires %s, but the deploy '
                'mints its credential with %s. Update the --scope options in '
                'shakenfist/deploy/collection/roles/node/tasks/'
                'kerbside_credentials.yml.'
                % (resource_class.__name__, required, minted))

    def test_minted_scopes_satisfy_the_admin_axis(self):
        # Two of the scraped endpoints are behind caller_is_admin, which is a
        # second axis: the derived scope above is necessary but not sufficient.
        minted = _minted_scopes()
        self.assertTrue(
            scopes.satisfies(minted, scopes.ADMIN),
            'Kerbside scrapes endpoints behind caller_is_admin, which requires '
            '%s, but the deploy mints its credential with %s. Update the '
            '--scope options in shakenfist/deploy/collection/roles/node/tasks/'
            'kerbside_credentials.yml.' % (scopes.ADMIN, minted))

    def test_minted_scopes_cannot_change_the_cluster(self):
        minted = _minted_scopes()
        for resource_class, method in NOT_SCRAPED:
            required = _required(resource_class, method)
            self.assertIsNotNone(
                required, 'No scope derived for %s %s, so this case checks '
                'nothing.' % (method.upper(), resource_class.__name__))
            self.assertFalse(
                scopes.satisfies(minted, required),
                'The deploy mints the Kerbside credential with %s, which '
                'satisfies %s for %s %s. Kerbside only reads, and the operator '
                'guide promises this credential cannot change the cluster.'
                % (minted, required, method.upper(), resource_class.__name__))

    def test_the_documented_hand_mint_matches_the_deploy(self):
        # The page tells operators of clusters the collection does not deploy
        # to mint the credential "the same way the deploy does". If the deploy's
        # scopes change and the page's do not, those clusters silently get a
        # differently privileged credential -- which is how the page came to
        # document a wildcard key in the first place (#4698).
        self.assertEqual(
            _minted_scopes(), _documented_scopes(),
            'docs/operator_guide/vdi_console_tokens.md documents '
            '%s for the hand-built Kerbside credential, but the deploy mints '
            '%s. The page says it is the same way the deploy does it, so the '
            'two must agree.' % (_documented_scopes(), _minted_scopes()))
