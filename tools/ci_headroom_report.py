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

The same reasoning adds a third section rather than widening the second.
The stage census reads the scheduler's stage events and stops above the
capacity guard, so a run in which every stage passed and the guard then
refused every candidate reads here as a clean run with no refusals --
which is precisely the shape of issue 3772. The guard census counts
'instance placement denied' (instance.py) by failing_stage and by which
dimension was exceeded, under its own heading, because a stage drop and a
guard denial are events about different things: a stage drop removed one
candidate node from a list which may still have had others, a denial is
the ledger refusing the write. A number made by adding them answers
neither question.

For the same reason 'placement admitted over namespace capacity claim' is
counted third and never as a refusal. Advisory claim mode admits that
placement deliberately -- CLAIM_ENFORCEMENT_HARD is False for a release
precisely so exceedances are observed before they are refused -- so it is
the system doing what the operator asked, and it is reported because it
is the calibration signal D9 asks for, not because anything went wrong.
Counting it as a refusal would manufacture refusals on a healthy cluster.

Like the stage census, both new tallies count every string they observe
rather than a list held here: a failing_stage or a dimension name this
tool has never been told about is counted, printed and flagged as
unrecognised, never dropped.

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
* **Ledger unreadable is not idle.** A failed capacity read makes every
  cpu_committed zero and every cpu_committed_row_present false at once, which
  from the payload alone looks exactly like a cluster doing nothing. The
  server now tells the two apart -- mariadb.get_scheduler_node_capacity()
  returns rows and a separate degraded flag, the database daemon forwards its
  own read failure on that flag, and the scheduler events "schedule could not
  read the capacity counters" when it is set -- but the sampled series this
  report reads carries no such flag, so from here an unreadable table and one
  the reconciler has never populated are still indistinguishable. A sample in
  that state is therefore excluded from the committed CPU figures and
  reported rather than averaged in; the scheduler event is where the
  difference is visible, and both states are worth knowing whichever it was.
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

One record, two consumers (D18). Everything printed below is computed once,
into the plain dict summary_record() returns, and the prose is rendered from
that dict rather than from the parsed objects beside it. Phase 2's harvest
runs this tool's summary_record() over several hundred banked bundles and
phase 5's guardrail will read the --json file, so the alternative -- both of
them parsing the printed tables -- would make every future change to a
heading or a column width a silent break of the dataset. Rendering the prose
from the record instead means the number in the job log and the number in
the dataset cannot disagree, which matters here because the whole plan turns
on people trusting these figures. It also means this file has exactly one
place where each figure is computed, so a reader checking the arithmetic
checks it once.

The record carries one statistic the prose does not print: the per-node
maximum committed fraction (D21). For each sample it is the largest
committed-over-ledger ratio any single node stood at, percentiled across the
run beside the cluster-wide figure the band verdict uses. It exists because
a cluster-wide fraction averages a full node against an empty one: a real
merge run sat at a cluster-wide p90 of 0.407 while one node was pinned at
1.000 for its whole duration and the scheduler refused twelve candidates at
sufficient_idle_cpu. The scheduler does not admit against an average, so the
average is the wrong number to size a cloud against. It is recorded rather
than printed only because this step is bound to leave the printed report
byte-identical, and D21's bounds do not exist until phase 2's harvest sets
them; the phase which sets them is the phase which should print it.

The record is a plain dict of standard-library types for the same reason the
rest of this file is: it has to serialise under stock python3 on a runner.
And writing it obeys D15 like everything else -- a --json path which cannot
be written is a warning on stdout and an exit code of zero, never an
exception, because a report tool which fails a build over its own output
file is precisely the instrument changing what it measures.

Usage:

    python3 tools/ci_headroom_report.py --series /srv/ci/traces/headroom.jsonl \\
        --census /srv/ci/traces/headroom-census.json --label slim-primary \\
        --json /srv/ci/traces/headroom-summary.json
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

# The shape of the dict summary_record() returns. Phase 2's harvest and phase
# 5's guardrail both read it from files written by builds older than
# themselves, so it is versioned: a consumer which cannot read version 2 can
# say so rather than silently misreading a renamed field as an absent one.
RECORD_VERSION = 1

# Loki's own max_entries_limit_per_query, and the value
# tools/ci_headroom_collect.sh in shakenfist/actions issues its query with. A
# response holding exactly this many entries may have been cut short, which
# is the one truncation Loki gives no signal for.
DEFAULT_CENSUS_LIMIT = 5000

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

# The capacity guard's own two audit messages, below the stage layer the
# constants above match. Matched on the message for the same reason the
# stage events are: the shipped JSON's 'message' is the event's message,
# because pylogrus merges the caller's fields over the record last.
#
# The two are read in one pass over the census file and tallied in two
# separate places, which is the whole point: 'instance placement denied' is
# the ledger refusing a write, and 'placement admitted over namespace
# capacity claim' is a placement which was ADMITTED, over an advisory claim
# the operator set. The second is never added to the first.
GUARD_DENIED_MESSAGE = 'instance placement denied'
CLAIM_OVER_LIMIT_MESSAGE = 'placement admitted over namespace capacity claim'

# The stages _direct_admit_instance_placement() can fail at, used only to
# annotate rows -- never to filter what is tallied. An unrecognised stage
# is counted and flagged, exactly as an unrecognised scheduler stage is.
GUARD_STAGE_NOTES = collections.OrderedDict([
    ('node', "the node's own capacity counters"),
    ('cluster', 'the cluster-wide capacity singleton'),
    ('claim', "the namespace's capacity claim"),
])

# Likewise for dimensions. 'demand' is called out because it is the one
# dimension which is not a count of anything allocated: it is the D13
# feedforward estimate added to measured CPU load, so a refusal on demand
# alone is a rate prediction and not a cloud which ran out of room.
GUARD_DIMENSION_NOTES = collections.OrderedDict([
    ('cpus', 'allocated vCPU'),
    ('memory_mb', 'allocated memory'),
    ('disk_gb', 'allocated disk'),
    ('demand', 'measured CPU load plus the D13 feedforward estimate -- '
               'NOT an allocation'),
])

UNKNOWN_STAGE = '(no failing_stage recorded)'
UNKNOWN_DIMENSION = '(unnamed dimension)'
NO_DIMENSIONS = '(no dimensions recorded)'
UNKNOWN_NAMESPACE = '(no namespace recorded)'

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
        # which is a read failure wearing an idle cluster's clothes. The
        # inference needs more than one node to carry that meaning: on a
        # single-hypervisor topology one node which simply has no capacity
        # row yet satisfies "all false" while being an ordinary per-node
        # fact, and reading it as a failed read discards the entire CPU
        # series -- on slim-primary, the first topology this phase's
        # definition of done names.
        flags = [n.row_present for n in self.nodes.values()]
        all_absent = bool(flags) and all(f is False for f in flags)
        self.ledger_unreadable = all_absent and len(flags) > 1
        self.sole_node_without_row = all_absent and len(flags) == 1

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
        """Committed vCPU over every node, ledgered or not.

        The honest cluster total, and what the committed-vCPU column
        reports. Deliberately not the fraction's numerator: see
        cluster_cpu_fraction.
        """
        return sum(n.committed_cpu for n in self.nodes.values())

    @property
    def cluster_cpu_ledger(self):
        ledgers = [n.cpu_ledger for n in self.nodes.values()
                   if n.cpu_ledger is not None]
        return sum(ledgers) if ledgers else None

    @property
    def unledgered_nodes(self):
        return [n for n in self.nodes.values() if n.cpu_ledger is None]

    @property
    def cluster_cpu_fraction(self):
        """Committed vCPU over ledger, both summed over the same nodes.

        A node publishing neither cpu_limit nor cpu_hard_max has committed
        vCPU but no ledger. Counting it in the numerator while the
        denominator skips it yields ratios above 1.0 -- an arithmetically
        impossible number which prints as OVERSUBSCRIBED, that is, as a
        recommendation to grow the cloud on the strength of one absent
        field. Both sides are therefore summed over the ledgered nodes,
        and the rest are reported by print_cluster_table rather than
        folded in. Memory never had this problem: NodeSample sets
        memory_ledger_mb and committed_memory_mb together or not at all.
        """
        paired = [n for n in self.nodes.values() if n.cpu_ledger is not None]
        ledger = sum(n.cpu_ledger for n in paired)
        if not ledger:
            return None
        return sum(n.committed_cpu for n in paired) / ledger

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


