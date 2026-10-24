import time

from testtools import content

from shakenfist_ci import base


# The cluster daemon runs per_instance_checks_and_usage() every five minutes
# (daemons/cluster/main.py). A run refills its queue only once the queue is
# empty, then works through it for at most ten seconds. On a CI-sized cluster
# the queue drains in one run, so the next run after the instance is created
# reaches it, at most five minutes in. Eight minutes leaves margin for a run
# that starts late or has a queue left over from the previous one.
SWEEP_WAIT = 8 * 60

POLL_INTERVAL = 10

# Once the sweep op is found, how long it may take to finish. Finished ops are
# hard deleted shortly afterwards, so "gone" also counts as finished.
SWEEP_FINISH_WAIT = 60

SWEEP_TASKS = {'collect_billing_statistics', 'health_check_kvm_process'}
TERMINAL_STATES = {'complete', 'deleted', 'abort', 'error'}


class TestObservationalSweeps(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'obssweep'
        super().__init__(*args, **kwargs)

    def _view(self, instance_uuid):
        # The system client is used for both reads, as the await helpers
        # do: it can see every namespace, and the listing is the same
        # data whichever client asks for it.
        return (
            self.system_client.get_instance(instance_uuid),
            self.system_client.list_cluster_operations_for_target(
                'instance', instance_uuid))

    def _check_pointer(self, instance, observational_uuids):
        pointer = instance.get('last_cluster_operation')
        if pointer is not None:
            self.assertNotIn(
                pointer['op_uuid'], observational_uuids,
                'last_cluster_operation names an observational operation')

    def test_sweep_op_is_observational(self):
        inst = self.create_instance(
            'test-observational-sweeps', 1, 1024, [],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self._await_instance_ready(inst['uuid'])
        self._await_instance_operations_complete(inst['uuid'])

        # An operation can vanish from the listing between polls, so the
        # uuids marked observational are accumulated rather than taken from
        # the current listing alone.
        observational_uuids = set()
        sweep = None
        start = time.time()
        while time.time() - start < SWEEP_WAIT:
            instance, ops = self._view(inst['uuid'])
            observational_uuids.update(
                op['uuid'] for op in ops if op.get('observational'))
            self._check_pointer(instance, observational_uuids)

            active = [op for op in ops if op['state'] not in TERMINAL_STATES]
            if active and all(op.get('observational') for op in active):
                self.assertFalse(
                    instance.get('has_pending_operations'),
                    'has_pending_operations is true with only observational '
                    'operations outstanding')

            found = [op for op in ops if SWEEP_TASKS.issubset(op['tasks'])]
            if found:
                sweep = found[0]
                break
            time.sleep(POLL_INTERVAL)

        waited = time.time() - start
        self.addDetail(
            'sweep_wait_seconds',
            content.text_content('%.1f' % waited))
        if sweep is None:
            self.fail(
                'The per-instance sweep never reached instance %s within %d '
                'seconds' % (inst['uuid'], SWEEP_WAIT))

        self.assertTrue(
            sweep.get('observational'),
            'The sweep operation %s is not marked observational'
            % sweep['uuid'])
        observational_uuids.add(sweep['uuid'])

        # Wait for the sweep op to finish or be deleted. How it finishes is
        # not this test's concern: an op in error is a separate problem, and
        # this test is only about how the op is classified.
        deadline = time.time() + SWEEP_FINISH_WAIT
        while True:
            instance, ops = self._view(inst['uuid'])
            observational_uuids.update(
                op['uuid'] for op in ops if op.get('observational'))
            self._check_pointer(instance, observational_uuids)
            current = [op for op in ops if op['uuid'] == sweep['uuid']]
            if not current or current[0]['state'] in TERMINAL_STATES:
                break
            if time.time() > deadline:
                self.fail(
                    'The sweep operation %s did not finish within %d seconds'
                    % (sweep['uuid'], SWEEP_FINISH_WAIT))
            time.sleep(POLL_INTERVAL)

        self.assertFalse(
            instance.get('has_pending_operations'),
            'has_pending_operations is true after the sweep finished')
