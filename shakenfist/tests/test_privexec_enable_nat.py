# Copyright 2026 Michael Still and contributors
"""Tests for the privexec enable NAT handler.

Every rule `_enable_nat` installs lives inside the network's own network
namespace, and every rule is installed at most once. Both halves of that
have been wrong: the idempotency probe this replaced listed POSTROUTING in
the root namespace, where none of these rules are, and the return
direction FORWARD rule named a literal string rather than the interface
the variable beside it holds.
"""

from unittest import mock

from shakenfist import exceptions
from shakenfist.daemons.privexec import main as privexec_main
from shakenfist.protos import privexec_pb2
from shakenfist.tests import base
from shakenfist.tests.test_privexec_floating_ip import CommandRecorder
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import iptables as util_iptables


NETWORK_UUID = 'ccc652aa-f7b6-4f99-b76d-443ae4d91412'
VXID = 0xe2300f
EGRESS_VETH = 'egr-e2300f-i'
VX_VETH = 'veth-e2300f-i'

# A -C which cannot find the rule. This is what iptables says when the
# rule is genuinely absent, and it is what makes the append run.
RULE_ABSENT = {'-C': ('', 'iptables: Bad rule (does a matching rule exist '
                          'in that chain?).\n', 1)}

# A -D which removes one copy and then finds nothing left, which is what
# a namespace holding exactly one copy of a rule looks like.
RULE_DELETED_ONCE = {'-D': [('', '', 0),
                            ('', 'iptables: Bad rule (does a matching rule '
                                 'exist in that chain?).\n', 1)]}


class PrivExecEnableNATTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.locate_command = mock.patch(
            'shakenfist.daemons.privexec.util.locate_command',
            side_effect=lambda c: c)
        self.locate_command.start()
        self.addCleanup(self.locate_command.stop)

        self.job = privexec_main.PrivExecJob(mock.MagicMock())

    def patch_commands(self, results=None):
        recorder = CommandRecorder(results)
        patcher = mock.patch(
            'shakenfist.daemons.privexec.util.command_helper',
            side_effect=recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        return recorder

    def _request(self):
        return privexec_pb2.EnableNATRequest(
            network_uuid=NETWORK_UUID,
            network_address='172.16.0.0',
            network_mask='255.255.255.0',
            vxid=VXID)

    def _enable_nat(self):
        # _enable_nat reads (and would write) the host's ip_forward
        # sysctl. Report it as already on so the write never happens, and
        # keep the patch off everything else the test framework does.
        with mock.patch('builtins.open', mock.mock_open(read_data='1\n')):
            return self.job._enable_nat(self._request())

    def _appends(self, recorder):
        return [c for c in recorder.calls if '-A' in c]

    def _deletes(self, recorder):
        return [c for c in recorder.calls if '-D' in c]

    def test_enable_nat_installs_the_expected_rules(self):
        recorder = self.patch_commands(RULE_ABSENT)

        reply = self._enable_nat()

        self.assertEqual(
            privexec_pb2.EnableNATReply.OK, reply.enable_nat_reply.error)
        self.assertEqual(
            [
                ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
                 '-t', 'filter', '-A', 'FORWARD', '-o', EGRESS_VETH,
                 '-i', VX_VETH, '-j', 'ACCEPT'),
                ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
                 '-t', 'filter', '-A', 'FORWARD', '-i', EGRESS_VETH,
                 '-o', VX_VETH, '-j', 'ACCEPT'),
                ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
                 '-t', 'nat', '-A', 'POSTROUTING', '-s',
                 '172.16.0.0/255.255.255.0', '-o', EGRESS_VETH,
                 '-j', 'MASQUERADE'),
                ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
                 '-t', 'nat', '-A', 'POSTROUTING', '-s',
                 '172.16.0.0/255.255.255.0', '-o', VX_VETH,
                 '-m', 'conntrack', '--ctstate', 'DNAT', '-j', 'MASQUERADE'),
            ],
            self._appends(recorder))

    def test_the_return_path_names_the_vx_veth(self):
        """The inbound FORWARD rule matches the interface, not its name.

        It used to be built with the literal string 'vx_veth_inner' where
        the variable of that name was intended, so it matched an interface
        which will never exist. Nothing broke, because the namespace's
        FORWARD policy is ACCEPT and these rules only ever accept -- but
        the rule was the wrong rule, and the day the policy tightens is
        not the day to discover that.
        """
        recorder = self.patch_commands(RULE_ABSENT)

        self._enable_nat()

        # The literal survives in exactly one place: the delete which
        # removes the rule an older Shaken Fist wrote with it. No rule
        # is installed naming it.
        self.assertEqual(
            [], [c for c in self._appends(recorder) if 'vx_veth_inner' in c])
        inbound = [c for c in self._appends(recorder)
                   if '-i' in c and c[c.index('-i') + 1] == EGRESS_VETH]
        self.assertEqual(1, len(inbound))
        self.assertIn(VX_VETH, inbound[0])

    def test_the_stale_return_path_rule_is_deleted(self):
        """The broken rule an older Shaken Fist wrote is removed.

        A -C for the corrected rule cannot match the old one, so without
        an explicit delete an upgraded namespace holds both forever: the
        right rule and a rule matching an interface which will never
        exist. Harmless while the FORWARD policy is ACCEPT, but it means
        the namespace never converges on what util_iptables describes.
        """
        recorder = self.patch_commands(dict(RULE_ABSENT, **RULE_DELETED_ONCE))

        self._enable_nat()

        stale = ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
                 '-t', 'filter', '-D', 'FORWARD', '-i', EGRESS_VETH,
                 '-o', 'vx_veth_inner', '-j', 'ACCEPT')
        # Two deletes: the one which removed the rule, and the one which
        # established there was not a second copy of it.
        self.assertEqual([stale, stale], self._deletes(recorder))

        # And it happens before the installs, so a namespace ends up
        # holding exactly the rules util_iptables names.
        self.assertLess(recorder.calls.index(self._deletes(recorder)[0]),
                        recorder.calls.index(self._appends(recorder)[0]))

    def test_every_copy_of_the_stale_rule_is_deleted(self):
        """Duplicates of the broken rule are drained, not just one.

        iptables -D removes one matching rule. The _enable_nat this
        replaced appended its rules unconditionally on every create and
        every maintain driven recreate, so a namespace on an upgraded
        cluster can hold several copies of the rule we no longer believe
        in. Deleting one of them leaves the rest, and nothing ever looks
        again -- the -C for the corrected rule cannot match a broken one,
        so the install appends and moves on.
        """
        recorder = self.patch_commands(dict(
            RULE_ABSENT,
            **{'-D': [('', '', 0), ('', '', 0), ('', '', 0),
                      ('', 'iptables: Bad rule (does a matching rule exist '
                           'in that chain?).\n', 1)]}))

        self._enable_nat()

        # Three copies removed, then a fourth call which found nothing
        # left. Not one delete and three survivors.
        self.assertEqual(4, len(self._deletes(recorder)))

    def test_duplicate_deletion_is_bounded(self):
        """A delete which always succeeds still terminates.

        Nothing should be able to report success forever, but a loop
        driven by an external command's exit code does not get to
        assume that. The bound is asserted as a literal rather than by
        naming the constant, because a test which reads the constant
        moves with it and therefore cannot fail.
        """
        recorder = self.patch_commands(RULE_ABSENT)

        self._enable_nat()

        self.assertEqual(32, privexec_main.MAX_DUPLICATE_IPTABLES_RULES)
        self.assertEqual(32, len(self._deletes(recorder)))

    def test_an_absent_stale_rule_is_not_an_error(self):
        """A namespace which never had the broken rule still comes up.

        Absence is the end state the delete wants, so iptables failing
        to find anything to delete is success. Treating it as a failure
        would break every network on a cluster installed after the fix.
        """
        recorder = self.patch_commands({
            '-C': ('', '', 1),
            '-D': ('', 'iptables: Bad rule (does a matching rule exist in '
                       'that chain?).\n', 1),
        })

        reply = self._enable_nat()

        self.assertEqual(
            privexec_pb2.EnableNATReply.OK, reply.enable_nat_reply.error)
        # The whole install still ran: every rule was checked for and,
        # finding none of them, appended.
        checks = [c for c in recorder.calls if '-C' in c]
        self.assertNotEqual([], checks)
        self.assertEqual(len(checks), len(self._appends(recorder)))

    def test_every_rule_is_installed_inside_the_namespace(self):
        """No iptables invocation may run in the root namespace.

        The idempotency probe this replaced did exactly that: it listed
        the root namespace's POSTROUTING looking for rules which are only
        ever written inside the network's namespace, so it could neither
        see a rule that was there nor avoid matching one that was not.
        """
        recorder = self.patch_commands(RULE_ABSENT)

        self._enable_nat()

        self.assertNotEqual([], recorder.calls)
        for call in recorder.calls:
            self.assertEqual(
                ('ip', 'netns', 'exec', NETWORK_UUID), call[:4],
                'command ran outside the network namespace: %s' % (call,))

    def test_the_hairpin_rule_only_matches_the_u_turn(self):
        """The hairpin masquerade is confined to DNATed local traffic.

        An instance reaching another instance's floating address is
        DNATed here and sent straight back out the veth it arrived on;
        the reply goes directly over the virtual network's L2 and is
        never un-DNATed, so the connection hangs. Masquerading the u-turn
        puts this namespace back on the return path.

        Both of the matches beside it are load bearing, and each
        excludes a different thing. Without -s the rule would also
        rewrite traffic arriving from outside the cluster for a floating
        address -- that is DNATed here too, and it reaches the holder
        with the caller's real address today. Without --ctstate DNAT it
        would catch anything this namespace forwards back into the
        network without rewriting it, a routed address included, whose
        whole point is that it is not rewritten.
        """
        recorder = self.patch_commands(RULE_ABSENT)

        self._enable_nat()

        hairpin = [c for c in self._appends(recorder)
                   if 'conntrack' in c]
        self.assertEqual(1, len(hairpin))
        rule = hairpin[0]
        self.assertEqual(('--ctstate', 'DNAT'),
                         rule[rule.index('--ctstate'):
                              rule.index('--ctstate') + 2])
        self.assertEqual('172.16.0.0/255.255.255.0',
                         rule[rule.index('-s') + 1])
        self.assertEqual(VX_VETH, rule[rule.index('-o') + 1])
        self.assertEqual('MASQUERADE', rule[-1])

    def test_an_existing_rule_is_not_appended_again(self):
        """A rule which -C finds is left alone.

        _enable_nat runs again every time the maintain loop recreates a
        network on the network node. Appending unconditionally accumulates
        duplicates, and the first match wins, so a duplicate laid down by
        an earlier pass would go on masking whatever the rule beside it
        later became.
        """
        recorder = self.patch_commands()

        reply = self._enable_nat()

        self.assertEqual(
            privexec_pb2.EnableNATReply.OK, reply.enable_nat_reply.error)
        self.assertEqual([], self._appends(recorder))
        self.assertEqual(4, len([c for c in recorder.calls if '-C' in c]))

    def test_the_audited_rule_is_one_of_the_installed_rules(self):
        """What is checked for later is what is installed here.

        ``Network.is_nat_okay`` audits a namespace for the hairpin rule,
        which is how a cluster that upgraded while all of its networks
        were healthy ever acquires it. The audit asks
        ``hairpin_masquerade_rule`` and the install walks
        ``network_nat_rules``; this is the assertion that the first is
        still one of the second, so an edit to either cannot leave the
        audit hunting for a rule nothing writes.
        """
        rules = util_iptables.network_nat_rules(
            '172.16.0.0', '255.255.255.0', VXID)
        hairpin = util_iptables.hairpin_masquerade_rule(
            '172.16.0.0', '255.255.255.0', VXID)

        self.assertIn((util_iptables.HAIRPIN_TABLE, hairpin), rules)

        # And the install really does write it, rather than the pairing
        # above being true of a list nothing uses.
        recorder = self.patch_commands(RULE_ABSENT)
        self._enable_nat()
        self.assertIn(
            ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
             '-t', util_iptables.HAIRPIN_TABLE, '-A', *hairpin),
            self._appends(recorder))

    def test_a_failed_append_stops_and_reports_iptables_failed(self):
        recorder = self.patch_commands({
            '-C': ('', '', 1),
            '-A FORWARD -o %s' % EGRESS_VETH: (
                '', 'iptables: No chain/target/match by that name.\n', 1),
        })

        reply = self._enable_nat()

        self.assertEqual(
            privexec_pb2.EnableNATReply.IPTABLES_FAILED,
            reply.enable_nat_reply.error)
        # The reply says which rule failed and why, rather than just
        # "iptables failed".
        self.assertIn(
            'No chain/target/match by that name',
            reply.enable_nat_reply.error_text)
        self.assertIn('FORWARD -o %s' % EGRESS_VETH,
                      reply.enable_nat_reply.error_text)
        # The first rule failed, so nothing after it was attempted.
        self.assertEqual(1, len(self._appends(recorder)))


