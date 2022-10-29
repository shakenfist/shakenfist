from math import inf
import time

from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist.node import (
    Nodes, active_states_filter as node_active_states_filter)


LOG, _ = logutil.setup(__name__)


def get_node_metrics(filters):
    metrics = {}

    for n in Nodes(filters):
        try:
            new_metrics = etcd.get('metrics', n.uuid, None)
            if new_metrics:
                new_metrics = new_metrics.get('metrics', {})
            else:
                n.add_event2('empty metrics from database for node')
                new_metrics = {}
            metrics[n.uuid] = new_metrics

        except exceptions.ReadException:
            n.add_event2('refreshing metrics for node failed')

    return metrics


def get_active_node_metrics():
    return get_node_metrics([node_active_states_filter])


VERSION_CACHE = None
VERSION_CACHE_AGE = 0

# This doesn't use OBJECT_NAME_TO_CLASSES because of circular imports.
OBJECT_NAMES = ['artifact', 'blob', 'instance', 'network', 'networkinterface',
                'node']


def get_minimum_object_version(objname):
    global VERSION_CACHE
    global VERSION_CACHE_AGE

    with etcd.get_lock('get_minimum_object_versions', None, None):
        if not VERSION_CACHE:
            VERSION_CACHE = {}
        elif time.time() - VERSION_CACHE_AGE > 300:
            VERSION_CACHE = {}
        elif objname in VERSION_CACHE:
            return VERSION_CACHE[objname]

        metrics = get_node_metrics([])
        for possible_objname in OBJECT_NAMES:
            minimum = inf
            for entry in metrics:
                ver = metrics[entry].get(
                    'object_version_%s' % possible_objname)
                if ver:
                    minimum = min(minimum, ver)
            VERSION_CACHE[possible_objname] = minimum

        VERSION_CACHE_AGE = time.time()
        return VERSION_CACHE[objname]
