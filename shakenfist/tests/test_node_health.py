from shakenfist import blob
from shakenfist import instance
from shakenfist import upload
from shakenfist.tests import base


class HealthDependencyDeclarationTestCase(base.ShakenFistTestCase):
    def test_object_types_declare_health_dependencies(self):
        for cls, expected in [
                (instance.Instance, ['instances', 'image_cache', 'blobs']),
                (blob.Blob, ['blobs']),
                (upload.Upload, ['uploads'])]:
            deps = cls.health_dependencies
            self.assertIsInstance(deps, list)
            self.assertTrue(all(isinstance(d, str) for d in deps))
            self.assertEqual(expected, deps)