class GuardCensus:
    """What the capacity guard refused, counted apart from the stage census.

    Read out of the same file in the same pass, and kept in its own object
    so that no count here can be added to a stage count by accident. The
    two measure different events: a stage drop removed one candidate node
    from a list which may still have had others and is often a healthy
    run's normal noise, while a denial is the ledger refusing the write
    for a node the scheduler had already chosen.

    The claim tally is a third thing again, and the one most easily
    misread: a claim exceedance is an ADMITTED placement. Advisory mode
    exists so exceedances are observed before they are refused, so it is
    never a refusal and never enters ``denials``.
    """

    def __init__(self):
        self.denials = 0
        self.malformed = 0

        # Every tally below is keyed by the string observed in the event,
        # never by a list held in this file, so a stage or a dimension
        # added to the guard after this tool was written is counted and
        # printed rather than dropped.
        self.stages = collections.Counter()
        self.exceeded = collections.Counter()
        self.sole_exceedance = collections.Counter()
        self.stage_dimensions = collections.defaultdict(collections.Counter)
        self.unenforced = 0
        self.empty_dimensions = 0
        self.nothing_exceeded = 0

        # The demand dimension carries the two terms whose sum is `used`
        # (issue 3913), which is the difference between a node that was
        # genuinely busy and an estimator that thought it would be.
        self.demand_measured_alone = 0
        self.demand_estimate_tipped = 0
        self.demand_unsplit = 0

        # Populated only where the event carries a shortfall. The value is
        # deliberately not computed here when it is absent: G3 puts the
        # definition of shortfall in one place, server side, so that two
        # consumers cannot disagree about its sign convention.
        #
        # Two dicts, for the same reason the rest of this class keeps the
        # claim tallies apart from the refusal ones: a claim exceedance is
        # an admitted placement, so its shortfall says how far past a
        # declared footprint a namespace went, not how far short of the
        # ledger a refused create fell. One dict would print the second
        # number under the first heading.
        self.shortfalls = {}
        self.claim_shortfalls = {}

        self.claims = 0
        self.claim_malformed = 0
        self.claim_namespaces = collections.Counter()
        self.claim_exceeded = collections.Counter()

    @property
    def observed(self):
        """Whether this census carried any guard event at all.

        Zero guard events in a census which did carry stage events is a
        statement about the query, not about the cluster: the collector's
        LogQL filter selects the stage messages only. The report says so
        rather than printing a zero which reads as "nothing was refused".
        """
        return bool(self.denials or self.claims or self.malformed
                    or self.claim_malformed)

    @property
    def unrecognised_stages(self):
        # The placeholders this file writes for a missing value are not
        # unrecognised stages: they are this tool saying the event did not
        # carry one, and they are already flagged in the table.
        return sorted(s for s in self.stages
                      if s not in GUARD_STAGE_NOTES and s != UNKNOWN_STAGE)

    @property
    def unrecognised_dimensions(self):
        names = set(self.exceeded) | set(self.claim_exceeded)
        return sorted(n for n in names
                      if n not in GUARD_DIMENSION_NOTES and n != UNKNOWN_DIMENSION)

    def _dimensions_of(self, extra, key):
        """The exceeded dimension names in one event, and their details.

        Returns (names, details, well_formed). Anything which is not the
        shape this tool expects makes well_formed False and is counted as
        malformed by the caller, rather than being silently read as an
        event with no exceeded dimensions -- which would look exactly like
        a denial nobody can explain.
        """
        if not isinstance(extra, dict):
            return [], [], False
        dimensions = extra.get(key)
        if dimensions is None:
            return [], [], False
        if not isinstance(dimensions, list):
            return [], [], False

        names = []
        details = []
        for entry in dimensions:
            if not isinstance(entry, dict):
                continue
            details.append(entry)
            if entry.get('exceeded') is not True:
                continue
            name = entry.get('dimension')
            names.append(name if isinstance(name, str) and name
                         else UNKNOWN_DIMENSION)
        return names, details, True

    def _note_shortfall(self, into, entry, name):
        shortfall = numeric(entry.get('shortfall'))
        if shortfall is None:
            return
        if name not in into or shortfall > into[name]:
            into[name] = shortfall

    def _note_demand_split(self, entry):
        """Which term of the demand dimension carried it past the limit.

        The demand clause is the one dimension which does not charge the
        incoming placement (phase 4a): it compares cpu_load_1 plus
        expected_demand against the limit and leaves `requested` out of
        the comparison entirely, which is what makes it satisfiable at
        every node size. So the split is read the same way the guard
        made it -- measured load alone already exceeds the limit, or the
        D13 feedforward estimate is what carried the sum over it. The
        second is an estimator finding rather than a cluster which ran
        out of CPU, and issue 3913 added the two terms to the event for
        exactly this reading. `requested` is deliberately not used here,
        because using it would report a comparison the guard never made.
        """
        load = numeric(entry.get('cpu_load_1'))
        estimate = numeric(entry.get('expected_demand'))
        limit = numeric(entry.get('limit'))
        if load is None or estimate is None or limit is None:
            self.demand_unsplit += 1
            return
        if load > limit:
            self.demand_measured_alone += 1
        else:
            self.demand_estimate_tipped += 1

    def observe_denial(self, extra):
        self.denials += 1
        stage = extra.get('failing_stage') if isinstance(extra, dict) else None
        stage_label = stage if isinstance(stage, str) and stage else UNKNOWN_STAGE
        self.stages[stage_label] += 1

        if isinstance(extra, dict) and extra.get('enforce') is False:
            # A ground-truth writer's denial, which does not refuse a
            # create: the cleaner and the startup reconciliation record
            # where a domain already is. Counted apart so it cannot be
            # read as a user-visible refusal.
            self.unenforced += 1

        names, details, well_formed = self._dimensions_of(extra, 'dimensions')
        if not well_formed:
            self.malformed += 1
            return

        for entry in details:
            if entry.get('exceeded') is not True:
                continue
            name = entry.get('dimension')
            name = name if isinstance(name, str) and name else UNKNOWN_DIMENSION
            self._note_shortfall(self.shortfalls, entry, name)
            if name == 'demand':
                self._note_demand_split(entry)

        if not details:
            # A dimensions list this tool could read, which was empty. A
            # different fact from the one below -- there was nothing to
            # mark exceeded, rather than something which was not marked --
            # and a different fact again from malformed, which is a shape
            # this tool did not recognise at all.
            self.empty_dimensions += 1
        if not names:
            # The guard refused and marked nothing exceeded. Worth seeing:
            # it is either a guard this tool does not understand or an
            # event shape which has moved.
            self.nothing_exceeded += 1
            self.stage_dimensions[stage_label][NO_DIMENSIONS] += 1
            return

        for name in set(names):
            self.exceeded[name] += 1
            self.stage_dimensions[stage_label][name] += 1
        if len(set(names)) == 1:
            self.sole_exceedance[names[0]] += 1

    def observe_claim(self, extra):
        self.claims += 1
        namespace = extra.get('namespace') if isinstance(extra, dict) else None
        self.claim_namespaces[
            namespace if isinstance(namespace, str) and namespace
            else UNKNOWN_NAMESPACE] += 1

        names, details, well_formed = self._dimensions_of(
            extra, 'claim_dimensions')
        if not well_formed:
            self.claim_malformed += 1
            return
        for entry in details:
            if entry.get('exceeded') is not True:
                continue
            name = entry.get('dimension')
            self._note_shortfall(
                self.claim_shortfalls, entry,
                name if isinstance(name, str) and name
                else UNKNOWN_DIMENSION)
        for name in set(names):
            self.claim_exceeded[name] += 1

    def observe(self, message, extra):
        """Tally one record if it is a guard event. Returns whether it was."""
        if message == GUARD_DENIED_MESSAGE:
            self.observe_denial(extra)
            return True
        if message == CLAIM_OVER_LIMIT_MESSAGE:
            self.observe_claim(extra)
            return True
        return False


