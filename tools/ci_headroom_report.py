# Copyright 2019 Michael Still and contributors
"""Summarise a CI headroom series and refusal census for one cluster job.

The CI cloud sizing plan was written from three hand-collected data points,
because nothing in CI records how close a functional test run gets to the
scheduler's admission limits. tools/ci_headroom_probe.py now samples
/admin/resources and the node roster through the test step of every cluster
job; this tool turns one of those series -- optionally beside a refusal
census pulled from Loki -- into a printed summary, so that a reader of the
job log, and later phase 2 of the plan, has a distribution instead of an
anecdote. See docs/plans/PLAN-ci-cloud-sizing-phase-01-headroom-probe.md.

Two constraints shape this file, and both are worth stating because they
look like arbitrary austerity otherwise.

It runs on the *runner*, under whatever python3 the runner image ships,
against a file fetched out of a log bundle. So: standard library only, no
shakenfist import, no third-party import. The percentile() helper below is
copied verbatim from tools/queue-wait-report.py for that reason rather than
imported.

It also exits zero whatever it finds (D15). This phase builds an instrument
and gates nothing; an instrument which can fail the job changes the thing it
is measuring, and would do so during the very window whose distribution
phase 2 means to read. Even an internal error here is printed and shrugged
off, never raised.

Two instruments, reported separately (D9). A fifteen second poll cannot see
a refusal, which begins and ends between samples; a census cannot see a
cloud sitting half empty for an hour. Folding them into one number would
hide which of the two produced it, so the series section and the census
section stand apart and the band verdict names both inputs.

What the numbers mean:

* **Committed vCPU** is max(cpu_measured, cpu_committed), because that is
  the charge admission applies -- summarize_resources() publishes
  cpu_available as cpu_hard_max minus exactly that maximum. Reporting only
  the measurement would read a node whose ledger is full but whose
  instances are still fetching images as idle.
* **The ledger** is the capacity row's cpu_limit where the row exists and
  cpu_hard_max where it does not. Those are the two figures D7 asks phase 2
  to reconcile -- a discrepancy of 12 against 10 which has never been
  explained -- so the count of node-samples which fell back to cpu_hard_max
  is a deliverable of this report, not a diagnostic aside. A run which is
  entirely fallback says something about D7 by itself.
* **Committed memory** is derived, because the payload publishes no such
  field: ram_available is ram_max minus memory_total_instance_actual, so
  ram_max minus ram_available is what the instances actually hold. A node
  publishing no ram_max has no memory ledger at all and is counted, not
  divided by.
* **Ledger unreadable is not idle.** The capacity read swallows failure --
  mariadb.get_scheduler_node_capacity() returns an empty list when the table
  is unreadable as well as when it is empty, and _capacity_by_node() hands
  that straight on -- which makes every cpu_committed zero and every
  cpu_committed_row_present false at once. A sample in that state is
  excluded from the committed CPU figures and reported, rather than averaged
  in as a cluster doing nothing. Note that an unreadable table and one the
  reconciler has never populated are indistinguishable from here; both are
  worth knowing and neither is an idle cluster.
* **A node missing from per_node has four possible meanings**, which is why
  the probe records the roster beside every sample: not a hypervisor,
  metrics older than 120s, a queue over UNREASONABLE_QUEUE_LENGTH, or gone.
  Only the first is answerable from the roster. The rest are reported as
  unexplained rather than dropped, so that "the cluster had three
  hypervisors" is never inferred from a sample which merely could not see
  the other two.

The census tallies whatever stage strings it observes and never a hardcoded
list (D10): the stage names are bare literals at their call sites in
scheduler.py with no enumeration anywhere, so a copy of them in a parser
would drift silently the first time one was added or renamed -- and the
plan itself had already drifted, naming three capacity stages when there
are four. The four capacity stages are named here only to annotate rows and
to decide what counts as a capacity warning; a stage this file has never
heard of still appears in the table with its own count.

The memory stage's three reasons are reported separately and never summed,
because one of them -- 'no memory_max in node metrics' -- is missing data
rather than a shortage. A census which counted it as a memory refusal would
read a stale metrics row as evidence the cloud is too small, which is the
precise error this plan exists to avoid.

Usage:

    python3 tools/ci_headroom_report.py --series /srv/ci/traces/headroom.jsonl \\
        --census /srv/ci/traces/refusal-census.json --label slim-primary
"""

import argparse
import collections
import datetime
import json
import sys
import traceback


