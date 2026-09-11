# Copyright 2019 Michael Still and contributors
"""Every create_instance() call in the CI suite goes through the wrapper.

``BaseTestCase.create_instance()`` waits out a transient capacity
refusal instead of failing the test on it (see
``shakenfist/tests/test_ci_capacity_wait.py`` for that wrapper's own
behaviour). That protection only exists for a caller which spells its
create as ``self.create_instance(...)``. A call which reaches a client's
``create_instance()`` directly -- ``self.test_client.create_instance(...)``,
a local variable, another test's client -- skips the wait entirely, and
does so silently: nothing about a raw call looks different from a
wrapped one in a test run, right up until a sibling test's capacity
turns it into a flake.

A call is not the only way to reach one. ``assertRaises(Exc,
self.test_client.create_instance, ...)`` hands the bound method over as a
*reference* and lets assertRaises do the calling, which is how six sites
in this suite reach the raw client. A guard which matched only
``ast.Call`` saw none of them while claiming to cover everything, so
references are checked too and carry the same marker.

So a raw call is not banned, it is priced. It needs a
``# raw-create: <reason>`` comment in the block of comment lines
immediately above it, with a non-empty reason, or this guard fails the
build. Two kinds of caller pay
that price on purpose: the wrapper's own single internal call in
``base.py`` (it has to reach the client somehow), and a caller which
must see the refusal rather than have it waited out -- the CI cloud
sizing plan's phase 3 saturation tests, which assert that a full
cluster actually refuses, are the case this exists for even though none
of them exist yet.

The check parses source with ``ast`` rather than importing the suite,
following ``test_ci_claims_headroom.py``'s ``_calls_of()`` pattern for
finding the calls and ``test_ci_namespace_membership.py``'s directory
walk for finding the files: the functional suite imports
``shakenfist_client``, which is not a test dependency of this
repository, so nothing under ``shakenfist/deploy/shakenfist_ci/`` can be
imported here.

The reason is required to be non-empty specifically so the allowlist
cannot grow by copy-pasting an existing marker line without writing a
new sentence -- an empty ``# raw-create:`` is treated exactly like no
marker at all.
"""

import ast
import os
import re

from shakenfist.tests import base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')

# The suite held 41 files with a create_instance() call when this was
# written, across 62 total .py files. The floor is a long way below
# that: its job is to catch a walk which found nothing or almost
# nothing, not to track the file count.
MINIMUM_SUITE_FILES = 20

# A call reaches the client's create_instance() through this receiver
# name with no marker required: it is BaseTestCase's own wrapper, which
# is the protection this guard exists to make mandatory.
WRAPPED_RECEIVER = 'self'

RAW_CREATE_RE = re.compile(r'^\s*#\s*raw-create:\s*(.*)$')
COMMENT_RE = re.compile(r'^\s*#')


def _is_wrapped_receiver(attribute):
    """True for self.create_instance, which needs no marker.

    The receiver has to be the bare name ``self``, not merely start with
    it: ``self.test_client.create_instance`` reaches the client directly
    and is exactly what this guard is for.
    """
    return (isinstance(attribute.value, ast.Name) and
            attribute.value.id == WRAPPED_RECEIVER)


def _create_instance_sites(tree):
    """Every place this source names a client's create_instance.

    A call is not the only way to reach one. ``assertRaises(Exc,
    self.test_client.create_instance, ...)`` passes the bound method as a
    *reference* and lets assertRaises do the calling, so a guard which
    only matched ast.Call saw none of those -- six of them, at the time
    this was written, every one a raw create which the marker rule
    claimed to cover and did not.

    Walking attribute nodes rather than call nodes finds both shapes, and
    finds each exactly once. An ast.Call's ``func`` *is* the attribute
    node, and CPython gives a call and its func the same lineno, so
    walking calls as well would report every call twice and tell us
    nothing the attribute had not already said.
    """
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == 'create_instance']


def _preceding_comment_block(lines, lineno):
    """Contiguous comment lines immediately above 1-indexed `lineno`.

    A marker's reason often wraps onto a second comment line, as the
    wrapper's own does in base.py, so the whole contiguous block is
    collected; only its first line is required to carry the
    ``raw-create:`` prefix.
    """
    block = []
    i = lineno - 2
    while i >= 0 and COMMENT_RE.match(lines[i]):
        block.insert(0, lines[i])
        i -= 1
    return block


def _raw_create_reason(lines, lineno):
    """The marker's reason text for a call at 1-indexed `lineno`, or None.

    Any line of the comment block carries the marker, not only its first.
    Requiring the first meant that an ordinary comment above a marker --
    ``# Start our test instance`` on the line above ``# raw-create: ...``
    -- silently disabled it, which fails safe but is a baffling thing to
    debug when adding one.
    """
    for line in _preceding_comment_block(lines, lineno):
        m = RAW_CREATE_RE.match(line)
        if m:
            return m.group(1).strip()
    return None


def find_unmarked_raw_creates(source):
    """Line numbers of raw create_instance uses with no non-empty marker."""
    tree = ast.parse(source)
    lines = source.splitlines()
    offences = []
    for attribute in _create_instance_sites(tree):
        if _is_wrapped_receiver(attribute):
            continue
        if not _raw_create_reason(lines, attribute.lineno):
            offences.append(attribute.lineno)
    return sorted(offences)


