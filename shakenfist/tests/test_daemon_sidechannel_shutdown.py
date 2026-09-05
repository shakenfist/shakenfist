# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.daemons.sidechannel import main as sidechannel
from shakenfist.tests import base


class _FakeJobObject:
    def __init__(self, abort_path):
        self.abort_path = abort_path


class _FakeThread:
    """Stands in for a monitor or executor thread.

    alive_answers is consumed one entry per is_alive() call, with the
    last entry repeated forever, so a test can model a thread which is
    still running on the first shutdown pass and gone on the next.

    ident mirrors threading.Thread.ident: None until the thread has
    actually been started, and an integer afterwards. It defaults to
    started, since that is what every record here but one models.
    """

    def __init__(self, alive_answers, ident=1):
        self.alive_answers = list(alive_answers)
        self.ident = ident
        self.join_calls = []

    def is_alive(self):
        if len(self.alive_answers) > 1:
            return self.alive_answers.pop(0)
        return self.alive_answers[0]

    def join(self, timeout=None):
        self.join_calls.append(timeout)


def _thread_record(instance_uuid, alive_answers, ident=1):
    return {
        'object': _FakeJobObject(f'/no/such/abort-{instance_uuid}'),
        'thread': _FakeThread(alive_answers, ident=ident),
        'instance_uuid': instance_uuid
    }


class SidechannelShutdownTestCase(base.ShakenFistTestCase):
    """Shutdown must tear down executors as executors (issue #3931).

    _request_thread_exit() used to read and mutate self.monitors no
    matter which record it was passed, so an executor with no
    corresponding monitor raised KeyError out of the shutdown sequence,
    executors were never removed, and a live monitor could be deleted
    because an unrelated executor thread had exited.
    """

    def setUp(self):
        super().setUp()
        for target in ('set_abort_path', 'clear_abort_path'):
            p = mock.patch.object(sidechannel.daemon, target)
            setattr(self, target, p.start())
            self.addCleanup(p.stop)

        p = mock.patch.object(sidechannel, 'add_event')
        self.add_event = p.start()
        self.addCleanup(p.stop)

    def _make_daemon(self):
        m = sidechannel.Monitor.__new__(sidechannel.Monitor)
        m.monitors = {}
        m.executors = {}
        return m

    def test_executor_without_monitor_does_not_keyerror(self):
        # The common shutdown path: the monitor loop above has already
        # drained self.monitors, and only the executor remains. This
        # raised KeyError before the fix.
        m = self._make_daemon()
        m.executors['i1'] = _thread_record('i1', [False])

        m._request_all_threads_exit()

        self.assertEqual({}, m.executors)

    def test_executor_shutdown_joins_the_executor_thread(self):
        m = self._make_daemon()
        m.monitors['i1'] = _thread_record('i1', [True])
        m.executors['i1'] = _thread_record('i1', [False])
        executor_thread = m.executors['i1']['thread']

        m._request_thread_exit('i1', m.executors, 'executor')

        self.assertEqual([0.5], executor_thread.join_calls)
        self.assertEqual([], m.monitors['i1']['thread'].join_calls)

    def test_executor_shutdown_leaves_the_monitor_alone(self):
        # The is_alive() test used to read the executor's thread but
        # delete the monitor, so a live monitor could be dropped because
        # an unrelated executor thread had already exited.
        m = self._make_daemon()
        m.monitors['i1'] = _thread_record('i1', [True])
        m.executors['i1'] = _thread_record('i1', [False])

        m._request_thread_exit('i1', m.executors, 'executor')

        self.assertIn('i1', m.monitors)
        self.assertNotIn('i1', m.executors)

    def test_live_executor_entry_is_retained(self):
        m = self._make_daemon()
        m.executors['i1'] = _thread_record('i1', [True])

        m._request_thread_exit('i1', m.executors, 'executor')

        self.assertIn('i1', m.executors)
        self.set_abort_path.assert_called_once_with(
            '/no/such/abort-i1', 'from _request_thread_exit')
        self.clear_abort_path.assert_not_called()

    def test_audit_events_name_the_thread_type(self):
        m = self._make_daemon()
        m.monitors['i1'] = _thread_record('i1', [False])
        m.executors['i2'] = _thread_record('i2', [False])

        m._request_all_threads_exit()

        messages = [c.args[3] for c in self.add_event.call_args_list]
        self.assertEqual(
            ['side channel monitor instructed to exit',
             'side channel monitor finished',
             'side channel executor instructed to exit',
             'side channel executor finished'],
            messages)

    def test_an_unstarted_thread_is_signalled_but_not_joined(self):
        # start_instance_executor() registers an executor before it
        # starts the thread, so a SIGTERM can arrive while the entry
        # exists and the thread does not. join() raises RuntimeError on
        # such a thread, which would abort the whole shutdown sequence
        # and leave every remaining thread unsignalled.
        m = self._make_daemon()
        m.executors['i1'] = _thread_record('i1', [False], ident=None)
        m.executors['i2'] = _thread_record('i2', [False])

        m._request_all_threads_exit()

        # The unstarted thread was told to stop and left in place for a
        # later pass to collect, and the started one behind it was still
        # reached.
        self.assertEqual([], m.executors['i1']['thread'].join_calls)
        self.assertIn('i1', m.executors)
        self.assertNotIn('i2', m.executors)
        self.assertIn(
            mock.call('/no/such/abort-i1', 'from _request_thread_exit'),
            self.set_abort_path.call_args_list)

    def test_a_missing_record_is_a_noop(self):
        # The monitor loop can delete an entry between the snapshot and
        # the call, so a vanished record must not raise.
        m = self._make_daemon()
        m._request_thread_exit('i1', m.monitors, 'monitor')
        self.set_abort_path.assert_not_called()

    def test_shutdown_waits_for_executors(self):
        # The drain loop used to be `while self.monitors:`, so it
        # terminated once the monitors were gone and never waited for
        # executors at all. This executor needs two passes to exit.
        m = self._make_daemon()
        m.executors['i1'] = _thread_record('i1', [True, False])

        with mock.patch.object(sidechannel.time, 'sleep') as sleep:
            m._wait_for_all_threads_exit()

        self.assertEqual({}, m.executors)
        sleep.assert_called_once_with(5)
