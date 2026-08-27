# Copyright 2019 Michael Still and contributors
"""Sample cluster resources on an interval, for CI headroom instrumentation.

The functional test suite creates and deletes instances continuously, but
nothing today records how close to the scheduler's admission limits those
runs actually get. `GET /admin/resources` (via `client.get_cluster_resources()`)
is the same data the scheduler uses to admit or refuse an instance, so
sampling it repeatedly through a test run builds a time series of headroom
that a later tool can turn into a p90/peak report -- see
docs/plans/PLAN-ci-cloud-sizing-phase-01-headroom-probe.md.

This is a background poller, not a single-shot check like
tools/ci_wait_schedulable.py (which this tool is modelled on for its
credential handling and client construction). It appends one JSON object per
line to the output file until --max-seconds elapses, then exits.

Each sample carries the *entire* `/admin/resources` payload verbatim, plus
the node roster from `GET /nodes` (`client.get_nodes()`), reduced to uuid,
fqdn and the three role booleans (is_hypervisor, is_network_node,
is_database_node). Recording the roster on every sample, rather than once
at the start, is deliberate: `summarize_resources()` silently omits from
`per_node` any node that is not a hypervisor, whose metrics are older than
120 seconds, or whose queue exceeds UNREASONABLE_QUEUE_LENGTH. A node
missing from a given sample's `per_node` therefore has four possible
explanations -- not a hypervisor, stale metrics, an overlong queue, or (if
it is also missing from that sample's roster) the node did not exist yet --
and only a roster recorded alongside that same sample lets a later reader
tell them apart. A roster fetched once at the top of the run cannot do
that, because cluster membership itself can change mid-run.

--max-seconds is required, with no default, because the CI workflow that
runs this tool sets `cancel-in-progress: true`. A cancelled job runs no
further workflow steps, so the step that would stop this poller is never
guaranteed to execute, and nothing else tears the underlying cluster down
either -- the under-cloud reaper collects it later, on its own schedule. A
poller with no cap of its own would keep polling a leaked VM indefinitely.

The sampling loop never raises: a failed sample (a network error, an
unexpected response shape, anything) is written as a record carrying an
`error` key and the timestamp, and the loop continues to the next interval.
The probe exists to give a later analysis step data to work with, so it
must not itself be the reason a CI run ends up with none.

Run it with /etc/sf/sfrc sourced, using the SF venv python so
shakenfist_client is importable, for example:

    . /etc/sf/sfrc
    /srv/shakenfist/venv/bin/python3 tools/ci_headroom_probe.py \\
        --interval 15 --max-seconds 2700 /srv/ci/traces/headroom.jsonl

Each output line is a JSON object with these keys:

    sampled_at      float, unix epoch seconds, wall clock at sample start.
    resources       the verbatim return value of client.get_cluster_resources()
                     (i.e. the full /admin/resources payload), absent on error.
    nodes           list of {'uuid', 'fqdn', 'is_hypervisor', 'is_network_node',
                     'is_database_node'} dicts, one per node in the current
                     roster, absent on error.
    error           string, the exception text, present only when the sample
                     failed. When present, 'resources' and 'nodes' are absent.
"""
import argparse
import json
import os
import sys
import time

from shakenfist_client import apiclient


def take_sample(client):
    """Fetch one sample of cluster resources and the node roster.

    Returns a JSON-serialisable dict. Never raises -- any failure is
    captured as an 'error' key so the caller can write it and move on.
    """
    sampled_at = time.time()
    try:
        resources = client.get_cluster_resources()
        nodes = [
            {
                'uuid': node.get('uuid'),
                'fqdn': node.get('fqdn'),
                'is_hypervisor': node.get('is_hypervisor'),
                'is_network_node': node.get('is_network_node'),
                'is_database_node': node.get('is_database_node'),
            }
            for node in client.get_nodes()
        ]
        return {
            'sampled_at': sampled_at,
            'resources': resources,
            'nodes': nodes,
        }
    except Exception as e:
        return {
            'sampled_at': sampled_at,
            'error': str(e),
        }


def main():
    description = (
        'Poll /admin/resources and the node roster on an interval, appending one JSON record per '
        'sample to a JSONL file, for CI headroom instrumentation.')
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        'output', help='Path to the JSONL file to append samples to.')
    parser.add_argument(
        '--interval', type=float, default=15,
        help='Seconds to sleep between samples (default: 15).')
    parser.add_argument(
        '--max-seconds', type=float, required=True,
        help='Stop sampling once this many seconds have elapsed since start. Required: a cancelled '
             'CI job never runs the step that would otherwise stop this poller, so it must carry '
             'its own cap or it spins forever against a leaked cluster.')
    args = parser.parse_args()

    client = apiclient.Client(
        namespace=os.environ.get('SHAKENFIST_NAMESPACE'),
        key=os.environ.get('SHAKENFIST_KEY'),
        base_url=os.environ.get('SHAKENFIST_API_URL', 'http://localhost:13000'))

    start_time = time.time()
    with open(args.output, 'a') as f:
        while time.time() - start_time < args.max_seconds:
            record = take_sample(client)
            f.write(json.dumps(record) + '\n')
            f.flush()
            os.fsync(f.fileno())
            time.sleep(args.interval)

    return 0


if __name__ == '__main__':
    sys.exit(main())
