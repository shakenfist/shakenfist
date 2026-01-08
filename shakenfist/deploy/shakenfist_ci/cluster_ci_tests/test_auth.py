import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


def _namespace_names(namespaces):
    namespace_names = []
    for ns in namespaces:
        namespace_names.append(ns['name'])
    return namespace_names


class TestAuth(base.BaseTestCase):
    def test_namespaces(self):
        name = 'ci-auth-%s' % self._uniquifier()
        key = self._uniquifier()

        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_initial', content.text_content(json.dumps(
            namespaces, indent=4, sort_keys=True)))
        self.assertNotIn(name, namespaces)
        self.system_client.create_namespace(name)
        self.system_client.add_namespace_key(name, 'test', key)
        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_after_create', content.text_content(
            json.dumps(namespaces, indent=4, sort_keys=True)))
        self.assertIn(name, _namespace_names(namespaces))

        self.assertRaises(apiclient.ResourceNotFoundException,
                          self.system_client.delete_namespace_key, name, 'banana')
        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_after_bad_delete', content.text_content(
            json.dumps(namespaces, indent=4, sort_keys=True)))
        self.assertIn(name, _namespace_names(namespaces))

        self.system_client.delete_namespace_key(name, 'test')
        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_after_key_delete', content.text_content(
            json.dumps(namespaces, indent=4, sort_keys=True)))
        self.assertIn(name, _namespace_names(namespaces))

        self.system_client.delete_namespace(name)
        namespaces = self.system_client.get_namespaces()
        self.addDetail('namespaces_after_ns_delete', content.text_content(
            json.dumps(namespaces, indent=4, sort_keys=True)))
        self.assertNotIn(name, _namespace_names(namespaces))
