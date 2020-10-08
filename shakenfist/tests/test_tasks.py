import testtools

from shakenfist import tasks
from shakenfist import exceptions


class TasksEqTestCase(testtools.TestCase):
    def test_QueueTask_eq(self):
        self.assertEqual(tasks.PreflightInstanceTask('abcd'),
                         tasks.PreflightInstanceTask('abcd'))

        self.assertEqual(tasks.PreflightInstanceTask('abcd', []),
                         tasks.PreflightInstanceTask('abcd', []))

        self.assertEqual(tasks.PreflightInstanceTask('abcd', ['a1', 'b2']),
                         tasks.PreflightInstanceTask('abcd', ['a1', 'b2']))

        self.assertNotEqual(tasks.PreflightInstanceTask('abcd', ['a1', 'b2']),
                            tasks.PreflightInstanceTask('abcd', ['a1']))

        with testtools.ExpectedException(NotImplementedError):
            self.assertEqual(tasks.PreflightInstanceTask('abcd', ['a1', 'b2']),
                             42)

        self.assertEqual(tasks.DeleteInstanceTask('abcd', 'somestate'),
                         tasks.DeleteInstanceTask('abcd', 'somestate'))

        self.assertNotEqual(tasks.DeleteInstanceTask('abcd', 'somestate'),
                            tasks.DeleteInstanceTask('abcd', 'something'))

        self.assertEqual(tasks.DeleteInstanceTask('abcd', 'somestate', 'dunno'),
                         tasks.DeleteInstanceTask('abcd', 'somestate', 'dunno'))

        self.assertEqual(tasks.ImageTask('http://someurl'),
                         tasks.ImageTask('http://someurl'))

        self.assertNotEqual(tasks.ImageTask('http://someurl'),
                            tasks.ImageTask('http://someur'))

        self.assertEqual(tasks.FetchImageTask('http://someurl', 'fake_uuid'),
                         tasks.FetchImageTask('http://someurl', 'fake_uuid'))

        self.assertNotEqual(tasks.FetchImageTask('http://someurl'),
                            tasks.FetchImageTask('http://someurl', 'fake_uuid'))


class InstanceTasksTestCase(testtools.TestCase):
    def test_InstanceTask(self):
        with testtools.ExpectedException(exceptions.NoInstanceTaskException):
            tasks.InstanceTask(None)

        with testtools.ExpectedException(exceptions.NetworkNotListTaskException):
            tasks.InstanceTask('uuid', 'not a list')

    def test_DeleteInstanceTask(self):
        with testtools.ExpectedException(exceptions.NoNextStateTaskException):
            tasks.DeleteInstanceTask('uuid', None)

    def test_FetchImageTask(self):
        with testtools.ExpectedException(exceptions.NoURLImageFetchTaskException):
            tasks.FetchImageTask(None)

        with testtools.ExpectedException(exceptions.NoURLImageFetchTaskException):
            tasks.FetchImageTask(1234)
