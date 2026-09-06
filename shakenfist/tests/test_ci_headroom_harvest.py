# Copyright 2019 Michael Still and contributors
"""The harvest must never quietly produce a smaller window than it claims.

``tools/ci_headroom_harvest.py`` turns the banked CI bundles from many merge
runs into the dataset phase 2's baseline is argued from, and phases 4 and 5
argue from after that. Almost every way it can be wrong makes the output
look *better* rather than obviously broken: a job dropped because its
artifact name was not recognised, a bundle recorded as having no probe
because the nested zip was not opened, a run's traces read from the previous
bundle's leftovers. None of those raise, and none of them are visible in the
resulting file.

So the coverage here is deliberately weighted towards the silent failures:

* An unrecognised **bundle** raises ``UnknownBundleError`` by name. A new row
  in the merge matrix must stop the harvest so a human says which topology it
  is, because the alternatives are a fabricated label on a real measurement
  or a job missing from the dataset with nothing to say it is missing.
* The two known-uninstrumented bundles are skipped *by name*, and skipping
  them is not the same code path as failing on an unknown one -- a test pins
  that they produce no record and no exception.
* The bundle is a nested zip. The traces live inside ``bundle.zip`` inside
  the artifact zip, and a tool which looked only at the outer namelist would
  report every run in the window as having no probe.
* A bundle with no series is recorded with a reason, not dropped, because the
  n step 2d states has to include it.
* Two bundles in the same run must not read each other's trace files.
* The output is compact JSONL (D22), because the record is 3.7 KB rather than
  the few hundred bytes the plan originally guessed.

GitHub is faked throughout: the real thing is ``gh`` on a subprocess, and a
test which shelled out would need network, credentials and a live window.
The fake implements the three calls the tool makes -- the run listing, a
run's artifacts, and a run's jobs -- plus the artifact download, which copies
a fixture zip built in setUp.

The tool is loaded by path for the same reason its own tests load the report
that way: ``tools/`` is not a package.
"""

import importlib.util
import io
import json
import os
import shutil
import tempfile
import zipfile

from shakenfist.tests import base


TOOLS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'tools')
HARVEST_PATH = os.path.join(TOOLS, 'ci_headroom_harvest.py')
REPORT_PATH = os.path.join(TOOLS, 'ci_headroom_report.py')

NODE_ONE = '11111111-1111-1111-1111-111111111111'
NODE_TWO = '22222222-2222-2222-2222-222222222222'

PRIMARY_BUNDLE = 'bundle-shakenfist-full-debian-12-slim-primary'
TIER_BUNDLE = 'bundle-shakenfist-full-debian-12-slim-tier'
ANSIBLE_BUNDLE = 'bundle-shakenfist-full-ansible-modules'
LIFECYCLE_BUNDLE = 'bundle-functional-node-lifecycle-collection'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harvest = _load('ci_headroom_harvest_under_test', HARVEST_PATH)
report = _load('ci_headroom_report_for_harvest', REPORT_PATH)


def node_payload(cpu_committed=6, cpu_limit=10):
    """One node's slice of a /admin/resources per_node payload."""
    return {
        'cpu_max_per_instance': 4,
        'cpu_schedulable': 8,
        'cpu_committed_row_present': True,
        'cpu_hard_max': 12,
        'cpu_measured': 4,
        'cpu_committed': cpu_committed,
        'cpu_limit': cpu_limit,
        'cpu_available': 12 - cpu_committed,
        'cpu_load_1': 1.0,
        'cpu_load_5': 1.0,
        'cpu_load_15': 1.0,
        'memory_reserved_mb': 2048,
        'ram_max_per_instance': 12000,
        'ram_max': 32000,
        'ram_available': 24000,
        'disk_available': 100,
        'instances_total': 2,
        'instances_active': 1,
    }