# Phase 0's D3 band, as ratios of committed vCPU to ledger. These have never
# been checked against a distribution -- phase 2's job is to replace them or
# defend them -- so every use of them in the output says PROVISIONAL out
# loud. Nothing gates on them in this phase (D15).
BAND_LOWER = 0.35
BAND_UPPER = 0.70

# The two audit messages Scheduler._log_and_raise_on_error() emits, one per
# stage, on every schedule rather than only on failures. Matching is on the
# event's message because pylogrus merges the caller's fields over the log
# record last and one of those fields is 'message': the echo's own message,
# 'Added event', does not survive into the shipped JSON. See the docstring
# of tools/queue-wait-report.py, which learned this the hard way.
STAGE_SURVIVED_PREFIX = 'schedule at stage '
STAGE_ABORTED_PREFIX = 'schedule has no candidates at stage '
STAGE_ABORTED_SUFFIX = ', aborting'

# Presentation only. This maps the four stages whose refusals mean "the
# cluster ran out of something" to the something, so rows can be annotated
# and so a capacity warning can be distinguished from, say, a queue_state
# drop. It is deliberately NOT a filter over what gets tallied: the census
# counts every stage string it observes (D10), and a stage absent from this
# map is still counted, still printed, and flagged as one this tool has not
# been told about.
CAPACITY_STAGE_NOTES = collections.OrderedDict([
    ('sufficient_idle_cpu', 'cpu'),
    ('sufficient_idle_memory', 'memory'),
    ('sufficient_free_disk', 'disk space'),
    ('sufficient_idle_disk',
     'disk BANDWIDTH -- a rate predicate, which sizing cannot address'),
])

# The one refusal reason which is missing data rather than a shortage
# (_has_sufficient_ram() in scheduler.py returns it when a node's metrics
# carry no memory_max at all). Classified by reason string rather than by
# stage, so no stage name is needed to keep it out of the shortage count.
MISSING_DATA_REASON = 'no memory_max in node metrics'

NO_REASON = '(no reason recorded)'

# How a node in the roster but missing from a sample's per_node is
# classified. summarize_resources() omits non-hypervisors, nodes whose
# metrics are older than 120 seconds, and nodes whose queue is over
# UNREASONABLE_QUEUE_LENGTH, and says which it did for none of them.
ABSENCE_NOT_HYPERVISOR = 'not a hypervisor (the roster says so)'
ABSENCE_UNEXPLAINED_HYPERVISOR = (
    'UNEXPLAINED: roster says hypervisor (stale metrics, long queue, or down)')
ABSENCE_UNEXPLAINED_UNKNOWN = (
    'UNEXPLAINED: roster does not say whether it is a hypervisor')
ABSENCE_NO_ROSTER = 'UNEXPLAINED: the sample recorded no roster'
ABSENCE_NOT_IN_ROSTER = 'UNEXPLAINED: in per_node but not in that sample roster'


def percentile(values, fraction):
    """Percentile over an unsorted list, without interpolating.

    Returns None for an empty list, and otherwise the value at the nearest
    index to ``fraction * (n - 1)``. Every value printed is therefore a
    value which was actually observed -- which matters when reading a tail
    made of a handful of samples, where an interpolated p99 is a number
    nothing measured.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = int(round(fraction * (len(ordered) - 1)))
    return ordered[rank]


def numeric(value):
    """Return value as a float, or None if it is not a number.

    Booleans are numbers in Python and are rejected here, because a payload
    field which arrived as True should read as absent rather than as 1.0.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


class NodeSample:
    """One node's headroom in one sample."""

    def __init__(self, node, payload):
        self.node = node

        measured = numeric(payload.get('cpu_measured'))
        committed = numeric(payload.get('cpu_committed'))
        self.committed_cpu = max(measured or 0.0, committed or 0.0)

        # The row's own limit where there is a row, the live derivation
        # where there is not. No fallback in the other direction: seeing the
        # two disagree is the point (D7, D12).
        limit = numeric(payload.get('cpu_limit'))
        hard_max = numeric(payload.get('cpu_hard_max'))
        if limit is not None:
            self.cpu_ledger = limit
            self.cpu_ledger_from_fallback = False
        else:
            self.cpu_ledger = hard_max
            self.cpu_ledger_from_fallback = hard_max is not None

        # Tri-state on purpose. An explicit False from every node in a
        # sample means the capacity read failed; a key which is not there at
        # all means a payload shape this tool does not understand, and
        # should not be read as the same thing.
        row_present = payload.get('cpu_committed_row_present')
        self.row_present = row_present if isinstance(row_present, bool) else None

        ram_max = numeric(payload.get('ram_max'))
        ram_available = numeric(payload.get('ram_available'))
        if ram_max and ram_max > 0 and ram_available is not None:
            self.memory_ledger_mb = ram_max
            self.committed_memory_mb = ram_max - ram_available
        else:
            self.memory_ledger_mb = None
            self.committed_memory_mb = None

    @property
    def cpu_fraction(self):
        if not self.cpu_ledger:
            return None
        return self.committed_cpu / self.cpu_ledger

    @property
    def memory_fraction(self):
        if not self.memory_ledger_mb:
            return None
        return self.committed_memory_mb / self.memory_ledger_mb