class Census:
    """A refusal census, or an honest account of why there isn't one."""

    def __init__(self):
        self.path = None
        self.status = 'not requested'
        self.detail = None
        self.stages = collections.OrderedDict()
        self.records = 0
        self.matched = 0
        self.guard_matched = 0
        self.guard = GuardCensus()
        self.unparseable_lines = 0
        self.limit = None

    @property
    def available(self):
        return self.status == 'read'

    @property
    def truncated(self):
        """True when the query returned every entry it was allowed to.

        Loki caps a query_range at max_entries_limit_per_query (5000 by
        default) and a response holding exactly the limit is
        indistinguishable from a complete one. The scheduler emits an
        event per stage per schedule, so a run creating a few hundred
        instances can reach it -- and a census cut short reads as "the
        cloud had room", which is the one misreading this tool exists to
        prevent.
        """
        return self.limit is not None and self.records >= self.limit

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
    def unclassified_shortage_drops(self):
        """Shortage drops at stages CAPACITY_STAGE_NOTES does not name.

        Per D10 the census tallies every stage string it sees, so a stage
        added to the scheduler after this tool was written is counted and
        printed. It cannot be counted into the capacity warning, though,
        because nothing here knows whether it is a capacity stage. The
        verdict names the total rather than staying silent, so a reader
        is not told "no capacity-stage drops" while the table above shows
        drops.
        """
        return sum(t.shortage_drops for stage, t in self.stages.items()
                   if stage not in CAPACITY_STAGE_NOTES)

    @property
    def disk_bandwidth_drops(self):
        tally = self.stages.get('sufficient_idle_disk')
        return tally.shortage_drops if tally else 0

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


def read_census(path, limit=None):
    """Read a Loki query_range response and tally the refusals in it.

    Says explicitly when there is no census. Printing "0 refusals" for a
    census which was never collected, or which failed to parse, is the
    dangerous reading: it looks exactly like a run in which nothing was
    ever refused, which is the finding this whole report exists to make.
    """
    census = Census()
    census.limit = limit
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
            message = record.get('message')
            stage, aborted = stage_of(message)
            if stage is None:
                # Below the stage layer: the guard's own events, tallied
                # into their own object so nothing here can end up added
                # to a stage count.
                if census.guard.observe(message, record.get('extra')):
                    census.guard_matched += 1
                continue
            census.matched += 1
            extra = record.get('extra')
            dropped = extra.get('dropped') if isinstance(extra, dict) else None
            census.tally(stage, aborted, dropped)

    return census


# The summary record (D18). Everything the report prints is computed here,
# once, into a plain dict; the printers below render prose from that dict and
# compute nothing of their own. Phase 2's harvest calls summary_record()
# directly over several hundred banked bundles rather than shelling out to
# this file, and phase 5's guardrail reads the --json file it writes.
#
# Two conventions run through all of it, and both exist to stop a reader --
# human or program -- inferring a fact the measurement does not support.
#
# A figure which was not measured is None, never zero. An absent census is
# the case this matters most for: "we did not look" and "nothing was
# refused" are different findings and the second is the one the whole plan is
# hunting for, so a census which was never collected leaves every count under
# it null. The same applies to a ledger which was never visible and to a
# percentile over an empty list.
#
# A ledger is recorded as the range it moved over rather than as an average,
# because the reconciler rewriting a capacity row mid-run is exactly the kind
# of event D7's 12-versus-10 discrepancy might turn out to be made of, and an
# average would erase it.


def ledger_bounds(values):
    """The lowest and highest ledger observed, ignoring the ones which were not."""
    present = [v for v in values if v is not None]
    if not present:
        return None, None
    return min(present), max(present)


def metric_block(values, fractions, ledgers):
    """One measured quantity's block of the record.

    The count, p90 and peak of the quantity itself; the ledger it was
    measured against as a range; and the p90 and peak of the fraction of
    that ledger. The fractions are computed per sample and percentiled
    afterwards, never derived by dividing one percentile by another -- a
    ratio of two percentiles is a number nothing ever stood at, and the
    printed table says as much.
    """
    low, high = ledger_bounds(ledgers)
    return collections.OrderedDict([
        ('n', len(values)),
        ('p90', percentile(values, 0.9)),
        ('peak', max(values) if values else None),
        ('ledger_min', low),
        ('ledger_max', high),
        ('p90_fraction', percentile(fractions, 0.9)),
        ('peak_fraction', max(fractions) if fractions else None),
    ])


def cluster_cpu_fractions(series):
    """The per-sample committed-over-ledger ratios the verdict is computed from.

    Named rather than inlined because it is the one load-bearing input to
    the band verdict, and because computing it must not require having
    printed anything first.
    """
    fractions = []
    for sample in series.usable_cpu_samples:
        fraction = sample.cluster_cpu_fraction
        if fraction is not None:
            fractions.append(fraction)
    return fractions


def per_node_max_cpu_fractions(series):
    """Per sample, the highest committed-over-ledger ratio any one node stood at.

    D21. The cluster-wide fraction sums both sides over every ledgered node,
    so it averages a full node against an empty one and reports the mean as
    headroom -- on one real merge run, a cluster-wide p90 of 0.407 while a
    node sat pinned at 1.000 for the whole run and twelve candidates were
    refused at sufficient_idle_cpu. The scheduler admits against one node's
    ledger at a time and never against the average, so this is the series a
    per-node band bound is set from.

    A node with no ledger contributes nothing here, exactly as it
    contributes to neither side of the cluster-wide fraction: it has
    committed vCPU and nothing to divide it by.
    """
    maxima = []
    for sample in series.usable_cpu_samples:
        fractions = [n.cpu_fraction for n in sample.nodes.values()
                     if n.cpu_fraction is not None]
        if fractions:
            maxima.append(max(fractions))
    return maxima


def series_record(series):
    """What was read out of the series file, and what could not be."""
    stamps = [s.sampled_at for s in series.samples if s.sampled_at is not None]
    reasons = collections.Counter(series.failed_samples)
    record = collections.OrderedDict()
    record['path'] = series.path
    record['read_error'] = series.read_error
    record['samples_usable'] = len(series.samples)
    record['samples_failed'] = len(series.failed_samples)
    record['unparseable_lines'] = series.unparseable_lines
    record['unrecognised_lines'] = series.unrecognised_lines
    record['total_lines'] = series.total_lines
    # In most_common() order so a consumer and the printed report list the
    # same errors first without either having to sort.
    record['failed_sample_reasons'] = collections.OrderedDict(
        reasons.most_common())
    record['window_start'] = min(stamps) if stamps else None
    record['window_end'] = max(stamps) if stamps else None
    record['window_seconds'] = (max(stamps) - min(stamps)) if stamps else None
    record['ledger_unreadable_samples'] = len(series.ledger_unreadable_samples)
    record['sole_node_without_row_samples'] = sum(
        1 for s in series.samples if s.sole_node_without_row)
    return record