class NetnsIptablesHelperTestCase(base.ShakenFistTestCase):
    """The two rule helpers, tested as helpers.

    Both are exercised through ``_enable_nat`` above, but that only ever
    walks them over the rule table. Their contracts -- "present, and
    here is why not if it is not" and "every copy is gone" -- belong to
    them, and the next caller (``_add_floating_ip`` is already one) gets
    those contracts and not the rule table.
    """

    def setUp(self):
        super().setUp()

        self.locate_command = mock.patch(
            'shakenfist.daemons.privexec.util.locate_command',
            side_effect=lambda c: c)
        self.locate_command.start()
        self.addCleanup(self.locate_command.stop)

    def patch_commands(self, results=None):
        recorder = CommandRecorder(results)
        patcher = mock.patch(
            'shakenfist.daemons.privexec.util.command_helper',
            side_effect=recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        return recorder

    RULE = ['FORWARD', '-i', EGRESS_VETH, '-o', VX_VETH, '-j', 'ACCEPT']

    def test_ensure_leaves_an_existing_rule_alone(self):
        recorder = self.patch_commands()

        present, error_text = privexec_main.ensure_netns_iptables_rule(
            NETWORK_UUID, 'filter', self.RULE)

        self.assertTrue(present)
        self.assertEqual('', error_text)
        self.assertEqual([], [c for c in recorder.calls if '-A' in c])

    def test_ensure_appends_an_absent_rule(self):
        recorder = self.patch_commands(RULE_ABSENT)

        present, error_text = privexec_main.ensure_netns_iptables_rule(
            NETWORK_UUID, 'filter', self.RULE)

        self.assertTrue(present)
        self.assertEqual('', error_text)
        self.assertEqual(
            [('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
              '-t', 'filter', '-A', *self.RULE)],
            [c for c in recorder.calls if '-A' in c])

    def test_ensure_reports_why_an_append_failed(self):
        """The caller gets iptables' complaint, not just "it failed".

        This is the only thing which distinguishes a kernel with no
        conntrack match from a namespace which has gone away, and it is
        what ``EnableNATFailed`` carries to the operator's ErrorReport.
        """
        self.patch_commands(dict(
            RULE_ABSENT,
            **{'-A': ('', 'iptables: No chain/target/match by that name.\n',
                      1)}))

        present, error_text = privexec_main.ensure_netns_iptables_rule(
            NETWORK_UUID, 'filter', self.RULE)

        self.assertFalse(present)
        self.assertIn('No chain/target/match by that name', error_text)
        self.assertIn(' '.join(self.RULE), error_text)
        self.assertIn(NETWORK_UUID, error_text)
        self.assertIn('filter', error_text)

    def test_remove_tolerates_an_absent_rule(self):
        recorder = self.patch_commands({
            '-D': ('', 'iptables: Bad rule (does a matching rule exist in '
                       'that chain?).\n', 1)})

        removed = privexec_main.remove_netns_iptables_rule(
            NETWORK_UUID, 'filter', self.RULE)

        self.assertEqual(0, removed)
        self.assertEqual(1, len(recorder.calls))

    def test_remove_drains_every_copy(self):
        recorder = self.patch_commands({
            '-D': [('', '', 0), ('', '', 0),
                   ('', 'iptables: Bad rule (does a matching rule exist in '
                        'that chain?).\n', 1)]})

        removed = privexec_main.remove_netns_iptables_rule(
            NETWORK_UUID, 'filter', self.RULE)

        self.assertEqual(2, removed)
        self.assertEqual(3, len(recorder.calls))
        for call in recorder.calls:
            self.assertEqual(
                ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
                 '-t', 'filter', '-D', *self.RULE), call)

    def test_remove_is_bounded(self):
        recorder = self.patch_commands()

        removed = privexec_main.remove_netns_iptables_rule(
            NETWORK_UUID, 'filter', self.RULE)

        self.assertEqual(32, removed)
        self.assertEqual(32, len(recorder.calls))


