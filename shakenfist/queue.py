from collections import defaultdict
import etcd3
import json
import math
import threading
import time

from shakenfist.config import config
from shakenfist import etcd
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


class Queue(object):
    def __init__(self, name):
        self.name = name
        self.queue_path = etcd.construct_key('queue', name, None)

        self.lock = threading.Lock()
        self.deadlines = defaultdict(list)
        self.workitems = {}

    def __enter__(self):
        # Grab all the currently queued work
        self._collect_workitems()

        # Start watching for new work
        self.client = etcd3.client(host=config.ETCD_HOST)
        self.watch_id = self.client.add_watch_prefix_callback(
            self.queue_path, self._watch_workitems)

        return self

    def __exit__(self, *args):
        with self.lock:
            self.client.cancel_watch(self.watch_id)
            self.deadlines = defaultdict(list)
            self.workitems = {}

    def _collect_workitems(self):
        with self.lock:
            for data, metadata in etcd.WrappedEtcdClient().get_prefix(
                    self.queue_path, sort_order='ascend', sort_target='key'):
                jobname = str(metadata['key']).split('/')[-1].rstrip("'")
                deadline = float(jobname.split('-')[0])
                workitem = json.loads(data, object_hook=etcd.decodeTasks)
                self.workitems[jobname] = workitem
                self.deadlines[deadline].append(jobname)

    def _watch_workitems(self, watchresponse):
        for event in watchresponse.events:
            if isinstance(event, etcd3.events.PutEvent):
                with self.lock:
                    jobname = str(event.key).split('/')[-1].rstrip("'")
                    deadline = float(jobname.split('-')[0])
                    workitem = json.loads(
                        event.value, object_hook=etcd.decodeTasks)
                    self.workitems[jobname] = workitem
                    self.deadlines[deadline].append(jobname)
                    LOG.info('Discovered work item %s' % jobname)

            elif isinstance(event, etcd3.events.DeleteEvent):
                with self.lock:
                    jobname = str(event.key).split('/')[-1].rstrip("'")
                    deadline = float(jobname.split('-')[0])
                    if (deadline in self.deadlines and
                            jobname in self.deadlines[deadline]):
                        self.deadlines[deadline].remove(jobname)
                    if jobname in self.workitems:
                        del self.workitems[jobname]
                    LOG.info('Removed work item %s' % jobname)

    def get_workitems(self, max=math.inf):
        if len(self.deadlines) == 0:
            return

        count = 0
        with self.lock:
            oldest = sorted(self.deadlines)[0]
            while time.time() > oldest:
                for jobname in self.deadlines.get(oldest, []):
                    yield (jobname, self.workitems[jobname])
                    del self.workitems[jobname]
                    self.deadlines[oldest].remove(jobname)

                    count += 1
                    if count >= max:
                        return

                del self.deadlines[oldest]
                if len(self.deadlines) == 0:
                    return
                oldest = sorted(self.deadlines)[0]

    def mark_workitem_as_processing(self, jobname, workitem):
        etcd.put('processing', self.name, jobname, workitem)
        etcd.delete('queue', self.name, jobname)
        LOG.with_fields({'jobname': jobname,
                         'queuename': self.name,
                         'workitem': workitem,
                         }).info('Moved workitem from queue to processing')

    def mark_workitem_as_completed(self, jobname, workitem):
        etcd.delete('processing', self.name, jobname)
        LOG.with_fields({'jobname': jobname,
                         'queuename': self.name,
                         'workitem': workitem,
                         }).info('Moved workitem from processing to completed')