def ledger_provenance_record(series):
    """Where each node-sample's ledger came from (D7).

    The count which fell back from the capacity row's cpu_limit to the
    derived cpu_hard_max is a deliverable rather than a diagnostic aside:
    D7 asks phase 2 to reconcile the two ledgers, and a run which is
    entirely fallback answers that question differently from one which is
    not. Fallbacks inside a ledger-unreadable sample are counted apart
    because they are a failed capacity read wearing the fallback's clothes,
    and D7 must read the second figure alone.

    The memory-ledger count rides along here rather than in the per-node
    block it is printed beside, because it is the same kind of fact about
    the same node-samples: how many of them had no ledger to divide by.
    """
    record = collections.OrderedDict([
        ('node_samples_with_row', 0),
        ('node_samples_fallback', 0),
        ('node_samples_fallback_unreadable', 0),
        ('node_samples_without_ledger', 0),
        ('node_samples_total', 0),
        ('node_samples_without_memory_ledger', 0),
    ])
    for sample in series.samples:
        unreadable = sample.ledger_unreadable
        for node in sample.nodes.values():
            if node.cpu_ledger is None:
                record['node_samples_without_ledger'] += 1
            elif node.cpu_ledger_from_fallback:
                if unreadable:
                    record['node_samples_fallback_unreadable'] += 1
                else:
                    record['node_samples_fallback'] += 1
            else:
                record['node_samples_with_row'] += 1
            if node.memory_ledger_mb is None:
                record['node_samples_without_memory_ledger'] += 1
    record['node_samples_total'] = (
        record['node_samples_with_row'] + record['node_samples_fallback']
        + record['node_samples_fallback_unreadable']
        + record['node_samples_without_ledger'])
    return record


def cluster_record(series):
    """Committed vCPU and memory summed over the cluster, per sample.

    The CPU figures are computed over the usable samples only: a sample
    whose capacity read failed publishes zero committed vCPU on every node
    at once, which averages in as an idle cluster. Memory comes from node
    metrics and is unaffected, so it is computed over every sample.
    """
    usable = series.usable_cpu_samples
    cpu = [s.cluster_committed_cpu for s in usable]
    cpu_ledgers = [s.cluster_cpu_ledger for s in usable]

    memory_samples = [s for s in series.samples
                      if s.cluster_committed_memory_mb is not None]
    memory = [s.cluster_committed_memory_mb for s in memory_samples]
    memory_ledgers = [s.cluster_memory_ledger_mb for s in memory_samples]
    memory_fractions = []
    for sample in memory_samples:
        ledger = sample.cluster_memory_ledger_mb
        if ledger:
            memory_fractions.append(sample.cluster_committed_memory_mb / ledger)

    record = collections.OrderedDict()
    record['committed_cpu'] = metric_block(
        cpu, cluster_cpu_fractions(series), cpu_ledgers)
    record['committed_memory_mb'] = metric_block(
        memory, memory_fractions, memory_ledgers)
    # Reported rather than folded in: a node with committed vCPU and no
    # ledger at all is in neither side of the fraction above, and a reader
    # who is not told that would read the fraction as covering the cluster.
    record['nodes_without_cpu_ledger'] = sorted(
        {n.node for s in usable for n in s.unledgered_nodes})
    return record


def per_node_record(series):
    """The same figures again, per node, keyed by node uuid.

    This is the block D21 exists because of, and the one a reader goes to
    when the cluster-wide fraction looks comfortable. Each node carries its
    own ledger, so "0.5 of what" is answerable without joining anything.
    """
    nodes = []
    for sample in series.samples:
        for node in sample.nodes:
            if node not in nodes:
                nodes.append(node)
    nodes.sort()

    usable = series.usable_cpu_samples
    record = collections.OrderedDict()
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

        memory = []
        memory_fractions = []
        memory_ledgers = []
        for sample in series.samples:
            entry = sample.nodes.get(node)
            if entry is None or entry.memory_ledger_mb is None:
                continue
            memory.append(entry.committed_memory_mb)
            memory_ledgers.append(entry.memory_ledger_mb)
            if entry.memory_fraction is not None:
                memory_fractions.append(entry.memory_fraction)

        record[node] = collections.OrderedDict([
            ('committed_cpu', metric_block(cpu, cpu_fractions, cpu_ledgers)),
            ('committed_memory_mb',
             metric_block(memory, memory_fractions, memory_ledgers)),
        ])
    return record


def absence_record(series):
    """Which rostered nodes per_node did not carry, and how far that is explained.

    Nothing is dropped: what the roster cannot explain is recorded as
    unexplained, because the alternative is inferring a cluster's size from
    a sample which merely could not see the rest of it.
    """
    counts = collections.Counter()
    by_class = collections.defaultdict(set)
    for sample in series.samples:
        for label, classification in sample.absences():
            counts[classification] += 1
            by_class[classification].add(label)

    classifications = collections.OrderedDict()
    for classification, count in counts.most_common():
        classifications[classification] = collections.OrderedDict([
            ('node_samples', count),
            ('nodes', sorted(by_class[classification])),
        ])

    seen = [len(s.nodes) for s in series.samples]
    return collections.OrderedDict([
        ('classifications', classifications),
        ('samples', len(seen)),
        ('nodes_visible_min', min(seen) if seen else None),
        ('nodes_visible_max', max(seen) if seen else None),
    ])


def census_record(census):
    """The stage census, or an honest account of why there isn't one.

    Every count is null unless the census was actually read. A census which
    was never collected, or which failed to parse, must not read as a run in
    which nothing was refused -- that is the finding this whole report
    exists to make, and printing or recording a zero for it is how it would
    be made falsely.
    """
    record = collections.OrderedDict()
    record['state'] = census.status
    record['available'] = census.available
    record['detail'] = census.detail
    record['path'] = census.path
    record['limit'] = census.limit
    if not census.available:
        for key in ('records', 'stage_events', 'guard_events',
                    'unparseable_lines', 'truncated', 'stages',
                    'capacity_shortage_drops', 'unclassified_shortage_drops',
                    'disk_bandwidth_drops', 'missing_data_drops'):
            record[key] = None
        return record

    record['records'] = census.records
    record['stage_events'] = census.matched
    record['guard_events'] = census.guard_matched
    record['unparseable_lines'] = census.unparseable_lines
    record['truncated'] = census.truncated

    stages = collections.OrderedDict()
    for stage in sorted(census.stages):
        tally = census.stages[stage]
        stages[stage] = collections.OrderedDict([
            ('events', tally.events),
            ('aborts', tally.aborts),
            ('dropped', tally.dropped),
            ('shortage_drops', tally.shortage_drops),
            ('reasons', collections.OrderedDict(tally.reasons.most_common())),
        ])
    record['stages'] = stages

    record['capacity_shortage_drops'] = census.capacity_shortage_drops
    record['unclassified_shortage_drops'] = census.unclassified_shortage_drops
    record['disk_bandwidth_drops'] = census.disk_bandwidth_drops
    record['missing_data_drops'] = census.missing_data_drops
    return record