def series_body(samples=3, cpu_committed=6):
    """A tiny but real JSONL series, of the shape the probe writes."""
    lines = []
    for index in range(samples):
        per_node = {
            NODE_ONE: node_payload(cpu_committed=cpu_committed),
            NODE_TWO: node_payload(cpu_committed=cpu_committed),
        }
        lines.append(json.dumps({
            'sampled_at': 1756000000.0 + (index * 15),
            'resources': {
                'total': {
                    'cpu_available': sum(
                        n['cpu_available'] for n in per_node.values()),
                    'ram_available': sum(
                        n['ram_available'] for n in per_node.values()),
                },
                'per_node': per_node,
            },
            'nodes': [
                {'uuid': NODE_ONE, 'fqdn': 'sf1', 'is_hypervisor': True,
                 'is_network_node': True, 'is_database_node': True},
                {'uuid': NODE_TWO, 'fqdn': 'sf2', 'is_hypervisor': True,
                 'is_network_node': False, 'is_database_node': False},
            ],
        }))
    return '\n'.join(lines) + '\n'


def census_body():
    """A Loki query_range response with one stage event in it."""
    event = json.dumps({
        'message': 'schedule at stage sufficient_idle_cpu',
        'extra': {'candidates': ['x']},
    })
    return json.dumps({
        'status': 'success',
        'data': {
            'resultType': 'streams',
            'result': [{
                'stream': {'job': 'shakenfist'},
                'values': [['1756000000000000000', event]],
            }],
        },
    })


def make_bundle(path, members, nested=True):
    """Write a bundle artifact zip.

    ``members`` maps a path relative to the archive root to its body. With
    ``nested`` true -- which is what GitHub actually hands back -- the whole
    thing is wrapped in an outer zip holding a single ``bundle.zip``.
    """
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as f:
        for name, body in members.items():
            f.writestr(name, body)
    if not nested:
        with open(path, 'wb') as f:
            f.write(inner.getvalue())
        return path
    with zipfile.ZipFile(path, 'w') as outer:
        outer.writestr('bundle.zip', inner.getvalue())
    return path


def instrumented_members(label=None):
    members = {
        'bundle/traces/headroom.jsonl': series_body(),
        'bundle/traces/headroom-census.json': census_body(),
        'bundle/logs/syslog': 'unrelated\n',
    }
    if label is not None:
        members['bundle/traces/headroom-label'] = label + '\n'
    return members


class FakeGitHub:
    """The three listings and one download the tool asks gh for.

    Keyed the same way the real API is: a run listing, then artifacts and
    jobs per run. Anything the tool asks for which was not seeded raises,
    rather than returning an empty list, so a test cannot pass because the
    tool asked the wrong question.
    """

    def __init__(self, runs, artifacts, jobs, zips, repo='shakenfist/shakenfist'):
        self.repo = repo
        self.calls = 0
        self.verbose = False
        self.runs = runs
        self.artifacts = artifacts
        self.jobs = jobs
        self.zips = zips
        self.downloaded = []

    def json(self, path):
        self.calls += 1
        raise AssertionError('unexpected direct json() call for %s' % path)

    def paginate(self, path, key, per_page=100, pages=None):
        self.calls += 1
        if path.startswith('actions/workflows/'):
            yield list(self.runs)
            return
        run_id = int(path.split('/')[2])
        if key == 'artifacts':
            yield list(self.artifacts[run_id])
            return
        if key == 'jobs':
            yield list(self.jobs[run_id])
            return
        raise AssertionError('unexpected listing %s/%s' % (path, key))

    def download(self, path, dest):
        self.calls += 1
        artifact_id = int(path.split('/')[2])
        self.downloaded.append(artifact_id)
        shutil.copyfile(self.zips[artifact_id], dest)
        return dest


class Args:
    """Just enough of an argparse namespace for harvest()."""

    def __init__(self, output, cache_dir, **kwargs):
        self.output = output
        self.cache_dir = cache_dir
        self.workflow = 'functional-tests.yml'
        self.since = None
        self.limit = None
        self.census_limit = report.DEFAULT_CENSUS_LIMIT
        self.quiet = True
        for key, value in kwargs.items():
            setattr(self, key, value)


class HarvestTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.cache = os.path.join(self.tempdir, 'cache')
        self.output = os.path.join(self.tempdir, 'harvest.jsonl')

    def _zip(self, name, members, nested=True):
        return make_bundle(
            os.path.join(self.tempdir, name + '.zip'), members, nested=nested)

    def _run_payload(self, run_id=1000, conclusion='success'):
        return {
            'id': run_id,
            'run_attempt': 1,
            'html_url': 'https://github.com/shakenfist/shakenfist/actions/runs/%d' % run_id,
            'head_sha': 'a' * 40,
            'created_at': '2026-09-01T00:00:00Z',
            'conclusion': conclusion,
        }

    def _harvest(self, github, **kwargs):
        args = Args(self.output, self.cache, **kwargs)
        count = harvest.harvest(github, args, report)
        with open(self.output) as f:
            lines = [line for line in f.read().split('\n') if line]
        return count, lines


class ClassificationTestCase(HarvestTestCase):
    def test_an_unknown_bundle_fails_loudly(self):
        # The whole point of the table. A new merge matrix row must stop the
        # harvest, because a guessed topology is a fabricated label on a real
        # measurement and a silent skip is a job missing from the dataset
        # with nothing to say so.
        self.assertRaises(
            harvest.UnknownBundleError, harvest.classify_artifact,
            'bundle-shakenfist-full-debian-13-fat-primary')

    def test_the_unknown_bundle_error_names_the_bundle(self):
        try:
            harvest.classify_artifact('bundle-shakenfist-full-something-new')
        except harvest.UnknownBundleError as e:
            self.assertIn('bundle-shakenfist-full-something-new', str(e))
            self.assertIn('BUNDLE_TOPOLOGIES', str(e))
        else:
            self.fail('an unrecognised bundle did not raise')

    def test_the_uninstrumented_bundles_are_skipped_not_raised(self):
        # Skipping these is a different code path from failing on an unknown
        # bundle, and it has to stay that way: they are known to carry no
        # traces/ directory, for two different reasons, and treating them as
        # missing data would put eight bogus probe-absent records in every
        # window.
        for name in (ANSIBLE_BUNDLE, LIFECYCLE_BUNDLE):
            action, reason = harvest.classify_artifact(name)
            self.assertEqual('skip', action)
            self.assertTrue(reason)

    def test_a_non_bundle_artifact_is_ignored(self):
        action, _ = harvest.classify_artifact('coverage')
        self.assertEqual('ignore', action)

    def test_the_instrumented_bundles_map_to_their_topologies(self):
        self.assertEqual('slim-primary', harvest.classify_artifact(PRIMARY_BUNDLE)[1].topology)
        self.assertEqual('slim-tier', harvest.classify_artifact(TIER_BUNDLE)[1].topology)
        self.assertEqual(
            'Guests', harvest.classify_artifact('bundle-shakenfist-full-guests')[1].job)


class BundleReadingTestCase(HarvestTestCase):
    def test_the_traces_are_found_inside_the_nested_zip(self):
        # Verified against artifact 9964055153 of merge run 33944911413: the
        # artifact download's namelist is exactly ['bundle.zip']. A tool
        # which read only the outer zip would report every bundle in the
        # window as having no probe.
        path = self._zip('nested', instrumented_members())
        with zipfile.ZipFile(path) as outer:
            self.assertEqual(['bundle.zip'], outer.namelist())
        dest = os.path.join(self.tempdir, 'unpacked')
        os.makedirs(dest)
        found = harvest.extract_traces(path, dest)
        self.assertEqual(
            sorted(['headroom.jsonl', 'headroom-census.json']), sorted(found))

    def test_an_unwrapped_bundle_still_reads(self):
        # Degrading into reading the right file, rather than into "the probe
        # never ran", if the archive step ever stops double-zipping.
        path = self._zip('flat', instrumented_members(), nested=False)
        dest = os.path.join(self.tempdir, 'unpacked-flat')
        os.makedirs(dest)
        found = harvest.extract_traces(path, dest)
        self.assertIn('headroom.jsonl', found)

    def test_a_bundle_without_traces_yields_nothing_rather_than_raising(self):
        path = self._zip('bare', {'bundle/logs/syslog': 'nothing here\n'})
        dest = os.path.join(self.tempdir, 'unpacked-bare')
        os.makedirs(dest)
        self.assertEqual({}, harvest.extract_traces(path, dest))

    def test_the_label_file_is_read_when_present(self):
        path = self._zip('labelled', instrumented_members(label='slim-tier cluster-ci.conf'))
        dest = os.path.join(self.tempdir, 'unpacked-labelled')
        os.makedirs(dest)
        found = harvest.extract_traces(path, dest)
        self.assertEqual(
            'slim-tier cluster-ci.conf', harvest.read_label(found['headroom-label']))
        self.assertEqual(
            'slim-tier', harvest.topology_from_label('slim-tier cluster-ci.conf'))


