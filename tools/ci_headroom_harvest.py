#!/usr/bin/env python3
# Copyright 2019 Michael Still and contributors
"""Turn the banked CI headroom bundles from many merge runs into one dataset.

Phase 1 of the CI cloud sizing plan built the instrument: every functional
cluster job samples ``/admin/resources`` and the node roster every fifteen
seconds, scrapes a filtered Loki census of the scheduler's per-candidate
stage events, and drops both into the job's ninety day artifact bundle.
Phase 2's D16 decided the baseline is computed *retrospectively* from the
runs already banked rather than by opening a new window and waiting it out:
the instrument has been running for a week, artifact retention is ninety
days, and every day spent waiting for the late part of the window is a day
of the early part expiring.

This tool is what makes that retrospective possible. It enumerates the
``merge_group`` runs of ``functional-tests.yml``, downloads each run's
cluster bundles, unpacks the two trace files phase 1 writes, hands them to
``tools/ci_headroom_report.py``'s ``summary_record()`` (D18), and writes one
JSON object per job per run. Step 2d runs it over the whole window and
commits the result; phase 4, phase 5 and any future re-measure check the
arithmetic against that file rather than trusting a number in a plan.

Four things about the shape of the data are load bearing, and each of them
was checked against a real artifact rather than reasoned about:

* **The bundle is a nested zip.** ``gh api .../artifacts/<id>/zip`` returns a
  zip whose single member is ``bundle.zip``, and the traces are inside
  *that*, at ``bundle/traces/``. Reading the outer zip's namelist for
  ``traces/headroom.jsonl`` finds nothing and would have looked exactly like
  a run whose probe never started.
* **Only four of the six cluster bundles carry the probe at all** (D17), for
  two different reasons, so the other two are skipped by name rather than
  recorded as missing data. See ``UNINSTRUMENTED_BUNDLES`` below.
* **The topology is not in the series.** It is passed to the report at run
  time and never written down, so a bundle on disk does not say which shape
  produced it. D20 fixes that prospectively by having the collect script
  write ``traces/headroom-label``; until those runs exist the mapping comes
  from the explicit table in ``BUNDLE_TOPOLOGIES``, which fails loudly on a
  bundle name it does not cover rather than guessing.
* **A bundle with no series is a record, not a gap.** A run predating phase
  1, or one whose probe never started, is written out with ``summary`` null
  and a reason. Dropping it silently would make the window look smaller and
  healthier than it was.

Unlike ``ci_headroom_report.py``, which runs on a CI runner under stock
python3 and is therefore standard library only, this is a developer tool run
by hand from a checkout. It still imports nothing outside the standard
library, but only because it did not need to: the one thing that would have
wanted a dependency is HTTP, and shelling out to ``gh`` gets authentication,
retries and enterprise hosts for free.

D15's exit-zero-whatever-happens discipline does **not** apply here. That
rule exists so an instrument cannot fail the job it is measuring; this tool
runs on a laptop, and a harvest which silently half-completed and exited
zero would poison the baseline. It fails loudly instead.

Example:

    tools/ci_headroom_harvest.py --since 2026-08-30 \\
        --output docs/plans/data/ci-cloud-sizing-baseline/harvest.jsonl
"""

import argparse
import collections
import datetime
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile


DEFAULT_REPO = 'shakenfist/shakenfist'
DEFAULT_WORKFLOW = 'functional-tests.yml'

# The harvest record's own version, distinct from the summary record's
# (``RECORD_VERSION`` in ci_headroom_report.py). The two change for different
# reasons -- this one when the harvest's framing of a job changes, that one
# when the measurement itself does -- and a consumer which conflated them
# would be unable to say which half it could not read.
HARVEST_VERSION = 1

# Where inside the *inner* zip the phase 1 traces live. The bundle is built
# by scp'ing the whole of /srv/ci/traces/ off the cluster primary, so these
# paths are the primary's directory layout with a 'bundle/' prefix the
# archive step adds.
TRACES_PREFIX = 'bundle/traces/'
SERIES_MEMBER = TRACES_PREFIX + 'headroom.jsonl'
CENSUS_MEMBER = TRACES_PREFIX + 'headroom-census.json'
LABEL_MEMBER = TRACES_PREFIX + 'headroom-label'