def guard_record(census):
    """What the capacity guard did, in its own block and never added to the above.

    ``state`` is the field a consumer must read first, and it has four
    values because there are four different things to know. ``no_census``
    and ``census_unavailable`` mean nothing was looked at.
    ``not_collected`` means the census was read and carried no guard event
    at all, which -- until D20 widens the collector's LogQL filter -- is a
    statement about the query rather than about the cluster, since the
    filter selects only the scheduler's stage messages. Only ``collected``
    carries counts, and in every other state they are null: a guard census
    nobody collected must never be reported as zero refusals (D20).
    """
    guard = census.guard
    if census.status == 'not requested':
        state = 'no_census'
    elif not census.available:
        state = 'census_unavailable'
    elif not guard.observed:
        state = 'not_collected'
    else:
        state = 'collected'

    record = collections.OrderedDict()
    record['state'] = state
    if state != 'collected':
        record['denials'] = None
        record['claims'] = None
        return record

    record['denials'] = guard.denials
    record['unenforced'] = guard.unenforced
    record['malformed'] = guard.malformed
    record['empty_dimensions'] = guard.empty_dimensions
    record['nothing_exceeded'] = guard.nothing_exceeded
    record['stages'] = collections.OrderedDict(sorted(guard.stages.items()))
    record['exceeded'] = collections.OrderedDict(sorted(guard.exceeded.items()))
    record['sole_exceedance'] = collections.OrderedDict(
        sorted(guard.sole_exceedance.items()))
    record['stage_dimensions'] = collections.OrderedDict(
        (stage, collections.OrderedDict(sorted(names.items())))
        for stage, names in sorted(guard.stage_dimensions.items()))
    record['demand_measured_alone'] = guard.demand_measured_alone
    record['demand_estimate_tipped'] = guard.demand_estimate_tipped
    record['demand_unsplit'] = guard.demand_unsplit
    # Recorded where the event reported one and absent where it did not. A
    # dimension with no shortfall field is a build predating it, not a
    # shortfall of zero, and G3 keeps the definition server side so that two
    # consumers cannot disagree about its sign.
    record['shortfalls'] = collections.OrderedDict(sorted(guard.shortfalls.items()))
    record['unrecognised_stages'] = guard.unrecognised_stages
    record['unrecognised_dimensions'] = guard.unrecognised_dimensions

    # An admitted placement, never a refusal: advisory claim mode exists so
    # exceedances are observed before they are refused.
    record['claims'] = guard.claims
    record['claim_malformed'] = guard.claim_malformed
    record['claim_namespaces'] = collections.OrderedDict(
        guard.claim_namespaces.most_common())
    record['claim_exceeded'] = collections.OrderedDict(
        sorted(guard.claim_exceeded.items()))
    record['claim_shortfalls'] = collections.OrderedDict(
        sorted(guard.claim_shortfalls.items()))
    return record


def verdict_record(record):
    """D3's band verdict, and the numbers it was reached from.

    Every figure here is taken from the blocks already built above rather
    than recomputed, so the verdict cannot disagree with the table a reader
    checks it against.

    ``refusal_warning`` is deliberately tri-state. Per D3 any capacity-stage
    refusal is a warning on its own, independent of the ratio -- but a
    census which was never read cannot say there were none, so it is null
    rather than False.

    The per-node maximum (D21) is carried unjudged. Its bounds do not exist
    yet: phase 2's harvest sets them from the distribution, and the
    provisional 0.35/0.70 below are bounds on the cluster-wide figure only.
    """
    ratio = record['cluster']['committed_cpu']['p90_fraction']
    if ratio is None:
        band = None
    elif ratio < BAND_LOWER:
        band = 'OVERSIZED'
    elif ratio > BAND_UPPER:
        band = 'OVERSUBSCRIBED'
    else:
        band = 'WITHIN BAND'

    shortage = record['census']['capacity_shortage_drops']
    per_node_max = record['per_node_max_cpu_fraction']
    return collections.OrderedDict([
        ('p90_cpu_fraction', ratio),
        ('band', band),
        ('band_lower', BAND_LOWER),
        ('band_upper', BAND_UPPER),
        ('band_provisional', True),
        ('refusal_warning', None if shortage is None else bool(shortage)),
        ('per_node_max_p90_fraction', per_node_max['p90']),
        ('per_node_max_peak_fraction', per_node_max['peak']),
        ('per_node_band', None),
    ])


def build_summary_record(series, census, label=None):
    """Assemble the record from an already-parsed series and census.

    Separate from summary_record() below so that a caller which already
    holds the parsed objects -- a test, or a consumer reading a series from
    somewhere other than a file -- does not have to write them back out to
    disk to summarise them.
    """
    record = collections.OrderedDict()
    record['record_version'] = RECORD_VERSION
    record['label'] = label
    record['series'] = series_record(series)
    record['ledger_provenance'] = ledger_provenance_record(series)
    record['cluster'] = cluster_record(series)
    record['per_node'] = per_node_record(series)
    maxima = per_node_max_cpu_fractions(series)
    record['per_node_max_cpu_fraction'] = collections.OrderedDict([
        ('n', len(maxima)),
        ('p90', percentile(maxima, 0.9)),
        ('peak', max(maxima) if maxima else None),
    ])
    record['absences'] = absence_record(series)
    record['census'] = census_record(census)
    record['guard'] = guard_record(census)
    record['verdict'] = verdict_record(record)
    return record


def summary_record(series, census=None, label=None,
                   census_limit=DEFAULT_CENSUS_LIMIT):
    """Summarise one job's headroom series and refusal census as a plain dict.

    ``series`` and ``census`` are paths, exactly as the four command line
    arguments of the same names are, so that phase 2's harvest can point
    this at two files it has just unpacked from a bundle and get back the
    same record --json would have written. ``census`` may be None, which is
    recorded as a census nobody collected rather than as one which found
    nothing.

    Everything is read once. The printed report is rendered from the record
    this returns, so nothing downstream re-reads either file.
    """
    return build_summary_record(
        read_series(series), read_census(census, limit=census_limit), label)


def write_record(record, path):
    """Write the record as one JSON object, or say why it could not be.

    Never raises. D15 says nothing this instrument does may fail the job it
    is measuring, and that has to hold for the output file as much as for
    the input: a full disk, a directory which does not exist, or a value
    which will not serialise is a warning on stdout and an exit code of
    zero. The printed report has already been rendered by the time this
    runs, so a failure here costs the reader nothing they can see.
    """
    try:
        with open(path, 'w') as f:
            json.dump(record, f, indent=2)
            f.write('\n')
    except (OSError, TypeError, ValueError) as e:
        print('WARNING: the summary record could not be written to %s: %s'
              % (path, e))
        print('The report above is unaffected, and this is not an error: an')
        print('instrument which can fail the job it measures changes what it')
        print('is measuring (D15).')


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


def fmt_ledger_range(low, high):
    """Render a ledger which may have moved during the run.

    A ledger which changed mid-run is worth seeing rather than averaging:
    the reconciler rewriting a capacity row is exactly the kind of event
    D7's 12-versus-10 discrepancy might turn out to be made of. The record
    carries the two bounds; this renders them as one figure when they agree
    and as a range when they do not.
    """
    if low is None:
        return '-'
    if low == high:
        return fmt(low)
    return '%s-%s' % (fmt(low), fmt(high))


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


def print_series_summary(record):
    series = record['series']
    print_heading('Series')
    if series['read_error']:
        print('  The series file could not be read: %s' % series['read_error'])
        print('  There is no headroom data for this run. This is not the same')
        print('  as a run which used no resources.')
        return

    print('  File:              %s' % series['path'])
    print('  Samples:           %d usable, %d failed (an "error" record), '
          '%d unparseable %s'
          % (series['samples_usable'], series['samples_failed'],
             series['unparseable_lines'],
             plural(series['unparseable_lines'], 'line')))
    if series['unrecognised_lines']:
        print('  Unrecognised:      %d JSON objects carrying neither '
              '"resources" nor "error"' % series['unrecognised_lines'])
    if series['window_start'] is not None:
        print('  Window:            %s to %s (%.0f seconds)'
              % (fmt_time(series['window_start']),
                 fmt_time(series['window_end']), series['window_seconds']))
    else:
        print('  Window:            unknown (no usable timestamps)')

    if series['unparseable_lines']:
        print('  A line which does not parse is normally the last one: the')
        print('  poller is killed mid-write when a job is cancelled.')

    if series['samples_failed']:
        print('  Failed samples, by error text:')
        for text, count in list(series['failed_sample_reasons'].items())[:5]:
            print('    %4d  %s' % (count, text[:90]))

    unreadable = series['ledger_unreadable_samples']
    if unreadable:
        print('  LEDGER UNREADABLE: %d of %d samples had '
              'cpu_committed_row_present false'
              % (unreadable, series['samples_usable']))
        print('    for every node at once. The capacity read returns an empty')
        print('    map for an unreadable table and for an empty one alike, so')
        print('    it means no counter was visible at all.')
        print('    That is NOT that the cluster was idle.')
        print('    Those samples are excluded from the committed CPU figures')
        print('    below. Memory is unaffected: it comes from node metrics.')