class RecordTestCase(HarvestTestCase):
    def _github(self, members_by_artifact, jobs=None, run_conclusion='success'):
        run = self._run_payload(conclusion=run_conclusion)
        artifacts = []
        zips = {}
        for index, (name, members) in enumerate(members_by_artifact.items()):
            artifact_id = 9000 + index
            artifacts.append({'id': artifact_id, 'name': name, 'expired': False})
            if members is not None:
                zips[artifact_id] = self._zip(str(artifact_id), members)
        if jobs is None:
            jobs = [
                {'name': 'Debian 12 cluster (collection) / Smoke tests (collection)',
                 'conclusion': 'success'},
                {'name': 'Debian 12 tier (collection) / Smoke tests (collection)',
                 'conclusion': 'failure'},
            ]
        return FakeGitHub([run], {run['id']: artifacts}, {run['id']: jobs}, zips)

    def test_a_full_record_carries_the_run_the_job_and_the_summary(self):
        github = self._github({PRIMARY_BUNDLE: instrumented_members()})
        count, lines = self._harvest(github)
        self.assertEqual(1, count)
        record = json.loads(lines[0])
        self.assertEqual(1000, record['run_id'])
        self.assertEqual('a' * 40, record['head_sha'])
        self.assertEqual('2026-09-01T00:00:00Z', record['run_created_at'])
        self.assertEqual('success', record['run_conclusion'])
        self.assertEqual('Debian 12 cluster', record['job'])
        self.assertEqual(
            'Debian 12 cluster (collection) / Smoke tests (collection)',
            record['github_job_name'])
        self.assertEqual('success', record['job_conclusion'])
        self.assertEqual('slim-primary', record['topology'])
        self.assertEqual('artifact-name-table', record['topology_source'])
        self.assertTrue(record['series_present'])
        self.assertTrue(record['census_present'])
        self.assertIsNone(record['absent_reason'])
        self.assertEqual(report.RECORD_VERSION, record['summary']['record_version'])
        self.assertEqual(3, record['summary']['series']['samples_usable'])

    def test_the_job_conclusion_is_the_jobs_not_the_runs(self):
        # The master plan's central claim is that utilisation explains the
        # pass rate spread, and it is per job. A run's conclusion is the
        # logical AND of six jobs, so recording only that would make every
        # job of a run in which any job failed look like a failure.
        github = self._github({TIER_BUNDLE: instrumented_members()},
                              run_conclusion='failure')
        _, lines = self._harvest(github)
        record = json.loads(lines[0])
        self.assertEqual('failure', record['run_conclusion'])
        self.assertEqual('failure', record['job_conclusion'])
        self.assertEqual('Debian 12 tier', record['job'])

    def test_a_job_which_cannot_be_matched_is_null_not_guessed(self):
        github = self._github({PRIMARY_BUNDLE: instrumented_members()}, jobs=[
            {'name': 'Something else entirely', 'conclusion': 'success'}])
        _, lines = self._harvest(github)
        record = json.loads(lines[0])
        self.assertIsNone(record['github_job_name'])
        self.assertIsNone(record['job_conclusion'])

    def test_a_bundle_with_no_series_is_recorded_not_dropped(self):
        # A run predating phase 1, or one whose probe never started, is part
        # of the window: the n step 2d states has to include it, and a
        # silently dropped record makes the window look both smaller and
        # healthier than it was.
        github = self._github({PRIMARY_BUNDLE: {'bundle/logs/syslog': 'x\n'}})
        count, lines = self._harvest(github)
        self.assertEqual(1, count)
        record = json.loads(lines[0])
        self.assertFalse(record['series_present'])
        self.assertIsNone(record['summary'])
        self.assertIn('headroom.jsonl', record['absent_reason'])
        # The framing is still complete, so the record can be counted.
        self.assertEqual('slim-primary', record['topology'])
        self.assertEqual(1000, record['run_id'])

    def test_an_absent_census_is_not_collected_rather_than_zero(self):
        # D20. The retrospective window's census filter could not match the
        # capacity guard messages, and a record saying zero refusals would be
        # read as a cluster which never refused.
        members = instrumented_members()
        del members['bundle/traces/headroom-census.json']
        github = self._github({PRIMARY_BUNDLE: members})
        _, lines = self._harvest(github)
        record = json.loads(lines[0])
        self.assertFalse(record['census_present'])
        self.assertIsNotNone(record['summary'])
        self.assertFalse(record['summary']['census']['available'])
        self.assertIsNone(record['summary']['census']['stage_events'])

    def test_the_label_file_overrides_the_artifact_name_table(self):
        # D20 writes the topology into the bundle so a later harvest need not
        # infer it. Once it is there it wins, which is what keeps this tool
        # working as topologies are added after it was written.
        members = instrumented_members(label='slim-fat-experiment cluster-ci.conf')
        github = self._github({PRIMARY_BUNDLE: members})
        _, lines = self._harvest(github)
        record = json.loads(lines[0])
        self.assertEqual('slim-fat-experiment', record['topology'])
        self.assertEqual('headroom-label', record['topology_source'])
        # The table's answer is kept beside it, so a disagreement is visible
        # rather than silently resolved.
        self.assertEqual('slim-primary', record['topology_table_says'])
        self.assertEqual('slim-fat-experiment cluster-ci.conf', record['label'])

    def test_an_expired_artifact_is_recorded_as_expired(self):
        run = self._run_payload()
        artifacts = [{'id': 9500, 'name': PRIMARY_BUNDLE, 'expired': True}]
        github = FakeGitHub([run], {run['id']: artifacts}, {run['id']: []}, {})
        count, lines = self._harvest(github)
        self.assertEqual(1, count)
        record = json.loads(lines[0])
        self.assertIn('expired', record['absent_reason'])
        self.assertEqual([], github.downloaded)

    def test_the_uninstrumented_bundles_produce_no_records(self):
        github = self._github({
            PRIMARY_BUNDLE: instrumented_members(),
            ANSIBLE_BUNDLE: {'bundle/logs/syslog': 'x\n'},
            LIFECYCLE_BUNDLE: {'bundle/logs/syslog': 'x\n'},
            'coverage': {'x': 'y'},
        })
        count, lines = self._harvest(github)
        self.assertEqual(1, count)
        self.assertEqual(PRIMARY_BUNDLE, json.loads(lines[0])['artifact_name'])

    def test_an_unknown_bundle_stops_the_whole_harvest(self):
        github = self._github({
            PRIMARY_BUNDLE: instrumented_members(),
            'bundle-shakenfist-full-brand-new-shape': instrumented_members(),
        })
        self.assertRaises(
            harvest.UnknownBundleError, self._harvest, github)

    def test_two_bundles_in_one_run_do_not_read_each_others_traces(self):
        # The three trace files have the same basenames in every bundle. A
        # shared unpack directory would leave the second bundle reading the
        # first's series whenever it was missing one -- which is precisely
        # the case this tool exists to report accurately.
        github = self._github({
            PRIMARY_BUNDLE: instrumented_members(),
            TIER_BUNDLE: {'bundle/logs/syslog': 'x\n'},
        })
        count, lines = self._harvest(github)
        self.assertEqual(2, count)
        by_name = {json.loads(line)['artifact_name']: json.loads(line) for line in lines}
        self.assertTrue(by_name[PRIMARY_BUNDLE]['series_present'])
        self.assertFalse(by_name[TIER_BUNDLE]['series_present'])
        self.assertIsNone(by_name[TIER_BUNDLE]['summary'])