# The single member of the outer artifact zip. Verified against artifact
# 9964055153 of merge run 33944911413: the outer namelist is exactly
# ['bundle.zip'] and the 1399 real entries are inside it.
INNER_BUNDLE_MEMBER = 'bundle.zip'

# Every artifact this tool will even look at starts with this. A run also
# uploads 'coverage', which is not a bundle and is not a missing bundle
# either, so it is passed over without comment -- whereas an unrecognised
# *bundle* is an error, because a new cluster job is exactly the kind of
# change that would otherwise silently shrink the dataset.
BUNDLE_PREFIX = 'bundle-'


BundleKind = collections.namedtuple(
    'BundleKind', ['job', 'topology', 'job_prefix'])


# D17's table, sourced from the merge matrix at
# .github/workflows/functional-tests.yml:436-480. Three of the four jobs run
# the *same* topology, which is the point: if slim-primary's three jobs
# differ from each other in peak demand then the difference is the suite and
# not the shape, and phase 4 must not respond to it by resizing the cloud.
# Pooling by topology would hide that, so the harvest records the job as well
# as the topology and step 2d reports both.
#
# 'job_prefix' is the name GitHub gives the job in the runs/<id>/jobs
# listing, which is *not* the matrix's 'name': the reusable smoke-cluster
# workflow contributes its own job name, so what the API returns is
# 'Debian 12 cluster (collection) / Smoke tests (collection)'. The prefix is
# stored explicitly rather than derived, because the derivation ('name' plus
# ' (collection)') is a fact about how functional-tests.yml happens to name
# the calling job today and would break silently if that changed.
BUNDLE_TOPOLOGIES = {
    'bundle-shakenfist-full-debian-12-slim-primary': BundleKind(
        'Debian 12 cluster', 'slim-primary', 'Debian 12 cluster (collection)'),
    'bundle-shakenfist-full-ubuntu-2404-slim-primary': BundleKind(
        'Ubuntu 24.04 cluster', 'slim-primary',
        'Ubuntu 24.04 cluster (collection)'),
    'bundle-shakenfist-full-guests': BundleKind(
        'Guests', 'slim-primary', 'Guests (collection)'),
    'bundle-shakenfist-full-debian-12-slim-tier': BundleKind(
        'Debian 12 tier', 'slim-tier', 'Debian 12 tier (collection)'),
}


# The two cluster bundles which carry no traces/ directory at all. Both were
# checked empirically against merge run 33944911413 rather than reasoned
# about, and the two causes are different, which is why they are listed with
# their reasons rather than as a bare set:
#
# * 'Ansible modules' does run through the reusable smoke-cluster workflow,
#   but with test_kind: ansible-modules (functional-tests.yml:514), and every
#   probe step in that workflow is gated `if: inputs.test_kind ==
#   'functional'`.
# * 'Node lifecycle' never reaches that workflow at all. It calls the
#   build-smoke-cluster composite action directly
#   (functional-tests.yml:554-557), and the probe steps live in the workflow
#   rather than in the action, so they are not in its job.
#
# Skipping them by name, deliberately, is the difference between a dataset
# which is missing two jobs and one which quietly records four failed
# harvests per run. Step 2f files the Future work entry for instrumenting
# them; D17 puts doing so out of this phase's scope.
UNINSTRUMENTED_BUNDLES = {
    'bundle-shakenfist-full-ansible-modules': (
        'the Ansible modules job runs smoke-cluster.yml with test_kind: '
        'ansible-modules, and every probe step in that workflow is gated on '
        'test_kind == functional'),
    'bundle-functional-node-lifecycle-collection': (
        'the Node lifecycle job calls the build-smoke-cluster composite '
        'action directly and never reaches the workflow the probe steps '
        'live in'),
}


class HarvestError(Exception):
    """Anything which should stop the harvest rather than shrink it."""


class UnknownBundleError(HarvestError):
    """A cluster bundle whose topology this tool has not been told.

    Named, and raised rather than skipped, because of what the alternatives
    cost. Guessing a topology from the artifact name would put a made-up
    label on a real measurement in a dataset the next three phases argue
    from. Skipping quietly would drop a whole job out of the window without
    saying so, and the resulting baseline would be a smaller n than it
    claimed with no way for a reader to tell. A new bundle name means the
    merge matrix in .github/workflows/functional-tests.yml gained a row, and
    a human should decide which topology it is.
    """