def print_ledger_provenance(record):
    provenance = record['ledger_provenance']
    from_row = provenance['node_samples_with_row']
    fallback = provenance['node_samples_fallback']
    fallback_unreadable = provenance['node_samples_fallback_unreadable']
    missing = provenance['node_samples_without_ledger']
    total = provenance['node_samples_total']
    sole = record['series']['sole_node_without_row_samples']

    print_heading('Ledger provenance (D7)')
    print('  Node-samples with a capacity row (cpu_limit):     %d' % from_row)
    print('  Node-samples which fell back to cpu_hard_max:     %d' % fallback)
    print('  Fallbacks inside ledger-unreadable samples:       %d'
          % fallback_unreadable)
    print('  Node-samples with no CPU ledger at all:           %d' % missing)
    if fallback_unreadable:
        print('  The third figure is a failed capacity read, not evidence about')
        print('  the two ledgers. Those samples are already excluded from the')
        print('  headroom figures, and D7 should read the second line alone.')
    if sole:
        print('  %d %s had one visible node and no capacity row for it. That is'
              % (sole, plural(sole, 'sample')))
        print('  a per-node fact on a single-hypervisor topology, not a failed')
        print('  read, so those samples are kept.')
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


def print_cluster_table(record):
    cluster = record['cluster']
    print_heading('Cluster-wide headroom')
    rows = []
    for heading, key in (('committed vCPU', 'committed_cpu'),
                         ('committed memory (MB)', 'committed_memory_mb')):
        block = cluster[key]
        rows.append([
            heading,
            str(block['n']),
            fmt(block['p90']),
            fmt(block['peak']),
            fmt_ledger_range(block['ledger_min'], block['ledger_max']),
            fmt_fraction(block['p90_fraction']),
            fmt_fraction(block['peak_fraction']),
        ])
    print_table(
        ['', 'n', 'p90', 'peak', 'ledger', 'p90 frac', 'peak frac'], rows)
    print('  Fractions are computed per sample and then percentiled, so each')
    print('  one is a ratio something actually stood at. Both sides of the CPU')
    print('  fraction are summed over the nodes which have a ledger, so it')
    print('  cannot exceed 1.0 because a node was missing one.')
    unledgered = cluster['nodes_without_cpu_ledger']
    if unledgered:
        print('  %d %s committed vCPU but no ledger, so %s excluded from the'
              % (len(unledgered),
                 plural(len(unledgered), 'node has', 'nodes have'),
                 plural(len(unledgered), 'it is', 'they are')))
        print('  CPU fraction while still counting in the committed column:')
        for node in unledgered:
            print('    %s' % node)


def print_per_node_tables(record):
    """The per-node tables, in the node order the record carries.

    This is the section D21 was argued from: a cluster-wide fraction inside
    the band, above one node's row reading 1.000 for the whole run.
    """
    cpu_rows = []
    memory_rows = []
    for node, blocks in record['per_node'].items():
        for key, rows in (('committed_cpu', cpu_rows),
                          ('committed_memory_mb', memory_rows)):
            block = blocks[key]
            if not block['n']:
                continue
            rows.append([
                node,
                str(block['n']),
                fmt(block['p90']),
                fmt(block['peak']),
                fmt_ledger_range(block['ledger_min'], block['ledger_max']),
                fmt_fraction(block['p90_fraction']),
                fmt_fraction(block['peak_fraction']),
            ])

    headings = ['node', 'n', 'p90', 'peak', 'ledger', 'p90 frac', 'peak frac']
    print_heading('Committed vCPU, per node')
    print_table(headings, cpu_rows)
    print_heading('Committed memory (MB), per node')
    print_table(headings, memory_rows)
    no_memory_ledger = record['ledger_provenance'][
        'node_samples_without_memory_ledger']
    if no_memory_ledger:
        print('  %d node-samples published no ram_max and therefore have no'
              % no_memory_ledger)
        print('  memory ledger. They are counted here and divided by nowhere.')


def print_absences(record):
    absences = record['absences']
    print_heading('Nodes absent from per_node')
    classifications = absences['classifications']
    if not classifications:
        print('  Every node in every roster appeared in that sample per_node.')
    else:
        rows = []
        for classification, entry in classifications.items():
            nodes = entry['nodes']
            shown = ', '.join(nodes[:3])
            if len(nodes) > 3:
                shown += ', ... (%d nodes)' % len(nodes)
            rows.append([classification, str(entry['node_samples']), shown])
        print_table(['classification', 'node-samples', 'nodes'], rows)
        print('  summarize_resources() omits a node which is not a hypervisor,')
        print('  whose metrics are over 120s old, or whose queue is over')
        print('  UNREASONABLE_QUEUE_LENGTH, and never says which. Only the')
        print('  first is answerable from the roster, so the rest are reported')
        print('  as unexplained rather than dropped.')

    if absences['samples']:
        print('  Nodes visible in per_node: %d at fewest, %d at most, across '
              '%d samples.' % (absences['nodes_visible_min'],
                               absences['nodes_visible_max'],
                               absences['samples']))
        print('  That is what the samples could see, which is not the same as')
        print('  how many hypervisors the cluster had.')


def print_census(record):
    census = record['census']
    print_heading('Refusal census')
    if census['state'] == 'not requested':
        print('  NO CENSUS WAS SUPPLIED (--census was not given).')
        print('  This is not "no refusals": nothing was looked at. A run whose')
        print('  refusals were never collected and a run which refused nothing')
        print('  are different findings, and this is the first.')
        return
    if not census['available']:
        print('  NO CENSUS IS AVAILABLE: %s (%s)'
              % (census['state'], census['detail']))
        print('  File: %s' % census['path'])
        print('  Read this as "unknown", never as zero refusals. The census')
        print('  depends on the log shipping path being healthy (D11), so a')
        print('  broken shipper looks exactly like a cluster with room to')
        print('  spare unless the difference is said out loud.')
        return

    print('  File:              %s' % census['path'])
    print('  Log records read:  %d (%d were schedule stage events, '
          '%d were capacity guard events, %d %s unparseable)'
          % (census['records'], census['stage_events'], census['guard_events'],
             census['unparseable_lines'],
             plural(census['unparseable_lines'], 'line')))
    if census['truncated']:
        print('  CENSUS MAY BE TRUNCATED: the query returned %d entries and was'
              % census['records'])
        print('  allowed %d. Loki gives no signal that it cut a response short,'
              % census['limit'])
        print('  so treat every count below as a LOWER BOUND. An undercounted')
        print('  census reads as a cluster with room, which is backwards.')

    stages = census['stages']
    if not stages:
        print('  No schedule stage events at all. Either nothing was scheduled')
        print('  in the census window, or the query did not match. Both are')
        print('  worth checking before reading this as an idle cluster.')
        return

    rows = []
    for stage, tally in sorted(stages.items(),
                               key=lambda kv: (-kv[1]['dropped'], kv[0])):
        note = CAPACITY_STAGE_NOTES.get(stage)
        if note is None:
            note = 'not a stage this report knows; counted anyway'
        rows.append([
            stage, str(tally['events']), str(tally['aborts']),
            str(tally['dropped']), note])
    print_table(['stage', 'events', 'aborts', 'dropped', 'kind'], rows)
    print('  Tallied by the stage string observed in the events, never by a')
    print('  list held here (D10), so a stage added or renamed in the')
    print('  scheduler still appears above.')

    missing = [name for name in CAPACITY_STAGE_NOTES if name not in stages]
    if missing:
        print('  Capacity stages not observed at all in this census: %s'
              % ', '.join(missing))

    print()
    print('  Drop reasons, by stage:')
    for stage, tally in sorted(stages.items(), key=lambda kv: kv[0]):
        if not tally['reasons']:
            continue
        print('    %s:' % stage)
        for reason, count in tally['reasons'].items():
            flag = ''
            if reason == MISSING_DATA_REASON:
                flag = '   <-- MISSING DATA, not a shortage'
            print('      %5d  %s%s' % (count, reason, flag))

    missing_data = census['missing_data_drops']
    if missing_data:
        print()
        print('  %d %s carried the reason %r.'
              % (missing_data, plural(missing_data, 'drop'), MISSING_DATA_REASON))
        print('  That is a node whose metrics row had no memory_max, which is')
        print('  missing data rather than a shortage of memory, and it is')
        print('  excluded from every shortage count in this report. Counting it')
        print('  would read a stale metrics row as evidence the cloud is small.')


