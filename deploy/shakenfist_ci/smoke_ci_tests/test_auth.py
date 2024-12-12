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

        self.assertNotIn(name, self.system_client.get_namespaces())
        self.system_client.create_namespace(name)
        self.system_client.add_namespace_key(name, 'test', key)
        self.assertIn(
            name, _namespace_names(self.system_client.get_namespaces()))

        self.assertRaises(apiclient.ResourceNotFoundException,
                          self.system_client.delete_namespace_key, name, 'banana')
        self.assertIn(
            name, _namespace_names(self.system_client.get_namespaces()))

        self.system_client.delete_namespace_key(name, 'test')
        self.assertIn(
            name, _namespace_names(self.system_client.get_namespaces()))

        self.system_client.delete_namespace(name)
        self.assertNotIn(
            name, _namespace_names(self.system_client.get_namespaces()))