class ConcurrencyEnableNATTestCase(base.ShakenFistTestCase):
    """The client side must surface the error detail from the reply.

    ``_enable_nat`` goes to the trouble of putting iptables' own
    complaint about the rule it refused into ``error_text``, which is
    the only thing which distinguishes "this kernel has no conntrack
    match" from "the namespace has gone away". A bare
    ``EnableNATFailed()`` threw all of that away and left the operator
    an ErrorReport reading ``network.nat.enable_failed`` and nothing
    else.
    """

    def _reply(self, error, error_text):
        return privexec_pb2.PrivExecReply(
            enable_nat_reply=privexec_pb2.EnableNATReply(
                network_uuid=NETWORK_UUID,
                network_address='172.16.0.0',
                network_mask='255.255.255.0',
                vxid=VXID,
                error=error,
                error_text=error_text))

    def test_enable_nat_raises_with_detail(self):
        reply = self._reply(
            privexec_pb2.EnableNATReply.IPTABLES_FAILED,
            'failed to append POSTROUTING -s 172.16.0.0/255.255.255.0 -o '
            'veth-e2300f-i -m conntrack --ctstate DNAT -j MASQUERADE to the '
            'nat table in namespace %s: iptables: No chain/target/match by '
            'that name.' % NETWORK_UUID)
        with mock.patch(
                'shakenfist.util.concurrency._marshal_privexec_request',
                return_value=reply):
            exc = self.assertRaises(
                exceptions.EnableNATFailed,
                util_concurrency.enable_nat,
                NETWORK_UUID, '172.16.0.0', '255.255.255.0', VXID)

        self.assertEqual('IPTABLES_FAILED', exc.error)
        self.assertIn('No chain/target/match by that name', exc.error_text)
        self.assertEqual(NETWORK_UUID, exc.network_uuid)

        # And all of it reaches the message, which is what an
        # ErrorReport records.
        self.assertIn('IPTABLES_FAILED', str(exc))
        self.assertIn('conntrack', str(exc))
        self.assertIn(NETWORK_UUID, str(exc))

    def test_enable_nat_is_quiet_on_ok(self):
        reply = self._reply(privexec_pb2.EnableNATReply.OK, '')
        with mock.patch(
                'shakenfist.util.concurrency._marshal_privexec_request',
                return_value=reply):
            self.assertIsNone(util_concurrency.enable_nat(
                NETWORK_UUID, '172.16.0.0', '255.255.255.0', VXID))