def print_guard_census(record):
    """The capacity guard's refusals, under their own heading.

    Deliberately a separate section from the stage census above rather
    than more rows in it. The stage census is about candidate nodes being
    dropped from a list; this is about the ledger refusing the write for
    the node which survived that list. A run can have none of the first
    and a hundred of the second, and that run is exactly the one this
    section exists to make visible.
    """
    census = record['census']
    guard = record['guard']
    print_heading('Capacity guard census')
    if guard['state'] == 'no_census':
        print('  NO CENSUS WAS SUPPLIED (--census was not given), so nothing is')
        print('  known about what the capacity guard did. Not zero refusals.')
        return
    if guard['state'] == 'census_unavailable':
        print('  NO CENSUS IS AVAILABLE: %s (%s)'
              % (census['state'], census['detail']))
        print('  Read as "unknown", never as zero guard refusals.')
        return

    if guard['state'] == 'not_collected':
        print('  NO CAPACITY GUARD EVENTS IN THIS CENSUS.')
        print('  Read that as a fact about the query before reading it as a')
        print('  fact about the cluster: the census is collected with a LogQL')
        print('  filter, and if that filter selects only the scheduler stage')
        print('  messages then a guard which refused every candidate leaves')
        print('  nothing here to count. The filter must also match')
        print('  %r' % GUARD_DENIED_MESSAGE)
        print('  and %r' % CLAIM_OVER_LIMIT_MESSAGE)
        print('  for this section to mean anything at all.')
        if census['stage_events']:
            print('  This census DID carry %d schedule stage %s, so the log'
                  % (census['stage_events'],
                     plural(census['stage_events'], 'event')))
            print('  shipping path was healthy and the filter is the difference.')
        return

    print('  Placements refused by the guard: %d' % guard['denials'])
    if guard['unenforced']:
        print('  %d of those had enforce=false: a ground-truth writer (the'
              % guard['unenforced'])
        print('  cleaner, or startup reconciliation) recording where a domain')
        print('  already is. Those refuse nothing a user asked for.')
    if guard['malformed']:
        print('  %d %s carried no usable dimensions list and %s counted here'
              % (guard['malformed'], plural(guard['malformed'], 'event'),
                 plural(guard['malformed'], 'is', 'are')))
        print('  but explain nothing. That is an event shape this tool does')
        print('  not understand, not a cluster fact.')
    if guard['empty_dimensions']:
        print('  %d %s carried a readable but EMPTY dimensions list, which is'
              % (guard['empty_dimensions'],
                 plural(guard['empty_dimensions'], 'event')))
        print('  a guard which refused without saying against what. Counted')
        print('  apart from the malformed events above, whose shape this tool')
        print('  could not read at all.')

    if guard['stages']:
        rows = []
        for stage, count in sorted(guard['stages'].items(),
                                   key=lambda kv: (-kv[1], kv[0])):
            if stage == UNKNOWN_STAGE:
                note = 'the event carried no failing_stage'
            else:
                note = GUARD_STAGE_NOTES.get(
                    stage, 'not a stage this report knows; counted anyway')
            rows.append([stage, str(count), note])
        print()
        print('  Refusals by failing stage:')
        print_table(['stage', 'refusals', 'what it guards'], rows, indent='    ')

    if guard['exceeded'] or guard['nothing_exceeded']:
        rows = []
        for dimension, count in sorted(guard['exceeded'].items(),
                                       key=lambda kv: (-kv[1], kv[0])):
            if dimension == UNKNOWN_DIMENSION:
                note = 'the event named no dimension'
            else:
                note = GUARD_DIMENSION_NOTES.get(
                    dimension,
                    'not a dimension this report knows; counted anyway')
            rows.append([dimension, str(count),
                         str(guard['sole_exceedance'].get(dimension, 0)), note])
        if guard['nothing_exceeded']:
            rows.append([NO_DIMENSIONS, str(guard['nothing_exceeded']), '0',
                         'the guard refused and marked nothing exceeded'])
        print()
        print('  Refusals by exceeded dimension. A refusal exceeding two')
        print('  dimensions is counted once under each, so the column sums to')
        print('  more than the refusal count; "alone" is the subset where that')
        print('  dimension was the only one exceeded.')
        print_table(['dimension', 'refusals', 'alone', 'what it is'], rows,
                    indent='    ')

    if len(guard['stages']) > 1:
        print()
        print('  Exceeded dimensions by stage:')
        for stage in sorted(guard['stage_dimensions']):
            names = guard['stage_dimensions'][stage]
            print('    %s: %s' % (stage, ', '.join(
                '%s x%d' % (name, count)
                for name, count in sorted(names.items(),
                                          key=lambda kv: (-kv[1], kv[0])))))

    demand_total = (guard['demand_measured_alone']
                    + guard['demand_estimate_tipped'] + guard['demand_unsplit'])
    if demand_total:
        print()
        print('  Of the %d %s exceeding the demand dimension:'
              % (demand_total, plural(demand_total, 'refusal')))
        print('    %5d  measured CPU load alone was already over the limit'
              % guard['demand_measured_alone'])
        print('    %5d  the D13 feedforward estimate is what carried it over'
              % guard['demand_estimate_tipped'])
        if guard['demand_unsplit']:
            print('    %5d  no cpu_load_1 / expected_demand split recorded'
                  % guard['demand_unsplit'])
        print('  Demand is not an allocation, so a refusal here is a rate')
        print('  prediction rather than a cloud which ran out of room, and the')
        print('  second line is an estimator finding rather than a sizing one.')

    if guard['shortfalls']:
        print()
        print('  Worst shortfall seen per dimension among these REFUSALS, as')
        print('  the event reported it. The server computes it where the guard')
        print('  made the comparison, floored at zero, so nothing here')
        print('  recomputes it and no two readers can disagree about its sign:')
        for dimension in sorted(guard['shortfalls']):
            print('    %-12s %s'
                  % (dimension, fmt(guard['shortfalls'][dimension], 3)))
    elif guard['denials']:
        print()
        print('  No refused dimension carried a shortfall field. That is a')
        print('  series written by a build predating it, not a shortfall of')
        print('  zero; the three numbers it is derived from are in the events.')

    for unrecognised, what in ((guard['unrecognised_stages'], 'stage'),
                               (guard['unrecognised_dimensions'], 'dimension')):
        if unrecognised:
            print()
            print('  Counted but unrecognised %s: %s'
                  % (plural(len(unrecognised), what),
                     ', '.join(unrecognised)))

    print()
    print('  Claim exceedances (ADMITTED, never refused): %d' % guard['claims'])
    if not guard['claims']:
        print('    No placement drew a namespace past a capacity claim, or no')
        print('    namespace in this cluster has one.')
        return
    print('    These placements SUCCEEDED. CLAIM_ENFORCEMENT_HARD is False, so')
    print('    advisory mode admits over a claim on purpose and this is the')
    print('    system doing what the operator asked. It is the signal a')
    print('    declared footprint needs revising (D9), and it is never added')
    print('    to the refusal count above.')
    if guard['claim_malformed']:
        print('    %d carried no usable claim_dimensions list.'
              % guard['claim_malformed'])
    rows = [[namespace, str(count)]
            for namespace, count in guard['claim_namespaces'].items()]
    print_table(['namespace', 'admitted over claim'], rows, indent='    ')
    if guard['claim_exceeded']:
        print('    Claim dimensions exceeded: %s' % ', '.join(
            '%s x%d' % (name, count)
            for name, count in sorted(guard['claim_exceeded'].items(),
                                      key=lambda kv: (-kv[1], kv[0]))))
    if guard['claim_shortfalls']:
        print('    Worst amount over the claim, per dimension, as the event')
        print('    reported it. This is how far past a declared footprint a')
        print('    namespace went on an ADMITTED placement, and it is not the')
        print('    refusal shortfall above:')
        for dimension in sorted(guard['claim_shortfalls']):
            print('      %-12s %s'
                  % (dimension, fmt(guard['claim_shortfalls'][dimension], 3)))