class RawCreateInstanceMarkerTestCase(base.ShakenFistTestCase):
    def test_the_check_recognises_an_unmarked_raw_call(self):
        """The positive control: without this a clean scan proves nothing."""
        offences = find_unmarked_raw_creates(
            "client.create_instance('a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([1], offences)

    def test_an_empty_reason_is_treated_as_unmarked(self):
        """A marker with nothing after the colon does not buy an exemption.

        Otherwise the allowlist grows by pasting a bare
        ``# raw-create:`` in front of a call, which is the copy-paste
        this guard exists to prevent.
        """
        offences = find_unmarked_raw_creates(
            "# raw-create:\n"
            "client.create_instance('a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([2], offences)

    def test_a_marker_with_a_reason_is_accepted(self):
        offences = find_unmarked_raw_creates(
            "# raw-create: must see the raw refusal, not a waited-out one\n"
            "client.create_instance('a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([], offences)

    def test_a_marker_may_wrap_onto_a_second_comment_line(self):
        """Matches the wrapper's own marker in base.py, which wraps."""
        offences = find_unmarked_raw_creates(
            "# raw-create: this is the wrapper itself, and is the one\n"
            "# place in the suite which has to issue the create.\n"
            "return client.create_instance(\n"
            "    'a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([], offences)

    def test_a_call_through_self_needs_no_marker(self):
        """self.create_instance(...) is the wrapper: this is the safe path."""
        offences = find_unmarked_raw_creates(
            "self.create_instance('a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([], offences)

    def test_a_marker_may_sit_under_an_ordinary_comment(self):
        """The marker is looked for anywhere in the block, not only first.

        Requiring the first line meant an ordinary comment above a marker
        silently disabled it, and the call was then reported as unmarked
        with no hint as to why -- which is a baffling thing to debug at
        exactly the moment someone is adding their first marker.
        """
        offences = find_unmarked_raw_creates(
            "# Start our test instance\n"
            "# raw-create: must see the raw refusal, not a waited-out one\n"
            "client.create_instance('a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([], offences)

    def test_a_reference_passed_to_assert_raises_is_not_exempt(self):
        """The shape six sites in this suite use, which ast.Call misses.

        assertRaises() is handed the bound method and does the calling
        itself, so there is no ast.Call naming create_instance anywhere in
        the source. Without this the guard reported a clean scan while six
        raw creates went unmarked, and the next one added would have been
        exempt for free.
        """
        offences = find_unmarked_raw_creates(
            "self.assertRaises(\n"
            "    Exception,\n"
            "    self.test_client.create_instance,\n"
            "    'a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([3], offences)

    def test_a_marked_reference_is_accepted(self):
        offences = find_unmarked_raw_creates(
            "self.assertRaises(\n"
            "    Exception,\n"
            "    # raw-create: asserts a 404, which waiting cannot fix\n"
            "    self.test_client.create_instance,\n"
            "    'a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([], offences)

    def test_a_call_is_reported_once(self):
        """A call is an attribute node too, so it can be found twice.

        ast.walk() visits a call's own func attribute as well as the call
        itself, and the two share a lineno, so a guard which walked both
        kinds would report every unmarked call twice -- and every
        offence list, and the failure message built from it, would be
        double length for no reason.
        """
        offences = find_unmarked_raw_creates(
            "return client.create_instance(\n"
            "    'a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([1], offences)

    def test_a_marker_above_a_wrapped_call_line_is_found(self):
        """The marker sits above the call, which is where the receiver is."""
        offences = find_unmarked_raw_creates(
            "# raw-create: this is the wrapper itself\n"
            "return client.create_instance(\n"
            "    'a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([], offences)

    def test_a_reference_through_self_needs_no_marker(self):
        offences = find_unmarked_raw_creates(
            "self.assertRaises(Exception, self.create_instance, 'a')\n")
        self.assertEqual([], offences)

    def test_a_call_through_a_self_attribute_is_not_exempt(self):
        """self.test_client.create_instance(...) still bypasses the wait.

        The receiver has to be the bare name ``self``, not merely start
        with it -- otherwise every raw call already in this suite's
        history (``self.test_client``, ``self.system_client``,
        ``self.burst_client``) would have been silently exempt.
        """
        offences = find_unmarked_raw_creates(
            "self.test_client.create_instance("
            "'a', 1, 1024, None, None, None, None)\n")
        self.assertEqual([1], offences)

    def test_no_unmarked_raw_create_calls_in_the_suite(self):
        """The real guard: every call under shakenfist_ci/ is wrapped or paid for."""
        found = []
        scanned = 0
        for dirpath, _, filenames in os.walk(CI_SUITE):
            for filename in filenames:
                if not filename.endswith('.py'):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path) as f:
                    source = f.read()
                scanned += 1
                for lineno in find_unmarked_raw_creates(source):
                    found.append(
                        '%s:%d' % (os.path.relpath(path, CI_SUITE), lineno))

        # os.walk() over a directory which is not there yields nothing
        # and raises nothing, so a clean result has to be shown to be a
        # result before it is trusted -- the same reasoning
        # test_ci_namespace_membership.py's floor uses.
        self.assertGreater(
            scanned, MINIMUM_SUITE_FILES,
            'The CI suite scan found %d Python files under %s, so an '
            'empty offence list below would mean nothing.'
            % (scanned, CI_SUITE))

        self.assertEqual(
            [], found,
            'A create_instance() call bypasses BaseTestCase\'s wrapper '
            'and is not marked with a non-empty "# raw-create: <reason>" '
            'comment immediately above it, so a transient capacity '
            'refusal there fails the test instead of being waited out. '
            'Route it through self.create_instance() (passing client= if '
            'it needs a client other than self.test_client), or add the '
            'marker if it must see the raw refusal. Sites: %s'
            % ', '.join(found))