class GitHubCLIError(HarvestError):
    """The gh binary failed, and we cannot tell an empty result from a fault."""


class GitHubCLI:
    """Everything this tool asks GitHub for, funnelled through gh.

    Deliberately not an HTTP library. The GitHub REST API needs a token, and
    every developer running this already has one working through gh -- via a
    keyring, a device flow, GH_TOKEN, or an enterprise host -- so reaching
    for requests would mean reimplementing the auth discovery gh already
    does, badly, and asking the operator to paste a PAT into a shell.

    Rate limits are not a constraint at this size. The core limit is 5000
    per hour and a full 66-run harvest issues about 400 calls (one run list
    page, then three per run), so the tool does not implement backoff; if it
    ever does hit a limit, gh's own error surfaces and the harvest stops
    rather than writing a short dataset.
    """

    def __init__(self, repo=DEFAULT_REPO, gh='gh', verbose=False):
        self.repo = repo
        self.gh = gh
        self.verbose = verbose
        self.calls = 0

    def _log(self, message):
        if self.verbose:
            print(message, file=sys.stderr)

    def json(self, path):
        """GET an API path under this repository and parse the response."""
        full = 'repos/%s/%s' % (self.repo, path)
        self._log('gh api %s' % full)
        self.calls += 1
        try:
            completed = subprocess.run(
                [self.gh, 'api', full], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        except OSError as e:
            raise GitHubCLIError(
                'could not run %s: %s. This tool shells out to the GitHub '
                'CLI for authentication; install it and run `gh auth login`.'
                % (self.gh, e))
        if completed.returncode != 0:
            raise GitHubCLIError(
                'gh api %s failed with exit code %d: %s'
                % (full, completed.returncode,
                   completed.stderr.decode('utf-8', 'replace').strip()))
        try:
            return json.loads(completed.stdout.decode('utf-8'))
        except ValueError as e:
            raise GitHubCLIError('gh api %s returned unparseable JSON: %s' % (full, e))

    def download(self, path, dest):
        """GET a binary API path under this repository into a file.

        Written to a .part file and renamed on success, so an interrupted
        harvest never leaves a truncated zip in the cache for the next run
        to treat as complete. The cache is keyed by artifact id and artifact
        content is immutable, so a completed file is always reusable.
        """
        full = 'repos/%s/%s' % (self.repo, path)
        self._log('gh api %s > %s' % (full, dest))
        self.calls += 1
        partial = dest + '.part'
        try:
            with open(partial, 'wb') as f:
                completed = subprocess.run(
                    [self.gh, 'api', full], stdout=f, stderr=subprocess.PIPE)
        except OSError as e:
            raise GitHubCLIError('could not run %s: %s' % (self.gh, e))
        if completed.returncode != 0:
            if os.path.exists(partial):
                os.unlink(partial)
            raise GitHubCLIError(
                'gh api %s failed with exit code %d: %s'
                % (full, completed.returncode,
                   completed.stderr.decode('utf-8', 'replace').strip()))
        os.rename(partial, dest)
        return dest

    def paginate(self, path, key, per_page=100, pages=None):
        """Walk a paginated listing, yielding one page's items at a time.

        Pages rather than items because the run listing wants to stop early:
        runs come back newest first, so once a page's oldest run is before
        the window there is nothing older worth asking for, and asking
        anyway would walk the whole of the repository's history.
        """
        joiner = '&' if '?' in path else '?'
        page = 1
        while pages is None or page <= pages:
            body = self.json(
                '%s%sper_page=%d&page=%d' % (path, joiner, per_page, page))
            items = body.get(key) or []
            if not items:
                return
            yield items
            if len(items) < per_page:
                return
            page += 1


def load_report(path):
    """Load tools/ci_headroom_report.py by path.

    The tools/ directory is not a package and this file deliberately imports
    nothing from shakenfist, so there is no import path to reach it by. The
    report's own tests load it exactly this way.
    """
    spec = importlib.util.spec_from_file_location('ci_headroom_report', path)
    if spec is None or spec.loader is None:
        raise HarvestError('could not load the report tool from %s' % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_since(value):
    """Parse a --since date into a comparable datetime.

    Accepts a bare date or a full ISO 8601 timestamp. GitHub returns
    created_at as UTC with a trailing Z, so a naive value is read as UTC
    rather than as local time; a harvest whose window boundary moved with
    the operator's timezone would not be reproducible from the README step
    2d is required to write.
    """
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            '%r is not an ISO 8601 date or timestamp (try 2026-08-30)' % value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def parse_timestamp(value):
    """Parse a GitHub timestamp, or None if it is missing or malformed."""
    if not value:
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def list_runs(github, workflow=DEFAULT_WORKFLOW, since=None, limit=None):
    """The merge_group runs of one workflow, newest first.

    Only completed runs. A run still in flight has some of its bundles
    uploaded and some not, and there is no way from the artifact listing to
    tell "this job has not finished" from "this job produced no bundle", so
    including one would put a spurious probe-absent record in the dataset.

    ``since`` is inclusive and compared against the run's creation time,
    which is the field D16's window is defined in terms of (the census fix
    merged on 2026-08-30, and runs created before that carry a census which
    could not have collected the guard events).
    """
    runs = []
    path = 'actions/workflows/%s/runs?event=merge_group&status=completed' % workflow
    for page in github.paginate(path, 'workflow_runs'):
        exhausted = False
        for run in page:
            created = parse_timestamp(run.get('created_at'))
            if since is not None and created is not None and created < since:
                # The listing is newest first, so the first run before the
                # window means every remaining run is too.
                exhausted = True
                break
            runs.append(run)
            if limit is not None and len(runs) >= limit:
                return runs
        if exhausted:
            break
    return runs


def job_conclusions(jobs):
    """Index a run's jobs by name, for the topology table's prefix lookup."""
    return [(job.get('name') or '', job.get('conclusion')) for job in jobs]


def find_job(jobs, prefix):
    """The (name, conclusion) of the job a bundle came from, or (None, None).

    Matched on a prefix because a job called through a reusable workflow is
    reported as '<caller job> / <called job>'. An exact match is tried first
    so a hypothetical job named exactly like the prefix is not shadowed by
    its own children.
    """
    for name, conclusion in jobs:
        if name == prefix:
            return name, conclusion
    for name, conclusion in jobs:
        if name.startswith(prefix + ' /'):
            return name, conclusion
    return None, None


def classify_artifact(name):
    """Decide what to do with one artifact, by name alone.

    Returns ('skip', reason) for something which is deliberately not part of
    the dataset, or ('harvest', BundleKind) for one which is. Raises
    UnknownBundleError for a bundle the topology table does not cover --
    see that exception's docstring for why this is not a skip.
    """
    if not name.startswith(BUNDLE_PREFIX):
        # 'coverage' and anything else a job uploads. Not a bundle, so not a
        # missing bundle either, and not worth a record.
        return 'ignore', 'not a bundle artifact'
    if name in UNINSTRUMENTED_BUNDLES:
        return 'skip', UNINSTRUMENTED_BUNDLES[name]
    if name in BUNDLE_TOPOLOGIES:
        return 'harvest', BUNDLE_TOPOLOGIES[name]
    raise UnknownBundleError(
        'artifact %r is a cluster bundle this tool has no topology for. The '
        'merge matrix in .github/workflows/functional-tests.yml has probably '
        'gained a row; add it to BUNDLE_TOPOLOGIES with its topology, or to '
        'UNINSTRUMENTED_BUNDLES with the reason it carries no probe. '
        'Guessing here would put a fabricated topology on a real '
        'measurement.' % name)


def open_bundle(path):
    """Open a bundle artifact zip, seeing through the nested-zip wrapper.

    An artifact download is a zip whose only member is bundle.zip, and every
    real file is inside that. Verified against artifact 9964055153 of merge
    run 33944911413: the outer namelist is exactly ['bundle.zip'].

    A bundle whose outer zip is not that shape is opened directly, so that a
    future change to how the bundle is packed degrades into reading the
    right file rather than into 'the probe never ran'. The two cases are
    distinguished by looking for the wrapper by name, not by counting
    members, because the count is the incidental part.
    """
    outer = zipfile.ZipFile(path)
    if INNER_BUNDLE_MEMBER not in outer.namelist():
        return outer
    inner_bytes = outer.read(INNER_BUNDLE_MEMBER)
    outer.close()
    return zipfile.ZipFile(io.BytesIO(inner_bytes))


def extract_traces(path, dest_dir):
    """Unpack the phase 1 trace files from a bundle, if they are there.

    Returns a dict of member basename to extracted path, holding only the
    files which were actually present. The series being absent is a normal
    outcome -- a run predating phase 1, or one whose probe never started --
    and is reported by the caller, not raised here.
    """
    found = {}
    with open_bundle(path) as bundle:
        names = set(bundle.namelist())
        for member in (SERIES_MEMBER, CENSUS_MEMBER, LABEL_MEMBER):
            if member not in names:
                continue
            basename = os.path.basename(member)
            target = os.path.join(dest_dir, basename)
            with open(target, 'wb') as f:
                f.write(bundle.read(member))
            found[basename] = target
    return found


def read_label(path):
    """Read traces/headroom-label, D20's fix for the topology guesswork.

    The collect script in shakenfist/actions is already passed the label the
    report prints; D20 has it write that label into the bundle as well, so a
    bundle on disk says which shape produced it and a future harvest need
    not infer it from the artifact name. Bundles in the retrospective window
    predate that change and have no such file, which is exactly why the
    artifact-name table still exists.
    """
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def topology_from_label(label):
    """The topology out of a label, which is '<topology> <stestr config>'.

    The label the workflow passes the report is the topology followed by the
    suite's stestr configuration, so the first whitespace-delimited token is
    the topology. Anything the label file says is taken at face value: the
    whole point of D20's file is that a topology added after this tool was
    written should not need the table updating.
    """
    if not label:
        return None
    parts = label.split()
    return parts[0] if parts else None


def bundle_record(github, run, artifact, kind, jobs, cache_dir, report,
                  census_limit, workdir):
    """Harvest one job's bundle into one record.

    Everything about the run and the job is recorded beside the measurement
    rather than left to be joined later. The head SHA in particular is a
    requirement of D16: a retrospective window spans a week of development,
    so a step change part way through it has to be visible as one, and it is
    only visible if each record says which tree produced it.
    """
    job_name, job_conclusion = find_job(jobs, kind.job_prefix)

    record = collections.OrderedDict()
    record['harvest_version'] = HARVEST_VERSION
    record['repo'] = github.repo
    record['run_id'] = run.get('id')
    record['run_attempt'] = run.get('run_attempt')
    record['run_url'] = run.get('html_url')
    record['head_sha'] = run.get('head_sha')
    record['run_created_at'] = run.get('created_at')
    record['run_conclusion'] = run.get('conclusion')
    record['artifact_id'] = artifact.get('id')
    record['artifact_name'] = artifact.get('name')
    record['job'] = kind.job
    record['github_job_name'] = job_name
    record['job_conclusion'] = job_conclusion
    record['topology'] = kind.topology
    record['topology_source'] = 'artifact-name-table'
    record['topology_table_says'] = kind.topology
    record['label'] = None
    record['series_present'] = False
    record['census_present'] = False
    record['absent_reason'] = None
    record['summary'] = None

    if artifact.get('expired'):
        # Ninety day retention. Recorded rather than skipped so a window
        # which has begun to expire is visible in the dataset as expiry,
        # and not as jobs which mysteriously stopped producing a series.
        record['absent_reason'] = 'the artifact has expired'
        return record

    zip_path = cached_artifact(github, artifact, cache_dir)
    try:
        traces = extract_traces(zip_path, workdir)
    except (zipfile.BadZipFile, OSError) as e:
        # A corrupt cache entry is worth failing on rather than recording as
        # missing data: it is a fact about this machine, not about the run,
        # and deleting the file and re-running is the fix.
        raise HarvestError(
            'bundle %s (artifact %s) could not be read from %s: %s. Delete '
            'that file to re-download it.'
            % (artifact.get('name'), artifact.get('id'), zip_path, e))

    label = read_label(traces['headroom-label']) if 'headroom-label' in traces else None
    if label:
        record['label'] = label
        labelled = topology_from_label(label)
        if labelled:
            record['topology'] = labelled
            record['topology_source'] = 'headroom-label'
    else:
        # The report is normally passed '<topology> <stestr config>'; with no
        # label file the only half we can reconstruct is the topology.
        record['label'] = kind.topology

    record['census_present'] = 'headroom-census.json' in traces

    if 'headroom.jsonl' not in traces:
        # Not dropped. A run predating phase 1, or one whose probe never
        # started, is part of the window and its absence is a finding: the
        # denominator step 2d states its n against has to include it.
        record['absent_reason'] = (
            'the bundle carries no traces/headroom.jsonl, so the probe never '
            'ran or predates phase 1')
        return record

    record['series_present'] = True
    record['summary'] = report.summary_record(
        traces['headroom.jsonl'], traces.get('headroom-census.json'),
        label=record['label'], census_limit=census_limit)
    return record


def cached_artifact(github, artifact, cache_dir):
    """Return a local path to the artifact zip, downloading it if need be.

    Keyed by artifact id, which is immutable and unique across the
    repository, so a cache hit needs no validation beyond the file being a
    readable zip. This matters more than it looks: the full window is 66
    runs times four bundles at roughly 5 MB, so about 1.3 GB, and step 2d
    will not get the harvest right on the first try.
    """
    artifact_id = artifact.get('id')
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, '%s.zip' % artifact_id)
    if os.path.exists(path) and zipfile.is_zipfile(path):
        return path
    github.download('actions/artifacts/%s/zip' % artifact_id, path)
    return path


def harvest_run(github, run, cache_dir, report, census_limit, workdir):
    """Every instrumented bundle of one run, as records.

    Reports rather than returns, in the sense that an unknown bundle raises
    out of here: one new matrix row should stop the harvest so a human
    decides what it is, not produce a dataset which is quietly missing a
    job.
    """
    run_id = run.get('id')
    artifacts = []
    for page in github.paginate('actions/runs/%s/artifacts' % run_id, 'artifacts'):
        artifacts.extend(page)

    wanted = []
    for artifact in artifacts:
        action, payload = classify_artifact(artifact.get('name') or '')
        if action == 'harvest':
            wanted.append((artifact, payload))
    if not wanted:
        return []

    # Only fetched once a run turns out to have something to harvest, which
    # keeps a run whose bundles all expired down to a single call.
    jobs = []
    for page in github.paginate('actions/runs/%s/jobs' % run_id, 'jobs'):
        jobs.extend(page)
    jobs = job_conclusions(jobs)

    records = []
    for artifact, kind in wanted:
        # A directory per artifact. The three trace files have the same
        # basenames in every bundle, so unpacking two bundles into one
        # directory would leave the second reading the first's leftovers
        # whenever it is missing a file -- which is precisely the case this
        # tool has to report accurately.
        unpacked = os.path.join(workdir, str(artifact.get('id')))
        os.makedirs(unpacked, exist_ok=True)
        records.append(bundle_record(
            github, run, artifact, kind, jobs, cache_dir, report,
            census_limit, unpacked))
    return records


def write_records(records, handle):
    """One compact JSON object per line (D22).

    Compact, and not pretty printed, because the size estimate this phase
    inherited was an order of magnitude out: a real record measured against
    merge run 33944911413 is 3,675 bytes compact and 5,381 indented, so the
    full window is roughly 950 KB rather than the 'low hundreds of
    kilobytes' D22 originally guessed. One object per line so the dataset
    can be filtered with grep and read a record at a time, and so a harvest
    interrupted part way through leaves a file which still parses.
    """
    count = 0
    for record in records:
        handle.write(json.dumps(record, separators=(',', ':')))
        handle.write('\n')
        count += 1
    return count


def default_cache_dir():
    """Somewhere outside the repository, always.

    The full window is about 1.3 GB of zips. A cache under the checkout
    would either pollute git status or need a gitignore entry which the next
    person to move the tool would forget, and either way a `git clean` would
    throw away an hour of downloads.
    """
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, 'shakenfist-ci-headroom')


