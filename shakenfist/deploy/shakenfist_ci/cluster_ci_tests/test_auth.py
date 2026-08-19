import json

import requests
from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestAuth(base.BaseTestCase):
    def test_namespaces(self):
        name = 'ci-auth-%s' % self._uniquifier()
        key = self._uniquifier()

        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_initial', content.text_content(json.dumps(
            namespaces, indent=4, sort_keys=True)))
        self.assertNotIn(name, base.namespace_names(namespaces))
        self.system_client.create_namespace(name)
        self.system_client.add_namespace_key(name, 'test', key)
        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_after_create', content.text_content(
            json.dumps(namespaces, indent=4, sort_keys=True)))
        self.assertIn(name, base.namespace_names(namespaces))

        self.assertRaises(apiclient.ResourceNotFoundException,
                          self.system_client.delete_namespace_key, name, 'banana')
        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_after_bad_delete', content.text_content(
            json.dumps(namespaces, indent=4, sort_keys=True)))
        self.assertIn(name, base.namespace_names(namespaces))

        self.system_client.delete_namespace_key(name, 'test')
        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_after_key_delete', content.text_content(
            json.dumps(namespaces, indent=4, sort_keys=True)))
        self.assertIn(name, base.namespace_names(namespaces))

        self.system_client.delete_namespace(name)
        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_after_ns_delete', content.text_content(
            json.dumps(namespaces, indent=4, sort_keys=True)))
        self.assertNotIn(name, base.namespace_names(namespaces))


class TestRevokedTokens(base.BaseTestCase):
    """Revoking a key must reject its outstanding tokens with a clean 401.

    Issue 3606: the API logs and audits these rejections now, and doing
    that on the failure path is only safe if it cannot itself fail. A
    500 here would mean the rejection path is broken -- which the unit
    tests cannot show, because they do not talk to a real eventlog. The
    raw token is held rather than an apiclient, because the client
    transparently re-authenticates on a 401 and so can never present a
    stale token twice.
    """

    def setUp(self):
        super().setUp()

        self.base_url = self.system_client.base_url
        self.namespace = 'ci-revoke-%s' % self._uniquifier()
        self.key = self._uniquifier()

        self.system_client.create_namespace(self.namespace)
        self.addCleanup(self.system_client.delete_namespace, self.namespace)
        self.system_client.add_namespace_key(self.namespace, 'test', self.key)

    def _bearer(self, key):
        resp = requests.post(
            self.base_url + '/auth',
            json={'namespace': self.namespace, 'key': key}, timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)
        return resp.json()['access_token']

    def _get_instances(self, token):
        return requests.get(
            self.base_url + '/instances',
            headers={'Authorization': 'Bearer %s' % token}, timeout=30)

    def test_rotated_key_rejects_outstanding_token(self):
        token = self._bearer(self.key)
        resp = self._get_instances(token)
        self.assertEqual(200, resp.status_code, resp.text)

        # Re-adding a key of the same name rotates it, which mints a new
        # nonce and so revokes every token minted from the old one.
        self.system_client.add_namespace_key(
            self.namespace, 'test', self._uniquifier())

        resp = self._get_instances(token)
        self.addDetail('replay_response', content.text_content(resp.text))
        self.assertEqual(401, resp.status_code, resp.text)

    def test_deleted_key_rejects_outstanding_token(self):
        token = self._bearer(self.key)
        self.assertEqual(200, self._get_instances(token).status_code)

        self.system_client.delete_namespace_key(self.namespace, 'test')

        resp = self._get_instances(token)
        self.addDetail('replay_response', content.text_content(resp.text))
        self.assertEqual(401, resp.status_code, resp.text)