class Sample:
    """One line of the series: a resources payload and the roster beside it."""

    def __init__(self, sampled_at, resources, roster):
        self.sampled_at = sampled_at
        self.roster = roster if isinstance(roster, list) else None

        per_node = resources.get('per_node')
        if not isinstance(per_node, dict):
            per_node = {}
        self.nodes = collections.OrderedDict()
        for node in sorted(per_node):
            payload = per_node[node]
            if isinstance(payload, dict):
                self.nodes[node] = NodeSample(node, payload)

        # An empty capacity map makes every row-present flag false at once,
        # which is a read failure wearing an idle cluster's clothes.
        flags = [n.row_present for n in self.nodes.values()]
        self.ledger_unreadable = bool(flags) and all(f is False for f in flags)

    def absences(self):
        """Classify every node the roster names but per_node does not.

        Returns a list of (node label, classification) pairs. Nothing is
        dropped: what the roster cannot explain is returned as unexplained,
        because the alternative is inferring a cluster size from a sample
        which merely could not see the rest of it.
        """
        found = []
        if self.roster is None:
            # One entry for the sample, not one per node: without a roster
            # there is nothing to say about which nodes are missing, and
            # saying it per node would imply the nodes we can see are the
            # problem when it is the sample which is unusable.
            found.append(('(this sample)', ABSENCE_NO_ROSTER))
            return found

        rostered = set()
        for entry in self.roster:
            if not isinstance(entry, dict):
                continue
            node = entry.get('uuid')
            label = node or entry.get('fqdn') or '(unnamed roster entry)'
            if node:
                rostered.add(node)
            if node in self.nodes:
                continue
            is_hypervisor = entry.get('is_hypervisor')
            if is_hypervisor is False:
                found.append((label, ABSENCE_NOT_HYPERVISOR))
            elif is_hypervisor is True:
                found.append((label, ABSENCE_UNEXPLAINED_HYPERVISOR))
            else:
                found.append((label, ABSENCE_UNEXPLAINED_UNKNOWN))

        for node in self.nodes:
            if node not in rostered:
                found.append((node, ABSENCE_NOT_IN_ROSTER))
        return found

    @property
    def cluster_committed_cpu(self):
        return sum(n.committed_cpu for n in self.nodes.values())

    @property
    def cluster_cpu_ledger(self):
        ledgers = [n.cpu_ledger for n in self.nodes.values()
                   if n.cpu_ledger is not None]
        return sum(ledgers) if ledgers else None

    @property
    def cluster_committed_memory_mb(self):
        values = [n.committed_memory_mb for n in self.nodes.values()
                  if n.committed_memory_mb is not None]
        return sum(values) if values else None

    @property
    def cluster_memory_ledger_mb(self):
        values = [n.memory_ledger_mb for n in self.nodes.values()
                  if n.memory_ledger_mb is not None]
        return sum(values) if values else None


class Series:
    """Everything read out of one series file, including what did not parse."""

    def __init__(self):
        self.path = None
        self.read_error = None
        self.samples = []
        self.failed_samples = []
        self.unparseable_lines = 0
        self.unrecognised_lines = 0
        self.total_lines = 0

    @property
    def usable_cpu_samples(self):
        """Samples whose committed CPU figures mean what they say.

        A sample whose ledger was unreadable is excluded rather than
        averaged in: every node's cpu_committed is zero in that state
        whatever the cluster was doing.
        """
        return [s for s in self.samples
                if s.nodes and not s.ledger_unreadable]

    @property
    def ledger_unreadable_samples(self):
        return [s for s in self.samples if s.ledger_unreadable]