def default_report_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'ci_headroom_report.py')


def build_parser(default_census_limit):
    parser = argparse.ArgumentParser(
        description=('Harvest the banked CI headroom bundles from merge runs '
                     'into one JSONL dataset (phase 2, D16/D17).'))
    parser.add_argument(
        '--repo', default=DEFAULT_REPO,
        help='The repository to harvest, as owner/name.')
    parser.add_argument(
        '--workflow', default=DEFAULT_WORKFLOW,
        help='The workflow file whose merge_group runs carry the bundles.')
    parser.add_argument(
        '--since', type=parse_since, default=None,
        help=('Only runs created at or after this date (ISO 8601). The phase '
              '2 window starts at 2026-08-30, the day the census filter fix '
              'merged.'))
    parser.add_argument(
        '--limit', type=int, default=None,
        help=('Stop after this many runs, newest first. Useful for proving '
              'the tool before committing to the whole window.'))
    parser.add_argument(
        '--cache-dir', default=default_cache_dir(),
        help=('Where downloaded bundle zips are kept, keyed by artifact id. '
              'Anything already there is reused. Defaults outside the '
              'repository so a git status is never buried under a gigabyte '
              'of zips.'))
    parser.add_argument(
        '--output', '-o', required=True,
        help=('Where to write the dataset, as one compact JSON object per '
              'line (D22).'))
    parser.add_argument(
        '--report', default=default_report_path(),
        help='Path to tools/ci_headroom_report.py, whose summary_record() this calls.')
    parser.add_argument(
        '--census-limit', type=int, default=default_census_limit,
        help=('The entry limit the census queries were issued with, passed '
              'through to the report so a response holding exactly that many '
              'entries is reported as possibly truncated.'))
    parser.add_argument(
        '--quiet', action='store_true',
        help='Do not print per-run progress to stderr.')
    return parser


