# Copyright 2019 Michael Still and contributors
"""Check whether the cluster can schedule an instance, for CI readiness gating.

The functional test deploy waits for the REST API to answer before running
tests, but a node is not actually schedulable until it is active *and* the
resources daemon has written fresh node metrics that the scheduler reads (see
shakenfist/scheduler.py:get_active_node_metrics). At cold start the API answers
several seconds before that first metrics write, so a test that creates an
instance immediately can hit a spurious 507 "No nodes remaining at scheduling
stage is_hypervisor".

This is a single-shot check: it exits 0 if every hypervisor node has logged at
least one 'resources' event (the resources daemon emits one in the same cycle
as the node-metrics write), and exits non-zero otherwise. The caller is
expected to retry it in a loop. Run it with /etc/sf/sfrc sourced, using the SF
venv python so shakenfist_client is importable, for example:

    . /etc/sf/sfrc; /srv/shakenfist/venv/bin/python3 - < tools/ci_wait_schedulable.py
"""
import os
import sys

from shakenfist_client import apiclient


def main():
    try:
        client = apiclient.Client(
            namespace=os.environ.get('SHAKENFIST_NAMESPACE'),
            key=os.environ.get('SHAKENFIST_KEY'),
            base_url=os.environ.get('SHAKENFIST_API_URL', 'http://localhost:13000'))

        hypervisors = [n for n in client.get_nodes() if n.get('is_hypervisor')]
        if not hypervisors:
            print('No hypervisor nodes are registered yet.')
            return 1

        waiting = []
        for node in hypervisors:
            events = client.get_node_events(
                node['name'], event_type='resources', limit=1)
            if not events:
                waiting.append(node['name'])

        if waiting:
            print('Hypervisors yet to report resources: %s.' % ', '.join(sorted(waiting)))
            return 1

    except Exception as e:
        print('Cluster is not answering as expected yet: %s' % e)
        return 1

    print('All %d hypervisor(s) have reported resources; the cluster is schedulable.'
          % len(hypervisors))
    return 0


if __name__ == '__main__':
    sys.exit(main())