def read_series(path):
    """Read a JSONL series, tolerating everything a killed poller leaves.

    A poller which is killed mid-write leaves a truncated final line; a
    sample which failed writes a record carrying 'error' and no 'resources'.
    Neither is an error here, but both are counted, because a series which
    is half error records should not read as a quiet cluster.
    """
    series = Series()
    series.path = path
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError as e:
        series.read_error = str(e)
        return series

    for line in lines:
        line = line.strip()
        if not line:
            continue
        series.total_lines += 1

        try:
            record = json.loads(line)
        except ValueError:
            series.unparseable_lines += 1
            continue
        if not isinstance(record, dict):
            series.unparseable_lines += 1
            continue

        if 'resources' not in record:
            if 'error' in record:
                series.failed_samples.append(str(record.get('error')))
            else:
                series.unrecognised_lines += 1
            continue

        resources = record.get('resources')
        if not isinstance(resources, dict):
            series.unrecognised_lines += 1
            continue

        series.samples.append(Sample(
            numeric(record.get('sampled_at')), resources, record.get('nodes')))

    return series


class StageTally:
    """What one observed stage string did, across the whole census."""

    def __init__(self, stage):
        self.stage = stage
        self.events = 0
        self.aborts = 0
        self.dropped = 0
        self.reasons = collections.Counter()

    @property
    def shortage_drops(self):
        """Drops at this stage which are a shortage rather than missing data."""
        return self.dropped - self.reasons.get(MISSING_DATA_REASON, 0)


class Census:
    """A refusal census, or an honest account of why there isn't one."""

    def __init__(self):
        self.path = None
        self.status = 'not requested'
        self.detail = None
        self.stages = collections.OrderedDict()
        self.records = 0
        self.matched = 0
        self.unparseable_lines = 0

    @property
    def available(self):
        return self.status == 'read'

    def tally(self, stage, aborted, dropped):
        if stage not in self.stages:
            self.stages[stage] = StageTally(stage)
        entry = self.stages[stage]
        entry.events += 1
        if aborted:
            entry.aborts += 1
        if isinstance(dropped, dict):
            for reason in dropped.values():
                entry.dropped += 1
                if isinstance(reason, dict):
                    text = reason.get('reason')
                    entry.reasons[text if isinstance(text, str) else NO_REASON] += 1
                else:
                    entry.reasons[NO_REASON] += 1

    @property
    def capacity_shortage_drops(self):
        return sum(t.shortage_drops for stage, t in self.stages.items()
                   if stage in CAPACITY_STAGE_NOTES)

    @property
    def missing_data_drops(self):
        return sum(t.reasons.get(MISSING_DATA_REASON, 0)
                   for t in self.stages.values())


def stage_of(message):
    """The stage a schedule audit message names, or None if it names none.

    Both forms carry the stage as a bare substring of the message, so it is
    read back out rather than matched against a list of known stages.
    """
    if not isinstance(message, str):
        return None, False
    if message.startswith(STAGE_SURVIVED_PREFIX):
        return message[len(STAGE_SURVIVED_PREFIX):].strip(), False
    if (message.startswith(STAGE_ABORTED_PREFIX)
            and message.endswith(STAGE_ABORTED_SUFFIX)):
        stage = message[len(STAGE_ABORTED_PREFIX):-len(STAGE_ABORTED_SUFFIX)]
        return stage.strip(), True
    return None, False


def read_census(path):
    """Read a Loki query_range response and tally the refusals in it.

    Says explicitly when there is no census. Printing "0 refusals" for a
    census which was never collected, or which failed to parse, is the
    dangerous reading: it looks exactly like a run in which nothing was
    ever refused, which is the finding this whole report exists to make.
    """
    census = Census()
    if path is None:
        return census
    census.path = path

    try:
        with open(path) as f:
            body = f.read()
    except OSError as e:
        census.status = 'unreadable'
        census.detail = str(e)
        return census

    if not body.strip():
        census.status = 'empty'
        census.detail = 'the file exists but is empty'
        return census

    try:
        payload = json.loads(body)
    except ValueError as e:
        census.status = 'unparseable'
        census.detail = 'not valid JSON: %s' % e
        return census

    if not isinstance(payload, dict):
        census.status = 'unexpected shape'
        census.detail = 'the top level is %s, not a JSON object' % type(payload).__name__
        return census

    data = payload.get('data')
    result = data.get('result') if isinstance(data, dict) else None
    if not isinstance(result, list):
        census.status = 'unexpected shape'
        census.detail = 'no data.result list -- this is not a Loki query_range response'
        return census

    census.status = 'read'
    for stream in result:
        if not isinstance(stream, dict):
            continue
        for value in stream.get('values') or []:
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                census.unparseable_lines += 1
                continue
            try:
                record = json.loads(value[1])
            except (ValueError, TypeError):
                census.unparseable_lines += 1
                continue
            if not isinstance(record, dict):
                census.unparseable_lines += 1
                continue

            census.records += 1
            stage, aborted = stage_of(record.get('message'))
            if stage is None:
                continue
            census.matched += 1
            extra = record.get('extra')
            dropped = extra.get('dropped') if isinstance(extra, dict) else None
            census.tally(stage, aborted, dropped)

    return census


