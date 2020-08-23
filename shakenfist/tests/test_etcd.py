import mock
import testtools

from shakenfist import etcd


class ActualLockTestCase(testtools.TestCase):
    def test_init(self):
        lock = etcd.ActualLock(name='testname', ttl=59, timeout=123)
        self.assertEqual(lock.timeout, 123)

    @mock.patch('etcd3.Lock.acquire', return_value=True)
    @mock.patch('etcd3.Lock.__exit__')
    def test_context(self, mock_exit, mock_acquire):
        with etcd.ActualLock(name='testname') as testlock:
            self.assertTrue(testlock.is_acquired())

    @mock.patch('etcd3.Lock.acquire', return_value=False)
    @mock.patch('etcd3.Lock.__exit__')  # Avoids confounding error on test fail
    def test_context_exception(self, mock_exit, mock_acquire):
        with testtools.ExpectedException(etcd.LockException):
            with etcd.ActualLock(name='testname'):
                pass

    def test_get_lock(self):
        lock = etcd.get_lock('testtype', 'testsubtype', 'testname')
        self.assertTrue(isinstance(lock, etcd.ActualLock))
