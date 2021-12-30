import json
import time

from shakenfist.config import config
from shakenfist import constants
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist.tasks import EventLogMessageTask, EventLogDeleteTask
from shakenfist.util import general as util_general


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.WorkerPoolDaemon):
    def _process_eventlog_node_workitems(self):
        while True:
            jobname, workitem = etcd.dequeue('eventlog')
            if not workitem:
                time.sleep(0.2)
            else:
                try:
                    if EventLogMessageTask.__subclasscheck__(type(workitem)):
                        extra = workitem.extra()
                        for objtype in constants.OBJECT_NAMES:
                            if objtype in extra:
                                with eventlog.EventLog(objtype, extra[objtype]) as eventdb:
                                    eventdb.write_event(
                                        workitem.timestamp(), workitem.message(),
                                        json.dumps(workitem.extra(), indent=4,
                                                   sort_keys=True))

                    elif EventLogDeleteTask.__subclasscheck__(type(workitem)):
                        with eventlog.EventLog(objtype, extra[objtype]) as eventdb:
                            eventdb.delete()
                    else:
                        raise exceptions.UnknownTaskException(
                            'Eventlog workitem was not decoded: %s' % workitem)

                finally:
                    etcd.resolve('eventlog', jobname)

    def run(self):
        LOG.info('Starting')

        eventlog_worker = None

        while True:
            try:
                self.reap_workers()
                worker_pids = []
                for w in self.workers:
                    worker_pids.append(w.pid)

                if config.NODE_IS_EVENTLOG_NODE and eventlog_worker not in worker_pids:
                    eventlog_worker = self.start_workitem(
                        self._process_eventlog_node_workitems, [], 'eventlog-worker')

            except Exception as e:
                util_general.ignore_exception('eventlog worker', e)