def plural(count, singular, plural_form=None):
    return singular if count == 1 else (plural_form or singular + 's')


def fmt(value, places=1):
    if value is None:
        return '-'
    return '%.*f' % (places, value)


def fmt_fraction(value):
    if value is None:
        return '-'
    return '%.3f' % value


def fmt_ledger(values):
    """Render a ledger which may have moved during the run.

    A ledger which changed mid-run is worth seeing rather than averaging:
    the reconciler rewriting a capacity row is exactly the kind of event
    D7's 12-versus-10 discrepancy might turn out to be made of.
    """
    present = [v for v in values if v is not None]
    if not present:
        return '-'
    if min(present) == max(present):
        return fmt(present[0])
    return '%s-%s' % (fmt(min(present)), fmt(max(present)))


def fmt_time(value):
    if value is None:
        return 'unknown'
    try:
        stamp = datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
    except (ValueError, OSError, OverflowError):
        return 'unreadable (%r)' % value
    return stamp.strftime('%Y-%m-%d %H:%M:%SZ')


def print_heading(title):
    print()
    print(title)
    print('-' * len(title))


def print_table(headings, rows, indent='  '):
    """Print a table, first column left aligned and the rest right aligned."""
    if not rows:
        print(indent + '(nothing to report)')
        return
    widths = [len(h) for h in headings]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    header = [headings[0].ljust(widths[0])]
    header.extend(h.rjust(widths[i]) for i, h in enumerate(headings) if i > 0)
    print(indent + ' '.join(header).rstrip())
    for row in rows:
        line = [row[0].ljust(widths[0])]
        line.extend(cell.rjust(widths[i]) for i, cell in enumerate(row) if i > 0)
        print(indent + ' '.join(line).rstrip())


def print_series_summary(series):
    print_heading('Series')
    if series.read_error:
        print('  The series file could not be read: %s' % series.read_error)
        print('  There is no headroom data for this run. This is not the same')
        print('  as a run which used no resources.')
        return

    stamps = [s.sampled_at for s in series.samples if s.sampled_at is not None]
    print('  File:              %s' % series.path)
    print('  Samples:           %d usable, %d failed (an "error" record), '
          '%d unparseable %s'
          % (len(series.samples), len(series.failed_samples),
             series.unparseable_lines,
             plural(series.unparseable_lines, 'line')))
    if series.unrecognised_lines:
        print('  Unrecognised:      %d JSON objects carrying neither '
              '"resources" nor "error"' % series.unrecognised_lines)
    if stamps:
        span = max(stamps) - min(stamps)
        print('  Window:            %s to %s (%.0f seconds)'
              % (fmt_time(min(stamps)), fmt_time(max(stamps)), span))
    else:
        print('  Window:            unknown (no usable timestamps)')

    if series.unparseable_lines:
        print('  A line which does not parse is normally the last one: the')
        print('  poller is killed mid-write when a job is cancelled.')

    if series.failed_samples:
        reasons = collections.Counter(series.failed_samples)
        print('  Failed samples, by error text:')
        for text, count in reasons.most_common(5):
            print('    %4d  %s' % (count, text[:90]))

    unreadable = series.ledger_unreadable_samples
    if unreadable:
        print('  LEDGER UNREADABLE: %d of %d samples had '
              'cpu_committed_row_present false'
              % (len(unreadable), len(series.samples)))
        print('    for every node at once. The capacity read returns an empty')
        print('    map for an unreadable table and for an empty one alike, so')
        print('    it means no counter was visible at all.')
        print('    That is NOT that the cluster was idle.')
        print('    Those samples are excluded from the committed CPU figures')
        print('    below. Memory is unaffected: it comes from node metrics.')