def print_verdict(record):
    census = record['census']
    guard = record['guard']
    verdict = record['verdict']
    print_heading('D3 band verdict (PROVISIONAL bounds %.2f / %.2f)'
                  % (BAND_LOWER, BAND_UPPER))
    ratio = verdict['p90_cpu_fraction']
    if ratio is None:
        print('  NO VERDICT: no sample produced a committed-vCPU-over-ledger')
        print('  ratio. With %d samples read and %d of them ledger-unreadable,'
              % (record['series']['samples_usable'],
                 record['series']['ledger_unreadable_samples']))
        print('  there is nothing to compare against the band.')
    else:
        print('  p90 committed vCPU / ledger, cluster wide: %s'
              % fmt_fraction(ratio))
        if verdict['band'] == 'OVERSIZED':
            text = ('OVERSIZED -- below the provisional lower bound of %.2f'
                    % BAND_LOWER)
        elif verdict['band'] == 'OVERSUBSCRIBED':
            text = ('OVERSUBSCRIBED -- above the provisional upper bound of '
                    '%.2f' % BAND_UPPER)
        else:
            text = 'WITHIN BAND'
        print('  Verdict: %s' % text)

    print('  These bounds are PROVISIONAL. Phase 0 set them without any')
    print('  distribution to check them against, and phase 2 replaces them or')
    print('  defends them. Nothing gates on this verdict: this phase computes')
    print('  and prints the band, and phase 5 owns turning it into a guardrail.')

    print()
    if verdict['refusal_warning'] is None:
        print('  Refusal warning: UNKNOWN. Per D3 any capacity-stage refusal is')
        print('  a warning on its own, independent of the ratio above -- but no')
        print('  census was read, so that half of the verdict is missing.')
        return
    shortage = census['capacity_shortage_drops']
    if verdict['refusal_warning']:
        print('  Refusal warning: YES. %d candidate %s at a capacity stage.'
              % (shortage, plural(shortage, 'drop')))
        print('  Per D3 that is a warning in its own right, whatever the ratio')
        print('  says: a poll every fifteen seconds cannot see a refusal, which')
        print('  begins and ends between samples.')
        if census['disk_bandwidth_drops']:
            print('  %d of them are at sufficient_idle_disk, which is disk'
                  % census['disk_bandwidth_drops'])
            print('  BANDWIDTH -- a rate predicate no amount of extra hardware')
            print('  in the same shape would fix. Do not read those as a case')
            print('  for a bigger cloud.')
    else:
        print('  Refusal warning: no capacity-stage drops in the census window.')
    if census['unclassified_shortage_drops']:
        print('  %d further %s at stages this report does not classify (see the'
              % (census['unclassified_shortage_drops'],
                 plural(census['unclassified_shortage_drops'], 'drop')))
        print('  census table above). They are not counted in the warning')
        print('  either way, because nothing here knows whether they are')
        print('  capacity stages -- a scheduler stage added since this tool')
        print('  was written lands here.')
    # Stated as its own line rather than folded into the warning above,
    # because the two are different evidence: a stage drop is a candidate
    # node removed from a list, a guard refusal is a create which did not
    # happen. A run with zero of the first and many of the second is the
    # #3772 shape, and it is the reading this line exists to prevent.
    if guard['state'] != 'collected':
        print('  Guard refusals: NOT COLLECTED in this census (see the capacity')
        print('  guard section). Unknown, not zero.')
    elif guard['denials']:
        print('  Guard refusals: YES. The ledger refused %d %s, of which %d'
              % (guard['denials'], plural(guard['denials'], 'placement'),
                 guard['denials'] - guard['unenforced']))
        print('  refused something a caller asked for. Whatever the ratio above')
        print('  says, a refused placement is a create which did not happen.')
        sole_demand = guard['sole_exceedance'].get('demand', 0)
        if sole_demand:
            print('  %d of them %s refused on the demand dimension ALONE, with'
                  % (sole_demand, plural(sole_demand, 'was', 'were')))
            print('  every allocated dimension inside its limit. That is not a')
            print('  cloud which ran out of room; see the split above.')
    else:
        print('  Guard refusals: none in the census window.')
    if guard['state'] == 'collected' and guard['claims']:
        print('  %d %s admitted OVER a namespace capacity claim. Advisory mode'
              % (guard['claims'], plural(guard['claims'], 'placement was',
                                         'placements were')))
        print('  did what the operator asked; this is calibration data, not a')
        print('  failure, and it is no part of the refusal counts above.')

    if census['truncated']:
        print('  The census may have been truncated, so every count above is a')
        print('  lower bound. Absence of a warning is not evidence of absence.')


def print_report(record):
    """Render the whole report from the record, and nothing but the record.

    Every figure printed here is read out of the dict rather than computed,
    so the job log and the --json file cannot disagree (D18). The sections
    which are not printed for an empty series are skipped on the record's
    own sample count for the same reason.
    """
    title = 'Shaken Fist CI headroom report'
    print(title)
    print('=' * len(title))
    if record['label']:
        print('Label:  %s' % record['label'])

    print_series_summary(record)
    if record['series']['samples_usable']:
        print_ledger_provenance(record)
        print_cluster_table(record)
        print_per_node_tables(record)
        print_absences(record)
    else:
        print()
        print('  No usable samples, so there is no headroom to report. That is')
        print('  a fact about the instrument, not about the cluster.')

    print_census(record)
    print_guard_census(record)
    print_verdict(record)
    print()


def report(args):
    record = summary_record(args.series, census=args.census, label=args.label,
                            census_limit=args.census_limit)
    print_report(record)
    if args.json:
        # Deliberately silent on success. The printed report must read
        # identically whether or not a summary record was also written, so
        # that a reader comparing a job log from before --json was wired up
        # against one from after sees no difference at all.
        write_record(record, args.json)


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
              'stage events, and ideally the capacity guard events beside '
              'them -- a filter which selects only the stage messages leaves '
              'the guard census with nothing to count, which the report says '
              'out loud. Optional: the report says so explicitly when it is '
              'absent, rather than printing zero refusals.'))
    parser.add_argument(
        '--label', default=None,
        help='A label for the run, printed at the top (typically the topology).')
    parser.add_argument(
        '--json', default=None,
        help=('Write the machine-readable summary record to this path, as one '
              'JSON object (D18). The printed report above is rendered from '
              'the same record, so the two cannot disagree; phase 2\'s '
              'harvest and phase 5\'s guardrail read this rather than parsing '
              'the prose. A path which cannot be written is a warning, never '
              'an error: nothing this instrument does may fail the job it is '
              'measuring (D15).'))
    parser.add_argument(
        '--census-limit', type=int, default=DEFAULT_CENSUS_LIMIT,
        help=('The entry limit the census query was issued with. A response '
              'holding exactly this many entries is reported as possibly '
              'truncated, because Loki gives no other signal that it cut one '
              'short. Defaults to 5000, which is both Loki\'s default '
              'max_entries_limit_per_query and the value used by '
              'tools/ci_headroom_collect.sh in shakenfist/actions.'))

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
