# Copyright 2026 Michael Still and contributors

"""Tests for the leftover-process filter in shakenfist/deploy/nodelifecycletests.sh.

The node lifecycle suite gracefully stops Shaken Fist on a victim node and then
asserts that no Shaken Fist process survived. That assertion used to be `ps -ef
| grep sf`, which matched any command line containing those two letters and
failed the job about four times a day for two weeks on a log-shipping helper
that merely mentions the hostname prefix sfcbr-* (issue #4209).

A grep in a CI shell script is otherwise untestable, and that is precisely why
it stayed wrong for so long, so the real function is extracted from the script
and run under bash here. Extracting rather than restating it means the test
cannot drift from the code it checks: a change to the pattern is a change to
what these cases assert.

The listing below is the shape `ps -ef` produces on a slim-tier CI node, and
the hostname-guard line is copied verbatim from the failure in #4209.
"""

import os
import subprocess
import tempfile

from shakenfist.tests import base


FUNCTION_NAME = 'sf_processes'

# Every one of these must be reported as a surviving Shaken Fist process. The
# process titles come from DAEMON_NAMES and the literal setproctitle() calls in
# shakenfist/daemons/, and the gunicorn forms from sf-api.service's --name.
SF_PROCESSES = [
    'root        1201       1  0 10:00 ?        00:00:01 sf-queues',
    'root        1202       1  0 10:00 ?        00:00:00 sf-queues startup',
    'root        1203       1  0 10:00 ?        00:00:00 sf-privexec',
    'root        1204       1  0 10:00 ?        00:00:00 sf-sentinel-last',
    'root        1205       1  0 10:00 ?        00:00:02 sf-net',
    'root        1206       1  0 10:00 ?        00:00:00 gunicorn: master [sf-api]',
    'root        1207    1206  0 10:00 ?        00:00:03 gunicorn: worker [sf-api]',
    ('root        1208       1  0 10:00 ?        00:00:00 /bin/sh -c '
     '/srv/shakenfist/venv/bin/gunicorn --workers 5 --name "sf-api" --preload'),
    ('root        1209       1  0 10:00 ?        00:00:00 '
     '/srv/shakenfist/venv/bin/python3 /srv/shakenfist/venv/bin/sf-cleaner'),
]

# None of these may be reported. The first is the false positive from #4209,
# copied from the failing job's output; the qemu line is a running instance,
# which is expected to survive a Shaken Fist shutdown and whose argv names the
# guest agent's virtio-serial channel sf-agent.
NOT_SF_PROCESSES = [
    ('root        3540       1  0 19:40 ?        00:00:00 /bin/bash -c for i in $(seq 1 600); do case '
     '"$(hostname)" in sfcbr-*) exit 0 ;; esac; sleep 2; done; echo "hostname still not sfcbr-* after '
     '20m; refusing to ship mislabelled logs, will retry" >&2; exit 1'),
    'root           2       0  0 10:00 ?        00:00:00 [kthreadd]',
    'root         145       2  0 10:00 ?        00:00:00 [ata_sff]',
    'root         310       2  0 10:00 ?        00:00:00 [kvm-nx-lpage-recovery-0]',
    ('libvirt+    9000       1  5 10:00 ?        00:05:00 /usr/bin/qemu-system-x86_64 -name '
     'guest=sf4-1,debug-threads=on -drive file=/srv/shakenfist/instances/abc/disk.qcow2 -device '
     'virtserialport,chardev=charchannel0,id=channel0,name=sf-agent'),
    'debian      9100    9099  0 10:00 ?        00:00:00 sudo ps -ef',
    'debian      9101    9100  0 10:00 ?        00:00:00 ps -ef',
    'root         400       1  0 10:00 ?        00:00:00 /usr/sbin/sshd -D',
    'root         401       1  0 10:00 ?        00:00:00 /lib/systemd/systemd-journald',
]


def _script_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), 'deploy', 'nodelifecycletests.sh')


def _extract_function():
    """Return the text of the sf_processes function as the script defines it.

    The script cannot be sourced -- it would run the whole suite -- so the one
    function under test is lifted out by brace matching from its definition to
    the line that closes it.
    """
    with open(_script_path()) as f:
        lines = f.read().split('\n')

    start = None
    for i, line in enumerate(lines):
        if line.startswith('function %s {' % FUNCTION_NAME):
            start = i
            break
    if start is None:
        raise AssertionError('%s is not defined in %s' % (FUNCTION_NAME, _script_path()))

    for i in range(start + 1, len(lines)):
        if lines[i].strip() == '}':
            return '\n'.join(lines[start:i + 1])
    raise AssertionError('%s is never closed in %s' % (FUNCTION_NAME, _script_path()))


def _filter(listing):
    """Run the real function over a ps listing and return the lines it kept."""
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as f:
        f.write('#!/bin/bash\n%s\n%s\n' % (_extract_function(), FUNCTION_NAME))
        harness = f.name

    try:
        result = subprocess.run(
            ['bash', harness], input=listing, capture_output=True, text=True, check=True)
    finally:
        os.unlink(harness)

    return [line for line in result.stdout.split('\n') if line]


class NodeLifecycleShellTestCase(base.ShakenFistTestCase):
    def test_reports_every_shaken_fist_process(self):
        # Fed the whole listing, the filter keeps exactly the daemons. This is
        # the property the check exists for: a daemon that failed to stop must
        # fail the job.
        kept = _filter('\n'.join(SF_PROCESSES + NOT_SF_PROCESSES) + '\n')
        self.assertEqual(sorted(SF_PROCESSES), sorted(kept))

    def test_reports_nothing_on_a_stopped_node(self):
        # A node where Shaken Fist really did stop still runs plenty of things,
        # including the helper from #4209 and any instances it was hosting.
        self.assertEqual([], _filter('\n'.join(NOT_SF_PROCESSES) + '\n'))

    def test_reports_nothing_for_an_empty_listing(self):
        # grep exits non-zero on no match, which the function must swallow --
        # the caller counts lines and would otherwise never see this case.
        self.assertEqual([], _filter(''))

    def test_hostname_guard_alone_does_not_trip_the_check(self):
        # The exact regression from #4209, isolated so a future change to the
        # pattern names this failure rather than a count mismatch.
        guard = NOT_SF_PROCESSES[0]
        self.assertIn('sfcbr-', guard)
        self.assertEqual([], _filter(guard + '\n'))

    def test_each_process_is_classified_individually(self):
        # Fed one line at a time, so that a pattern which only works in the
        # presence of its neighbours cannot pass.
        for line in SF_PROCESSES:
            self.assertEqual([line], _filter(line + '\n'), 'should have matched: %s' % line)
        for line in NOT_SF_PROCESSES:
            self.assertEqual([], _filter(line + '\n'), 'should not have matched: %s' % line)