def print_ledger_provenance(series):
    print_heading('Ledger provenance (D7)')
    fallback = 0
    from_row = 0
    missing = 0
    for sample in series.samples:
        for node in sample.nodes.values():
            if node.cpu_ledger is None:
                missing += 1
            elif node.cpu_ledger_from_fallback:
                fallback += 1
            else:
                from_row += 1
    total = fallback + from_row + missing
    print('  Node-samples with a capacity row (cpu_limit):     %d' % from_row)
    print('  Node-samples which fell back to cpu_hard_max:     %d' % fallback)
    print('  Node-samples with no CPU ledger at all:           %d' % missing)
    if not total:
        print('  No node-samples at all, so nothing to say about the ledger.')
        return
    if fallback == total:
        print('  EVERY node-sample fell back. No capacity row was ever visible,')
        print('  so this run cannot speak to the 12-versus-10 discrepancy D7')
        print('  asks phase 2 to reconcile -- but that it saw no row at all is')
        print('  itself a finding to carry forward.')
    elif fallback:
        print('  A mixed run: some node-samples saw a capacity row and some did')
        print('  not, so the two ledgers can be compared directly here.')


def print_cluster_table(series):
    print_heading('Cluster-wide headroom')
    usable = series.usable_cpu_samples
    cpu = [s.cluster_committed_cpu for s in usable]
    cpu_fractions = []
    for sample in usable:
        ledger = sample.cluster_cpu_ledger
        if ledger:
            cpu_fractions.append(sample.cluster_committed_cpu / ledger)

    memory_samples = [s for s in series.samples
                      if s.cluster_committed_memory_mb is not None]
    memory = [s.cluster_committed_memory_mb for s in memory_samples]
    memory_fractions = []
    for sample in memory_samples:
        ledger = sample.cluster_memory_ledger_mb
        if ledger:
            memory_fractions.append(sample.cluster_committed_memory_mb / ledger)

    rows = [
        [
            'committed vCPU',
            str(len(cpu)),
            fmt(percentile(cpu, 0.9)),
            fmt(max(cpu) if cpu else None),
            fmt_ledger([s.cluster_cpu_ledger for s in usable]),
            fmt_fraction(percentile(cpu_fractions, 0.9)),
            fmt_fraction(max(cpu_fractions) if cpu_fractions else None),
        ],
        [
            'committed memory (MB)',
            str(len(memory)),
            fmt(percentile(memory, 0.9)),
            fmt(max(memory) if memory else None),
            fmt_ledger([s.cluster_memory_ledger_mb for s in memory_samples]),
            fmt_fraction(percentile(memory_fractions, 0.9)),
            fmt_fraction(max(memory_fractions) if memory_fractions else None),
        ],
    ]
    print_table(
        ['', 'n', 'p90', 'peak', 'ledger', 'p90 frac', 'peak frac'], rows)
    print('  Fractions are computed per sample and then percentiled, so each')
    print('  one is a ratio something actually stood at.')
    return cpu_fractions


def print_per_node_tables(series):
    cpu_rows = []
    memory_rows = []
    usable = series.usable_cpu_samples

    nodes = []
    for sample in series.samples:
        for node in sample.nodes:
            if node not in nodes:
                nodes.append(node)
    nodes.sort()

    no_memory_ledger = 0
    for node in nodes:
        cpu = []
        cpu_fractions = []
        cpu_ledgers = []
        for sample in usable:
            entry = sample.nodes.get(node)
            if entry is None:
                continue
            cpu.append(entry.committed_cpu)
            cpu_ledgers.append(entry.cpu_ledger)
            if entry.cpu_fraction is not None:
                cpu_fractions.append(entry.cpu_fraction)
        if cpu:
            cpu_rows.append([
                node,
                str(len(cpu)),
                fmt(percentile(cpu, 0.9)),
                fmt(max(cpu)),
                fmt_ledger(cpu_ledgers),
                fmt_fraction(percentile(cpu_fractions, 0.9)),
                fmt_fraction(max(cpu_fractions) if cpu_fractions else None),
            ])

        memory = []
        memory_fractions = []
        memory_ledgers = []
        for sample in series.samples:
            entry = sample.nodes.get(node)
            if entry is None:
                continue
            if entry.memory_ledger_mb is None:
                no_memory_ledger += 1
                continue
            memory.append(entry.committed_memory_mb)
            memory_ledgers.append(entry.memory_ledger_mb)
            if entry.memory_fraction is not None:
                memory_fractions.append(entry.memory_fraction)
        if memory:
            memory_rows.append([
                node,
                str(len(memory)),
                fmt(percentile(memory, 0.9)),
                fmt(max(memory)),
                fmt_ledger(memory_ledgers),
                fmt_fraction(percentile(memory_fractions, 0.9)),
                fmt_fraction(max(memory_fractions) if memory_fractions else None),
            ])

    headings = ['node', 'n', 'p90', 'peak', 'ledger', 'p90 frac', 'peak frac']
    print_heading('Committed vCPU, per node')
    print_table(headings, cpu_rows)
    print_heading('Committed memory (MB), per node')
    print_table(headings, memory_rows)
    if no_memory_ledger:
        print('  %d node-samples published no ram_max and therefore have no'
              % no_memory_ledger)
        print('  memory ledger. They are counted here and divided by nowhere.')


