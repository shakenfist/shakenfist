# Copyright 2019 Michael Still and contributors
"""The headroom probe's contract is that it always produces a readable line.

``tools/ci_headroom_probe.py`` runs unattended for the length of a
functional job, against a cluster the job is actively hammering, and its
output is the only input phase 2 has. Everything below is about the
promise the module docstring makes -- that neither a failed sample nor a
failed write ends the series -- because ``tools/ci_headroom_report.py``
is written against exactly that record shape, and a poller which dies
silently mid-run produces a short series which looks like a quiet one.

Three properties are covered:

* A failed sample is a record with ``error`` and *without* ``resources``
  or ``nodes``. The report distinguishes an errored sample from an empty
  one, and would count a record carrying ``resources: null`` as a
  successful sample of nothing.
* The roster is reduced to exactly five keys. ``ci.md`` documents that
  reduction as load-bearing for phase 2, which needs the role booleans to
  tell the four reasons a node can be missing from ``per_node`` apart.
* The write is guarded on the same terms as the sample. A value the json
  module cannot serialise degrades to an error line rather than to a
  dead poller.

The tool is loaded by path, as ``test_ci_claims_headroom.py`` does,
because ``tools/`` is not an importable package. It imports
``shakenfist_client`` at module scope and that is not a test dependency
of this repository, so a stub is installed in ``sys.modules`` first --
the probe only ever calls two methods on it, both of which are replaced
here anyway.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types

from shakenfist.tests import base


PROBE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'tools', 'ci_headroom_probe.py')


def _load_probe():
    stub = types.ModuleType('shakenfist_client')
    stub.apiclient = types.ModuleType('shakenfist_client.apiclient')
    stub.apiclient.Client = object
    saved = {name: sys.modules.get(name)
             for name in ('shakenfist_client', 'shakenfist_client.apiclient')}
    sys.modules['shakenfist_client'] = stub
    sys.modules['shakenfist_client.apiclient'] = stub.apiclient
    try:
        spec = importlib.util.spec_from_file_location(
            'ci_headroom_probe_under_test', PROBE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in saved.items():
            if value is None:
                del sys.modules[name]
            else:
                sys.modules[name] = value


probe = _load_probe()


class FakeClient:
    """A client which answers, or fails, exactly as scripted."""

    def __init__(self, resources=None, nodes=None, raises=None):
        self.resources = resources if resources is not None else {'per_node': {}}
        self.nodes = nodes if nodes is not None else []
        self.raises = raises

    def get_cluster_resources(self):
        if self.raises:
            raise self.raises
        return self.resources

    def get_nodes(self):
        if self.raises:
            raise self.raises
        return self.nodes


class FailingFile:
    def __init__(self, exc):
        self.exc = exc

    def write(self, _data):
        raise self.exc

    def flush(self):
        pass

    def fileno(self):
        raise self.exc


class TakeSampleTestCase(base.ShakenFistTestCase):
    def test_a_good_sample_carries_the_payload_verbatim(self):
        resources = {'total': {'cpu_available': 4}, 'per_node': {'a': {}}}
        client = FakeClient(resources=resources, nodes=[])
        record = probe.take_sample(client)
        self.assertEqual(resources, record['resources'])
        self.assertNotIn('error', record)
        self.assertIsInstance(record['sampled_at'], float)

    def test_a_failed_sample_omits_resources_rather_than_nulling_it(self):
        """The report tells an errored sample from a successful empty one.

        A record carrying resources: null would be read as a sample which
        saw a cluster with no nodes, which is a finding about the
        cluster. An absent key is a finding about the probe.
        """
        record = probe.take_sample(FakeClient(raises=OSError('connection refused')))
        self.assertIn('error', record)
        self.assertIn('connection refused', record['error'])
        self.assertNotIn(
            'resources', record,
            'A failed sample carried a resources key. The report reads '
            'that as a successful sample of an empty cluster.')
        self.assertNotIn('nodes', record)

    def test_take_sample_never_raises(self):
        """Whatever the client does, the loop must survive it.

        Not just network errors: an unexpected response shape reaches
        this code as a TypeError or AttributeError from inside the
        comprehension, and the probe must write that down and continue
        rather than end the run.
        """
        for failure in (OSError('down'), ValueError('bad json'),
                        KeyError('missing'), RuntimeError('boom')):
            record = probe.take_sample(FakeClient(raises=failure))
            self.assertIn('error', record)

    def test_a_roster_entry_is_reduced_to_the_five_documented_keys(self):
        """ci.md calls this reduction load-bearing for phase 2."""
        client = FakeClient(nodes=[{
            'uuid': 'u1', 'fqdn': 'node1.local', 'is_hypervisor': True,
            'is_network_node': False, 'is_database_node': True,
            'state': 'created', 'ip': '10.0.0.1', 'release': '0.8',
        }])
        record = probe.take_sample(client)
        self.assertEqual(
            {'uuid', 'fqdn', 'is_hypervisor', 'is_network_node',
             'is_database_node'},
            set(record['nodes'][0].keys()))
        self.assertTrue(record['nodes'][0]['is_database_node'])

    def test_a_roster_entry_missing_keys_reads_as_none_not_an_error(self):
        """An older node record must not cost the whole sample.

        The role booleans become None, which print_absences already
        treats as 'unexplained' rather than as 'not a hypervisor' -- the
        distinction the tri-state exists for.
        """
        record = probe.take_sample(FakeClient(nodes=[{'uuid': 'u1'}]))
        self.assertNotIn('error', record)
        self.assertIsNone(record['nodes'][0]['is_hypervisor'])
        self.assertIsNone(record['nodes'][0]['fqdn'])


class WriteRecordTestCase(base.ShakenFistTestCase):
    """Written against real files: write_record fsyncs, so a fake fd will not do."""

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.path = os.path.join(self.tempdir, 'headroom.jsonl')

    def _write(self, record):
        with open(self.path, 'a') as f:
            written = probe.write_record(f, record)
        with open(self.path) as f:
            return written, f.read()

    def test_a_good_record_is_written_as_one_json_line(self):
        written, body = self._write({'sampled_at': 1.0})
        self.assertTrue(written)
        self.assertEqual({'sampled_at': 1.0}, json.loads(body.strip()))
        self.assertTrue(body.endswith('\n'),
                        'Records must be newline terminated or the next '
                        'sample continues this one and both are lost.')

    def test_an_unserialisable_record_degrades_to_an_error_line(self):
        """A poller which dies on one bad value loses every later sample."""
        written, body = self._write({'sampled_at': 2.0, 'resources': object()})
        self.assertTrue(written)
        record = json.loads(body.strip())
        self.assertEqual(2.0, record['sampled_at'])
        self.assertIn(
            'could not be serialised', record['error'],
            'An unserialisable sample should be written down as an error '
            'record, so the gap in the series says why it is there.')
        self.assertNotIn('resources', record)

    def test_a_failing_write_returns_false_rather_than_raising(self):
        """A full /srv costs one line, not the rest of the run."""
        self.assertFalse(
            probe.write_record(FailingFile(OSError('No space left on device')),
                               {'sampled_at': 3.0}))
