from unittest import mock

from shakenfist import blob
from shakenfist import instance
from shakenfist import node_health
from shakenfist import resource_health
from shakenfist import upload
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class _FakeCheck(resource_health.HealthCheck):
    def __init__(self, identity, result):
        self._identity = identity
        self._result = result

    @property
    def identity(self):
        return self._identity

    def check(self):
        return self._result


def _ok(identity):
    return _FakeCheck(
        identity, resource_health.HealthResult(
            identity, resource_health.HealthStatus.OK))


def _fail(identity, status, detail=None):
    return _FakeCheck(
        identity, resource_health.HealthResult(identity, status, detail))


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


class NodeObjectTypesTestCase(base.ShakenFistTestCase):
    def test_hypervisor_hosts_instance_blob_upload(self):
        with mock.patch.object(node_health, 'config') as c:
            c.NODE_IS_HYPERVISOR = True
            types = {t for t, _ in node_health.node_object_types()}
        self.assertEqual(
            {ObjectType.INSTANCE, ObjectType.BLOB, ObjectType.UPLOAD}, types)

    def test_non_hypervisor_hosts_blob_upload_only(self):
        with mock.patch.object(node_health, 'config') as c:
            c.NODE_IS_HYPERVISOR = False
            types = {t for t, _ in node_health.node_object_types()}
        self.assertEqual({ObjectType.BLOB, ObjectType.UPLOAD}, types)


class BuildChecksTestCase(base.ShakenFistTestCase):
    def test_dedups_shared_path_and_maps_all_types(self):
        deps = [
            (ObjectType.INSTANCE, ['instances', 'image_cache', 'blobs']),
            (ObjectType.BLOB, ['blobs']),
        ]
        checks, types_by_identity = node_health.build_checks(
            deps, storage_path='/srv/shakenfist', write_interval=300,
            timeout=30)
        # instances, image_cache, blobs -- blobs appears once despite two
        # types depending on it.
        self.assertEqual(3, len(checks))
        blobs = '/srv/shakenfist/blobs'
        self.assertEqual(
            {ObjectType.INSTANCE, ObjectType.BLOB}, types_by_identity[blobs])
        self.assertEqual(
            {ObjectType.INSTANCE},
            types_by_identity['/srv/shakenfist/instances'])


class EvaluateTestCase(base.ShakenFistTestCase):
    def _deps(self):
        return {
            '/s/instances': {ObjectType.INSTANCE},
            '/s/blobs': {ObjectType.INSTANCE, ObjectType.BLOB},
            '/s/uploads': {ObjectType.UPLOAD},
        }

    def test_all_healthy(self):
        tbi = self._deps()
        checks = [_ok(i) for i in tbi]
        result = node_health.evaluate(checks, tbi)
        self.assertTrue(result.healthy)
        self.assertEqual([], result.failed)
        self.assertEqual(set(), result.affected_types)
        self.assertEqual('all resource health checks passed', result.reason)

    def test_instance_path_failure_affects_instance(self):
        tbi = self._deps()
        checks = [
            _fail('/s/instances', resource_health.HealthStatus.TIMEOUT,
                  'hung'),
            _ok('/s/blobs'), _ok('/s/uploads')]
        result = node_health.evaluate(checks, tbi)
        self.assertFalse(result.healthy)
        self.assertIn(ObjectType.INSTANCE, result.affected_types)
        self.assertIn('/s/instances', result.reason)
        self.assertIn('timeout', result.reason)

    def test_uploads_only_failure_does_not_affect_instance(self):
        tbi = self._deps()
        checks = [
            _ok('/s/instances'), _ok('/s/blobs'),
            _fail('/s/uploads', resource_health.HealthStatus.MISSING, 'gone')]
        result = node_health.evaluate(checks, tbi)
        self.assertFalse(result.healthy)
        self.assertEqual({ObjectType.UPLOAD}, result.affected_types)
        self.assertNotIn(ObjectType.INSTANCE, result.affected_types)

    def test_blobs_failure_affects_instance_and_blob(self):
        tbi = self._deps()
        checks = [
            _ok('/s/instances'),
            _fail('/s/blobs', resource_health.HealthStatus.MISSING, 'eio'),
            _ok('/s/uploads')]
        result = node_health.evaluate(checks, tbi)
        self.assertEqual(
            {ObjectType.INSTANCE, ObjectType.BLOB}, result.affected_types)