def print_absences(series):
    print_heading('Nodes absent from per_node')
    counts = collections.Counter()
    by_class = collections.defaultdict(set)
    for sample in series.samples:
        for label, classification in sample.absences():
            counts[classification] += 1
            by_class[classification].add(label)

    if not counts:
        print('  Every node in every roster appeared in that sample per_node.')
    else:
        rows = []
        for classification, count in counts.most_common():
            nodes = sorted(by_class[classification])
            shown = ', '.join(nodes[:3])
            if len(nodes) > 3:
                shown += ', ... (%d nodes)' % len(nodes)
            rows.append([classification, str(count), shown])
        print_table(['classification', 'node-samples', 'nodes'], rows)
        print('  summarize_resources() omits a node which is not a hypervisor,')
        print('  whose metrics are over 120s old, or whose queue is over')
        print('  UNREASONABLE_QUEUE_LENGTH, and never says which. Only the')
        print('  first is answerable from the roster, so the rest are reported')
        print('  as unexplained rather than dropped.')

    seen = [len(s.nodes) for s in series.samples]
    if seen:
        print('  Nodes visible in per_node: %d at fewest, %d at most, across '
              '%d samples.' % (min(seen), max(seen), len(seen)))
        print('  That is what the samples could see, which is not the same as')
        print('  how many hypervisors the cluster had.')


def print_census(census):
    print_heading('Refusal census')
    if census.status == 'not requested':
        print('  NO CENSUS WAS SUPPLIED (--census was not given).')
        print('  This is not "no refusals": nothing was looked at. A run whose')
        print('  refusals were never collected and a run which refused nothing')
        print('  are different findings, and this is the first.')
        return
    if not census.available:
        print('  NO CENSUS IS AVAILABLE: %s (%s)' % (census.status, census.detail))
        print('  File: %s' % census.path)
        print('  Read this as "unknown", never as zero refusals. The census')
        print('  depends on the log shipping path being healthy (D11), so a')
        print('  broken shipper looks exactly like a cluster with room to')
        print('  spare unless the difference is said out loud.')
        return

    print('  File:              %s' % census.path)
    print('  Log records read:  %d (%d were schedule stage events, '
          '%d %s unparseable)'
          % (census.records, census.matched, census.unparseable_lines,
             plural(census.unparseable_lines, 'line')))

    if not census.stages:
        print('  No schedule stage events at all. Either nothing was scheduled')
        print('  in the census window, or the query did not match. Both are')
        print('  worth checking before reading this as an idle cluster.')
        return

    rows = []
    for stage, tally in sorted(census.stages.items(),
                               key=lambda kv: (-kv[1].dropped, kv[0])):
        note = CAPACITY_STAGE_NOTES.get(stage)
        if note is None:
            note = 'not a stage this report knows; counted anyway'
        rows.append([
            stage, str(tally.events), str(tally.aborts), str(tally.dropped),
            note])
    print_table(['stage', 'events', 'aborts', 'dropped', 'kind'], rows)
    print('  Tallied by the stage string observed in the events, never by a')
    print('  list held here (D10), so a stage added or renamed in the')
    print('  scheduler still appears above.')

    missing = [name for name in CAPACITY_STAGE_NOTES if name not in census.stages]
    if missing:
        print('  Capacity stages not observed at all in this census: %s'
              % ', '.join(missing))

    print()
    print('  Drop reasons, by stage:')
    for stage, tally in sorted(census.stages.items()):
        if not tally.reasons:
            continue
        print('    %s:' % stage)
        for reason, count in tally.reasons.most_common():
            flag = ''
            if reason == MISSING_DATA_REASON:
                flag = '   <-- MISSING DATA, not a shortage'
            print('      %5d  %s%s' % (count, reason, flag))

    missing_data = census.missing_data_drops
    if missing_data:
        print()
        print('  %d %s carried the reason %r.'
              % (missing_data, plural(missing_data, 'drop'), MISSING_DATA_REASON))
        print('  That is a node whose metrics row had no memory_max, which is')
        print('  missing data rather than a shortage of memory, and it is')
        print('  excluded from every shortage count in this report. Counting it')
        print('  would read a stale metrics row as evidence the cloud is small.')