class CacheTestCase(HarvestTestCase):
    def test_a_cached_artifact_is_not_downloaded_twice(self):
        # The full window is roughly 1.3 GB, and step 2d will not get the
        # harvest right on the first attempt.
        run = self._run_payload()
        artifacts = [{'id': 9700, 'name': PRIMARY_BUNDLE, 'expired': False}]
        zips = {9700: self._zip('9700', instrumented_members())}
        jobs = [{'name': 'Debian 12 cluster (collection) / Smoke tests (collection)',
                 'conclusion': 'success'}]

        first = FakeGitHub([run], {run['id']: artifacts}, {run['id']: jobs}, zips)
        self._harvest(first)
        self.assertEqual([9700], first.downloaded)

        second = FakeGitHub([run], {run['id']: artifacts}, {run['id']: jobs}, zips)
        count, lines = self._harvest(second)
        self.assertEqual([], second.downloaded)
        self.assertEqual(1, count)
        self.assertTrue(json.loads(lines[0])['series_present'])

    def test_the_cache_default_is_outside_the_repository(self):
        cache = harvest.default_cache_dir()
        repo = os.path.dirname(TOOLS)
        self.assertFalse(os.path.abspath(cache).startswith(os.path.abspath(repo) + os.sep))


class SerialisationTestCase(HarvestTestCase):
    def test_the_output_is_compact_jsonl(self):
        # D22, corrected after a real record was measured at 3.7 KB compact
        # rather than the few hundred bytes the plan first guessed. Indenting
        # a 264 record dataset costs about half a megabyte for nothing.
        record = {'run_id': 1, 'summary': {'a': 1, 'b': [1, 2]}}
        handle = io.StringIO()
        harvest.write_records([record, record], handle)
        body = handle.getvalue()
        self.assertEqual(2, body.count('\n'))
        self.assertNotIn(', ', body)
        self.assertNotIn(': ', body)
        for line in body.strip().split('\n'):
            self.assertEqual(record, json.loads(line))