def harvest(github, args, report):
    """Enumerate, download, summarise and write. Returns the record count."""
    runs = list_runs(github, workflow=args.workflow, since=args.since,
                     limit=args.limit)
    if not args.quiet:
        print('Harvesting %d merge_group %s of %s'
              % (len(runs), 'run' if len(runs) == 1 else 'runs',
                 args.workflow), file=sys.stderr)

    written = 0
    # Written incrementally rather than accumulated and dumped at the end.
    # A harvest of the full window is an hour of downloads, and an
    # interruption two thirds of the way through should leave two thirds of
    # a usable dataset rather than nothing.
    with open(args.output, 'w') as handle:
        for index, run in enumerate(runs, start=1):
            if not args.quiet:
                print('[%d/%d] run %s %s %s'
                      % (index, len(runs), run.get('id'),
                         (run.get('head_sha') or '')[:12],
                         run.get('created_at')), file=sys.stderr)
            with tempfile.TemporaryDirectory(prefix='ci-headroom-') as workdir:
                records = harvest_run(
                    github, run, args.cache_dir, report, args.census_limit,
                    workdir)
            written += write_records(records, handle)
            handle.flush()
    return written


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # The report module is loaded before arguments are parsed only so that
    # --census-limit can default to the same constant the report does, which
    # is the value tools/ci_headroom_collect.sh actually issues its query
    # with. A --report override still takes effect: the module is reloaded
    # below if the path differs.
    report = load_report(default_report_path())
    parser = build_parser(report.DEFAULT_CENSUS_LIMIT)
    args = parser.parse_args(argv)
    if os.path.abspath(args.report) != os.path.abspath(default_report_path()):
        report = load_report(args.report)

    if args.since is None and args.limit is None:
        # Refused rather than defaulted. Without a bound this walks the whole
        # of artifact retention and downloads every bundle in it, and the
        # operator who wanted the whole window should say which window that
        # is so the README step 2d writes can quote a reproducible command.
        parser.error('give at least one of --since or --limit; an unbounded '
                     'harvest downloads the entire 90 day retention window')

    github = GitHubCLI(repo=args.repo, verbose=not args.quiet)
    written = harvest(github, args, report)
    if not args.quiet:
        print('Wrote %d %s to %s in %d API calls'
              % (written, 'record' if written == 1 else 'records',
                 args.output, github.calls), file=sys.stderr)
    return 0


if __name__ == '__main__':
    # Unlike the report tool, this one is allowed to fail. D15's exit-zero
    # rule is about not failing the job an instrument is measuring; a harvest
    # which half completed and exited zero would hand step 2d a short dataset
    # with nothing to say it was short.
    sys.exit(main())