def print_verdict(cpu_fractions, census, series):
    print_heading('D3 band verdict (PROVISIONAL bounds %.2f / %.2f)'
                  % (BAND_LOWER, BAND_UPPER))
    ratio = percentile(cpu_fractions, 0.9)
    if ratio is None:
        print('  NO VERDICT: no sample produced a committed-vCPU-over-ledger')
        print('  ratio. With %d samples read and %d of them ledger-unreadable,'
              % (len(series.samples), len(series.ledger_unreadable_samples)))
        print('  there is nothing to compare against the band.')
    else:
        print('  p90 committed vCPU / ledger, cluster wide: %s'
              % fmt_fraction(ratio))
        if ratio < BAND_LOWER:
            verdict = ('OVERSIZED -- below the provisional lower bound of %.2f'
                       % BAND_LOWER)
        elif ratio > BAND_UPPER:
            verdict = ('OVERSUBSCRIBED -- above the provisional upper bound of '
                       '%.2f' % BAND_UPPER)
        else:
            verdict = 'WITHIN BAND'
        print('  Verdict: %s' % verdict)

    print('  These bounds are PROVISIONAL. Phase 0 set them without any')
    print('  distribution to check them against, and phase 2 replaces them or')
    print('  defends them. Nothing gates on this verdict: this phase computes')
    print('  and prints the band, and phase 5 owns turning it into a guardrail.')

    print()
    if not census.available:
        print('  Refusal warning: UNKNOWN. Per D3 any capacity-stage refusal is')
        print('  a warning on its own, independent of the ratio above -- but no')
        print('  census was read, so that half of the verdict is missing.')
        return
    shortage = census.capacity_shortage_drops
    if shortage:
        print('  Refusal warning: YES. %d candidate %s at a capacity stage.'
              % (shortage, plural(shortage, 'drop')))
        print('  Per D3 that is a warning in its own right, whatever the ratio')
        print('  says: a poll every fifteen seconds cannot see a refusal, which')
        print('  begins and ends between samples. Note that drops at')
        print('  sufficient_idle_disk are disk BANDWIDTH, a rate predicate no')
        print('  amount of extra hardware in the same shape would fix.')
    else:
        print('  Refusal warning: no capacity-stage drops in the census window.')


def report(args):
    title = 'Shaken Fist CI headroom report'
    print(title)
    print('=' * len(title))
    if args.label:
        print('Label:  %s' % args.label)

    series = read_series(args.series)
    census = read_census(args.census)

    print_series_summary(series)
    if series.samples:
        print_ledger_provenance(series)
        cpu_fractions = print_cluster_table(series)
        print_per_node_tables(series)
        print_absences(series)
    else:
        cpu_fractions = []
        print()
        print('  No usable samples, so there is no headroom to report. That is')
        print('  a fact about the instrument, not about the cluster.')

    print_census(census)
    print_verdict(cpu_fractions, census, series)
    print()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=('Summarise a CI headroom series, and optionally a refusal '
                     'census, for one cluster job.'))
    parser.add_argument(
        '--series', required=True,
        help='Path to the JSONL series written by tools/ci_headroom_probe.py.')
    parser.add_argument(
        '--census', default=None,
        help=('Path to a Loki query_range response holding the run scheduler '
              'stage events. Optional: the report says so explicitly when it '
              'is absent, rather than printing zero refusals.'))
    parser.add_argument(
        '--label', default=None,
        help='A label for the run, printed at the top (typically the topology).')

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # argparse exits 2 on a usage error and 0 on --help. Neither may
        # fail the job: D15 says nothing this phase adds can, and a report
        # tool which fails a build over its own arguments is precisely the
        # instrument changing what it measures.
        return 0

    try:
        report(args)
    except Exception:
        print('The headroom report failed to render:')
        traceback.print_exc(file=sys.stdout)
        print('Reported rather than raised: this phase gates nothing and an')
        print('instrument which can fail a job changes what it measures (D15).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
