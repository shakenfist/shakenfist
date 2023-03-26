from shakenfist_utilities import logs

from shakenfist import etcd
from shakenfist.eventlog import EVENT_TYPE_AUDIT
from shakenfist import exceptions
from shakenfist.node import (
    Nodes, active_states_filter as node_active_states_filter)


LOG, _ = logs.setup(__name__)


def get_node_metrics(filters):
    metrics = {}

    for n in Nodes(filters):
        try:
            new_metrics = etcd.get('metrics', n.uuid, None)
            if new_metrics:
                new_metrics = new_metrics.get('metrics', {})
            else:
                n.add_event(EVENT_TYPE_AUDIT, 'empty metrics from database for node')
                new_metrics = {}
            metrics[n.uuid] = new_metrics

        except exceptions.ReadException:
            n.add_event(EVENT_TYPE_AUDIT, 'refreshing metrics for node failed')

    return metrics


def get_active_node_metrics():
    return get_node_metrics([node_active_states_filter])
