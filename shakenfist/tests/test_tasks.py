import testtools

from shakenfist import exceptions
from shakenfist import tasks
from shakenfist.tests import base


class TasksEqTestCase(base.ShakenFistTestCase):
    def test_QueueTask_eq(self):
        self.assertEqual(tasks.PreflightInstanceTask('abcd'),
                         tasks.PreflightInstanceTask('abcd'))

        self.assertEqual(tasks.PreflightInstanceTask('abcd', []),
                         tasks.PreflightInstanceTask('abcd', []))

        self.assertEqual(tasks.PreflightInstanceTask('abcd'),
                         tasks.PreflightInstanceTask('abcd', []))

        self.assertNotEqual(tasks.PreflightInstanceTask('abcd'),
                            tasks.PreflightInstanceTask('abcd', ['netuuid']))

        self.assertEqual(tasks.PreflightInstanceTask('abcd', ['a1', 'b2']),
                         tasks.PreflightInstanceTask('abcd', ['a1', 'b2']))

        self.assertNotEqual(tasks.PreflightInstanceTask('abcd', ['a1', 'b2']),
                            tasks.PreflightInstanceTask('abcd', ['a1']))

        with testtools.ExpectedException(NotImplementedError):
            self.assertEqual(tasks.PreflightInstanceTask('abcd', ['a1', 'b2']),
                             42)

        self.assertEqual(tasks.DeleteInstanceTask('abcd'),
                         tasks.DeleteInstanceTask('abcd'))

        self.assertEqual(tasks.ImageTask('http://someurl'),
                         tasks.ImageTask('http://someurl'))

        self.assertNotEqual(tasks.ImageTask('http://someurl'),
                            tasks.ImageTask('http://someur'))

        self.assertEqual(tasks.FetchImageTask('http://someurl', 'fake_uuid'),
                         tasks.FetchImageTask('http://someurl', 'fake_uuid'))

        self.assertNotEqual(tasks.FetchImageTask('http://someurl'),
                            tasks.FetchImageTask('http://someurl', 'fake_uuid'))

        self.assertNotEqual(tasks.FetchImageTask('http://someurl', 'fake_uuid'),
                            tasks.FetchImageTask('http://someurl', 'fake_uudd'))

        self.assertNotEqual(tasks.FetchImageTask('http://someurl', 'fake_uuid'),
                            tasks.FetchImageTask('http://someurm', 'fake_uuid'))


class InstanceTasksTestCase(base.ShakenFistTestCase):
    def test_InstanceTask(self):
        i = tasks.InstanceTask('some-uuid')
        self.assertEqual('some-uuid', i.instance_uuid())

        with testtools.ExpectedException(exceptions.NoInstanceTaskException):
            tasks.InstanceTask(None)

        with testtools.ExpectedException(exceptions.NoInstanceTaskException):
            tasks.InstanceTask({'uuid': 'some-uuid'})

        with testtools.ExpectedException(exceptions.NetworkNotListTaskException):
            tasks.InstanceTask('uuid', 'not a list')

    def test_FetchImageTask(self):
        with testtools.ExpectedException(exceptions.NoURLImageFetchTaskException):
            tasks.FetchImageTask(None)

        with testtools.ExpectedException(exceptions.NoURLImageFetchTaskException):
            tasks.FetchImageTask(1234)


class NetworkTasksTestCase(base.ShakenFistTestCase):
    def test_NetworkTask(self):
        n = tasks.NetworkTask('some-uuid')
        self.assertEqual('some-uuid', n.network_uuid())

        with testtools.ExpectedException(exceptions.NoNetworkTaskException):
            tasks.NetworkTask(None)

        with testtools.ExpectedException(exceptions.NoNetworkTaskException):
            tasks.NetworkTask({'uuid': 'test-uuid'})

    def test_DeployNetworkTask(self):
        d = tasks.DeployNetworkTask('some-uuid')

        # Test hashing via equality
        self.assertEqual(d, tasks.DeployNetworkTask('some-uuid'))
        self.assertNotEqual(d, tasks.DeployNetworkTask('diff-uuid'))
