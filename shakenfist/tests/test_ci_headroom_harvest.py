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
* The known-uninstrumented bundle is skipped *by name*, and skipping it is
  not the same code path as failing on an unknown one -- a test pins that it
  produces no record and no exception. A separate test pins the two tables'
  *shape*: no bundle name is in both, every ``BundleKind`` is fully filled
  in, and every topology it names is one that actually exists -- so a bundle
  moved between the tables (as the Ansible modules one was, by phase 6 of
  PLAN-ci-cloud-sizing-phase-06-docs.md) cannot end up in neither, or in
  both.
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

PRIMARY_BUNDLE = 'bundle-shakenfist-full-debian-13-slim-primary'
TIER_BUNDLE = 'bundle-shakenfist-full-debian-13-slim-tier'
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
        self.paths = []

    def json(self, path):
        self.calls += 1
        raise AssertionError('unexpected direct json() call for %s' % path)

    def paginate(self, path, key, per_page=100, pages=None):
        self.calls += 1
        self.paths.append(path)
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
        self.until = None
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
        # traces/ directory, and treating them as missing data would put a
        # bogus probe-absent record in every window. Iterates the table
        # itself rather than a hardcoded tuple, so this does not go stale the
        # next time a bundle moves between the two tables.
        for name in harvest.UNINSTRUMENTED_BUNDLES:
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

    def test_the_ansible_modules_bundle_is_now_instrumented(self):
        # Phase 6 of PLAN-ci-cloud-sizing-phase-06-docs.md (D3) moved this
        # bundle out of UNINSTRUMENTED_BUNDLES once the matching
        # shakenfist/actions change widened the probe-step gate onto that
        # job. job_prefix is read from two real runs (36343591915,
        # 36316642104), not derived, because the derivation broke once
        # before -- see the comment above BUNDLE_TOPOLOGIES.
        action, kind = harvest.classify_artifact(ANSIBLE_BUNDLE)
        self.assertEqual('harvest', action)
        self.assertEqual('slim-primary', kind.topology)
        self.assertEqual('Ansible modules', kind.job)
        self.assertEqual('Ansible modules (collection)', kind.job_prefix)


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
                {'name': 'Debian 13 cluster (collection) / Smoke tests (collection)',
                 'conclusion': 'success'},
                {'name': 'Debian 13 tier (collection) / Smoke tests (collection)',
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
        self.assertEqual('Debian 13 cluster', record['job'])
        self.assertEqual(
            'Debian 13 cluster (collection) / Smoke tests (collection)',
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
        self.assertEqual('Debian 13 tier', record['job'])

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

    def test_the_uninstrumented_bundle_produces_no_record(self):
        github = self._github({
            PRIMARY_BUNDLE: instrumented_members(),
            LIFECYCLE_BUNDLE: {'bundle/logs/syslog': 'x\n'},
            'coverage': {'x': 'y'},
        })
        count, lines = self._harvest(github)
        self.assertEqual(1, count)
        self.assertEqual(PRIMARY_BUNDLE, json.loads(lines[0])['artifact_name'])

    def test_an_ansible_modules_bundle_with_no_traces_yet_is_recorded_absent(self):
        # The bundle-table half of D3 (phase 6) can land before the matching
        # shakenfist/actions change is pushed -- the plan's Prepared changes
        # section says so explicitly. Until then a real 'Ansible modules'
        # bundle still uploads (the job already produced one; only its
        # contents change), so a harvest run in that window must not raise
        # and must not silently drop it: it is now classified 'harvest', so
        # it goes through the same no-series path as any other instrumented
        # bundle whose probe did not run, and is recorded with a reason
        # rather than dropped.
        github = self._github({
            PRIMARY_BUNDLE: instrumented_members(),
            ANSIBLE_BUNDLE: {'bundle/logs/syslog': 'x\n'},
        })
        count, lines = self._harvest(github)
        self.assertEqual(2, count)
        by_name = {json.loads(line)['artifact_name']: json.loads(line) for line in lines}
        self.assertFalse(by_name[ANSIBLE_BUNDLE]['series_present'])
        self.assertIsNone(by_name[ANSIBLE_BUNDLE]['summary'])
        self.assertIn('headroom.jsonl', by_name[ANSIBLE_BUNDLE]['absent_reason'])
        self.assertEqual('slim-primary', by_name[ANSIBLE_BUNDLE]['topology'])

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


class LoudFailureTestCase(HarvestTestCase):
    """An empty harvest is an error, whatever emptied it.

    The ordering defect was one road to a harvest which wrote an empty file
    over the dataset and exited zero. Fixing it closes that road and not the
    symptom: a renamed workflow file, a changed event name, a created=
    filter the API stops honouring or a token whose scope has lapsed all
    arrive at the same place. The module docstring promises this tool fails
    loudly instead.
    """

    def test_an_empty_enumeration_is_an_error(self):
        github = FakeGitHub([], {}, {}, {})
        self.assertRaises(harvest.HarvestError, self._harvest, github)

    def test_an_empty_enumeration_does_not_truncate_the_dataset(self):
        # The output is opened for writing before anything is downloaded, so
        # the check has to come first. Re-running a harvest whose window has
        # gone wrong must not be the thing that destroys the last good copy.
        with open(self.output, 'w') as f:
            f.write('{"run_id":1}\n')
        github = FakeGitHub([], {}, {}, {})
        self.assertRaises(harvest.HarvestError, self._harvest, github)
        with open(self.output) as f:
            self.assertEqual('{"run_id":1}\n', f.read())

    def test_runs_which_carry_no_bundle_are_an_error_too(self):
        # Distinct from the case above: the window had runs in it, and every
        # one of them yielded nothing. A bundle naming change reads this
        # way, and so does a workflow which stopped uploading them.
        run = self._run_payload()
        github = FakeGitHub(
            [run], {run['id']: [{'id': 9800, 'name': 'coverage',
                                 'expired': False}]},
            {run['id']: []}, {})
        self.assertRaises(harvest.HarvestError, self._harvest, github)

    def test_a_harvest_which_produced_a_record_is_not_an_error(self):
        # The guard must not fire on a harvest which worked, including one
        # whose only record is an absence: an expired artifact is a fact
        # about the window and is written rather than dropped.
        run = self._run_payload()
        github = FakeGitHub(
            [run], {run['id']: [{'id': 9801, 'name': PRIMARY_BUNDLE,
                                 'expired': True}]},
            {run['id']: []}, {})
        count, lines = self._harvest(github)
        self.assertEqual(1, count)
        self.assertIn('expired', json.loads(lines[0])['absent_reason'])


class CacheTestCase(HarvestTestCase):
    def test_a_cached_artifact_is_not_downloaded_twice(self):
        # The full window is roughly 1.3 GB, and step 2d will not get the
        # harvest right on the first attempt.
        run = self._run_payload()
        artifacts = [{'id': 9700, 'name': PRIMARY_BUNDLE, 'expired': False}]
        zips = {9700: self._zip('9700', instrumented_members())}
        jobs = [{'name': 'Debian 13 cluster (collection) / Smoke tests (collection)',
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

    def test_since_filters_a_newest_first_listing(self):
        github = self._listing([
            '2026-09-02T00:00:00Z', '2026-08-31T00:00:00Z',
            '2026-08-29T00:00:00Z', '2026-08-28T00:00:00Z'])
        runs = harvest.list_runs(
            github, since=harvest.parse_since('2026-08-30'))
        self.assertEqual([1000, 1001], [r['id'] for r in runs])

    def test_since_filters_an_oldest_first_listing_too(self):
        # This is the regression. The listing is not documented as ordered
        # and on 2026-09-08 it arrived oldest first, whereupon the old
        # early-exit read the first run, found it before the window, and
        # returned nothing at all for a window with sixteen runs in it.
        github = self._listing([
            '2026-08-28T00:00:00Z', '2026-08-29T00:00:00Z',
            '2026-08-31T00:00:00Z', '2026-09-02T00:00:00Z'])
        runs = harvest.list_runs(
            github, since=harvest.parse_since('2026-08-30'))
        self.assertEqual([1003, 1002], [r['id'] for r in runs])

    def test_the_window_is_pushed_down_to_the_api(self):
        # Client-side filtering alone would walk the whole of the
        # repository's history now that nothing stops early.
        github = self._listing(['2026-09-02T00:00:00Z'])
        harvest.list_runs(github, since=harvest.parse_since('2026-08-30'))
        self.assertIn('created=%3E%3D2026-08-30T00%3A00%3A00Z',
                      github.paths[0])

    def test_limit_caps_the_run_count(self):
        github = self._listing([
            '2026-09-02T00:00:00Z', '2026-09-01T00:00:00Z',
            '2026-08-31T00:00:00Z'])
        runs = harvest.list_runs(github, limit=2)
        self.assertEqual([1000, 1001], [r['id'] for r in runs])

    def test_limit_takes_the_newest_whatever_the_order(self):
        github = self._listing([
            '2026-08-31T00:00:00Z', '2026-09-02T00:00:00Z',
            '2026-09-01T00:00:00Z'])
        runs = harvest.list_runs(github, limit=2)
        self.assertEqual([1001, 1002], [r['id'] for r in runs])

    def test_a_run_with_no_creation_time_is_kept_not_dropped(self):
        github = self._listing(['2026-09-02T00:00:00Z', None])
        runs = harvest.list_runs(github, since=harvest.parse_since('2026-08-30'))
        self.assertEqual([1000, 1001], [r['id'] for r in runs])

    def test_until_bounds_the_far_end_of_the_window(self):
        # A window with only a start grows with every merge. The command
        # step 2g's README first quoted (--since with --limit) stopped
        # reproducing its own dataset within a day, when two more runs
        # merged and the newest ten became a different ten.
        github = self._listing([
            '2026-09-06T00:00:00Z', '2026-09-07T00:00:00Z',
            '2026-09-08T00:00:00Z', '2026-09-09T00:00:00Z'])
        runs = harvest.list_runs(
            github, since=harvest.parse_since('2026-09-07'),
            until=harvest.parse_since('2026-09-08T12:00:00Z'))
        self.assertEqual([1002, 1001], [r['id'] for r in runs])

    def test_both_boundaries_are_pushed_down_to_the_api(self):
        github = self._listing(['2026-09-07T00:00:00Z'])
        harvest.list_runs(
            github, since=harvest.parse_since('2026-09-07'),
            until=harvest.parse_since('2026-09-08T02:00:00Z'))
        self.assertIn(
            'created=2026-09-07T00%3A00%3A00Z..2026-09-08T02%3A00%3A00Z',
            github.paths[0])

    def test_until_alone_is_pushed_down_as_an_upper_bound(self):
        github = self._listing(['2026-09-07T00:00:00Z'])
        harvest.list_runs(
            github, until=harvest.parse_since('2026-09-08T02:00:00Z'))
        self.assertIn('created=%3C%3D2026-09-08T02%3A00%3A00Z',
                      github.paths[0])

    def test_a_since_with_an_offset_is_pushed_down_in_utc(self):
        # parse_since() keeps the offset the operator wrote, and strftime
        # ignores tzinfo, so formatting the parsed value directly would
        # stamp a +10:00 wall clock with a Z and ask the API for a boundary
        # ten hours *later* than the one requested. The server-side filter
        # runs first, so those hours never reach the client-side one: the
        # window narrows silently, in the maintainer's own timezone.
        github = self._listing(['2026-09-02T00:00:00Z'])
        harvest.list_runs(
            github, since=harvest.parse_since('2026-08-30T00:00:00+10:00'))
        self.assertIn('created=%3E%3D2026-08-29T14%3A00%3A00Z',
                      github.paths[0])

    def test_no_since_leaves_the_listing_unfiltered(self):
        # A created= filter which leaked into an unbounded listing would
        # bound it to whatever the last window was.
        github = self._listing(['2026-09-02T00:00:00Z'])
        harvest.list_runs(github, limit=1)
        self.assertNotIn('created=', github.paths[0])

    def test_since_and_limit_together_take_the_newest_in_the_window(self):
        # The form the addendum was actually harvested with.
        github = self._listing([
            '2026-08-28T00:00:00Z', '2026-09-02T00:00:00Z',
            '2026-08-31T00:00:00Z', '2026-09-01T00:00:00Z'])
        runs = harvest.list_runs(
            github, since=harvest.parse_since('2026-08-30'), limit=2)
        self.assertEqual([1001, 1003], [r['id'] for r in runs])

    def test_a_naive_since_is_read_as_utc(self):
        # GitHub reports created_at in UTC. A window boundary which moved
        # with the operator's timezone would not be reproducible from the
        # command the dataset's README quotes.
        parsed = harvest.parse_since('2026-08-30')
        self.assertEqual('UTC', str(parsed.tzinfo))


class BundleTableShapeTestCase(HarvestTestCase):
    """Pin the shape of the two bundle-name tables themselves.

    Distinct from ClassificationTestCase, which checks what classify_artifact
    does with individual names: these tests check the tables it reads never
    drift into an inconsistent shape, which is exactly what moving a bundle
    between them (as D3 of phase 6 did for the Ansible modules one) risks
    getting wrong -- a bundle left in both tables would be classified by
    whichever dict.__contains__ check runs first (classify_artifact tries
    UNINSTRUMENTED_BUNDLES before BUNDLE_TOPOLOGIES), silently skipping real
    data; a bundle in neither raises UnknownBundleError and stops the
    harvest the next time it is run.
    """

    # The topologies a BundleKind is allowed to name. Sourced from D17's
    # table (PLAN-ci-cloud-sizing.md) and F1's inventory
    # (PLAN-ci-cloud-sizing-phase-06-docs.md): every merge_group cluster job
    # this tool ever harvests deploys one of these two shapes. 'localhost'
    # (the single-node smoke topology) is deliberately excluded -- the smoke
    # job runs on pull_request, not merge_group, so this tool never sees a
    # bundle from it (list_runs() only reads merge_group runs), and a
    # BundleKind naming it would be an error, not a new case to allow.
    KNOWN_TOPOLOGIES = frozenset({'slim-primary', 'slim-tier'})

    def test_no_bundle_name_is_in_both_tables(self):
        overlap = set(harvest.UNINSTRUMENTED_BUNDLES) & set(harvest.BUNDLE_TOPOLOGIES)
        self.assertEqual(set(), overlap)

    def test_every_bundle_kind_has_a_job_topology_and_job_prefix(self):
        for name, kind in harvest.BUNDLE_TOPOLOGIES.items():
            self.assertTrue(kind.job, '%s has an empty job' % name)
            self.assertTrue(kind.topology, '%s has an empty topology' % name)
            self.assertTrue(kind.job_prefix, '%s has an empty job_prefix' % name)

    def test_every_uninstrumented_bundle_has_a_non_empty_reason(self):
        for name, reason in harvest.UNINSTRUMENTED_BUNDLES.items():
            self.assertTrue(reason, '%s has an empty reason' % name)

    def test_every_bundle_kind_names_a_topology_that_exists(self):
        for name, kind in harvest.BUNDLE_TOPOLOGIES.items():
            self.assertIn(
                kind.topology, self.KNOWN_TOPOLOGIES,
                '%s names unknown topology %r' % (name, kind.topology))