class RunListingTestCase(HarvestTestCase):
    def _listing(self, created_dates):
        runs = []
        for index, created in enumerate(created_dates):
            run = self._run_payload(run_id=1000 + index)
            run['created_at'] = created
            runs.append(run)
        return FakeGitHub(runs, {}, {}, {})

    def test_since_stops_at_the_window_edge(self):
        # Newest first, so the first run before the window means every
        # remaining run is too -- which is what keeps the harvest from
        # walking the whole of the repository's history.
        github = self._listing([
            '2026-09-02T00:00:00Z', '2026-08-31T00:00:00Z',
            '2026-08-29T00:00:00Z', '2026-08-28T00:00:00Z'])
        runs = harvest.list_runs(
            github, since=harvest.parse_since('2026-08-30'))
        self.assertEqual([1000, 1001], [r['id'] for r in runs])

    def test_limit_caps_the_run_count(self):
        github = self._listing([
            '2026-09-02T00:00:00Z', '2026-09-01T00:00:00Z',
            '2026-08-31T00:00:00Z'])
        runs = harvest.list_runs(github, limit=2)
        self.assertEqual([1000, 1001], [r['id'] for r in runs])

    def test_a_naive_since_is_read_as_utc(self):
        # GitHub reports created_at in UTC. A window boundary which moved
        # with the operator's timezone would not be reproducible from the
        # command the dataset's README quotes.
        parsed = harvest.parse_since('2026-08-30')
        self.assertEqual('UTC', str(parsed.tzinfo))
