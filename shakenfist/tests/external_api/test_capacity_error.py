# Copyright 2019 Michael Still and contributors

"""The 507 capacity-refusal helper, standalone.

Step 4a of
``docs/plans/PLAN-transient-capacity-refusals-phase-04-retry-after.md``,
as amended by the phase's first review round. This module exercises
``capacity_error()`` directly rather than through a Flask app or a real
endpoint; the wiring to the two create-path branches is tested in
``tests/test_external_api.py``.

D28 assembles the response body a second time, in this repository,
because ``sf_api.error()`` itself lives in ``shakenfist-utilities`` (a
pinned third-party dependency) and cannot be edited here.
``capacity_error()`` replaces ``sf_api.error()``'s body wholesale
rather than patching it (D28's "re-serialise rather than patch"), so a
``shakenfist-utilities`` upgrade that renames or restructures the
``error``/``status`` keys cannot drop our ``stage``/``transient``
fields -- they are never derived from the upstream body in the first
place. The real risk is the inverse: our 507 quietly becoming the only
error response in the API whose ``error``/``status`` keys no longer
match the other 305 ``sf_api.error()`` call sites, because nothing here
would notice a drift in a shape we only mirror.
``test_sf_api_error_shape_canary`` below exists for that.

The body tests assert the *whole* decoded body by equality, not by
membership of the new keys, so that a bug in this module's own
re-serialisation fails here too.
"""

import ast
import inspect
import json
import os

from shakenfist_utilities import api as sf_api

from shakenfist.constants import CAPACITY_GUARD_STAGE
from shakenfist.external_api import base as api_base
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.tests import base


class CapacityErrorTestCase(base.ShakenFistTestCase):
    def test_transient_body_and_headers(self):
        resp = api_base.capacity_error(
            'no nodes remaining at scheduling stage sufficient_idle_cpu',
            'sufficient_idle_cpu')

        self.assertEqual(507, resp.status_code)
        self.assertEqual(
            str(api_base.TRANSIENT_RETRY_AFTER_SECONDS),
            resp.headers.get('Retry-After'))

        # Whole-body equality, not assertIn / per-key membership: see the
        # module docstring and D28's risk note in the phase 4 plan.
        self.assertEqual(
            {
                'error': 'no nodes remaining at scheduling stage sufficient_idle_cpu',
                'status': 507,
                'stage': 'sufficient_idle_cpu',
                'transient': True,
            },
            json.loads(resp.get_data(as_text=True)))

    def test_permanent_stage_is_refused_without_a_retry(self):
        # cpu_max_per_instance fires when the request asks for more
        # vCPUs than any node's per-instance maximum. Deleting every
        # instance in the cluster would not satisfy it, so the refusal
        # is published with the stage intact but no promise of
        # retry-worthiness and no Retry-After for a client to sleep on.
        resp = api_base.capacity_error(
            'No nodes remaining at scheduling stage cpu_max_per_instance',
            'cpu_max_per_instance')

        self.assertEqual(507, resp.status_code)
        self.assertNotIn('Retry-After', resp.headers)
        self.assertEqual(
            {
                'error': 'No nodes remaining at scheduling stage cpu_max_per_instance',
                'status': 507,
                'stage': 'cpu_max_per_instance',
                'transient': False,
            },
            json.loads(resp.get_data(as_text=True)))

    def test_unrecognised_stage_is_not_transient(self):
        # Including 'unknown', the create path's fallback for a refusal
        # that arrived carrying no stage at all. Refusing to promise
        # retry-worthiness is the safe direction to be wrong in: the
        # cost is one refusal a client could have retried, against a
        # client spinning on an impossible request until its deadline.
        for stage in ['unknown', 'a_stage_nobody_has_classified']:
            resp = api_base.capacity_error('refused', stage)

            self.assertNotIn('Retry-After', resp.headers)
            self.assertEqual(
                {
                    'error': 'refused',
                    'status': 507,
                    'stage': stage,
                    'transient': False,
                },
                json.loads(resp.get_data(as_text=True)))

    def test_capacity_guard_stage(self):
        # The admission-guard branch has no single filter stage to
        # report, and publishes the constant per D30. It is transient
        # today because a claim cannot refuse a placement while
        # mariadb.CLAIM_ENFORCEMENT_HARD is False.
        resp = api_base.capacity_error(
            'schedule failed, every candidate refused by capacity guard',
            CAPACITY_GUARD_STAGE)

        self.assertEqual(
            str(api_base.TRANSIENT_RETRY_AFTER_SECONDS),
            resp.headers.get('Retry-After'))
        self.assertEqual(
            {
                'error': 'schedule failed, every candidate refused by capacity guard',
                'status': 507,
                'stage': 'capacity_guard',
                'transient': True,
            },
            json.loads(resp.get_data(as_text=True)))

    def test_retry_after_constant(self):
        # D31: 15 seconds, fixed.
        self.assertEqual(15, api_base.TRANSIENT_RETRY_AFTER_SECONDS)

    def test_retry_after_matches_the_defer_default(self):
        """Pin the second literal the constant's comment appeals to.

        ``TRANSIENT_RETRY_AFTER_SECONDS``' comment justifies 15 by
        saying it is how long the server itself waits before
        re-examining deferred work. That is two independently written
        literals, not one definition: nothing imports the other. This
        asserts the claim rather than trusting it, so moving either one
        fails here instead of leaving the comment quietly untrue.
        """
        default = inspect.signature(BaseClusterOperation.defer).parameters['delay'].default

        self.assertEqual(15.0, default)
        self.assertEqual(
            float(api_base.TRANSIENT_RETRY_AFTER_SECONDS), float(default))

    def test_every_scheduler_stage_is_classified(self):
        """No scheduler filter stage may go unclassified.

        The two sets in ``external_api/base.py`` are written by hand,
        and a stage added to or renamed in ``scheduler.py`` would
        otherwise fall silently into the non-transient default -- a
        genuine capacity shortage published as not worth retrying, with
        nothing to notice. Derive the stage names from the source, the
        way ``test_openapi_spec.py`` derives its completeness from the
        published specification.

        The ``affinity_constraints`` stage is excluded because it
        passes an ``exception_class``: it raises
        ``AffinityConstraintUnsatisfiable``, which the create path
        answers with a 409 and never reaches this helper.
        """
        from shakenfist import scheduler

        source = os.path.abspath(scheduler.__file__)
        tree = ast.parse(open(source).read())

        stages = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != '_log_and_raise_on_error':
                continue
            if any(kw.arg == 'exception_class' for kw in node.keywords):
                continue
            stage = node.args[1]
            self.assertIsInstance(
                stage, ast.Constant,
                'A scheduler stage name is no longer a literal, so this '
                'check can no longer enumerate the stages')
            stages.add(stage.value)

        # A sanity floor: if the AST walk silently stopped matching, an
        # empty set would pass the comparison below vacuously.
        self.assertIn('sufficient_idle_cpu', stages)
        self.assertIn('cpu_max_per_instance', stages)

        classified = (api_base.TRANSIENT_CAPACITY_STAGES
                      | api_base.PERMANENT_CAPACITY_STAGES)
        self.assertEqual(
            set(), stages - classified,
            'Scheduler stages which reach capacity_error() but are in '
            'neither TRANSIENT_CAPACITY_STAGES nor '
            'PERMANENT_CAPACITY_STAGES; they will be published as not '
            'worth retrying, which may be wrong')

        # The guard stage is not a scheduler stage, so it is the only
        # classified name that may be absent from the source walk.
        self.assertEqual(
            {CAPACITY_GUARD_STAGE}, classified - stages,
            'A classified stage name no longer matches any raise site in '
            'scheduler.py')

    def test_sf_api_error_shape_canary(self):
        """Pin the shape of a third-party function, not of our own code.

        ``sf_api.error()`` (``shakenfist_utilities.api.error``) is not
        ours to change -- it is pinned at ``pyproject.toml:38`` -- and
        ``capacity_error()`` deliberately mirrors its ``{'error': ...,
        'status': ...}`` shape rather than extending it, per D28.
        Nothing else in this repository asserts that shape directly, so
        a ``shakenfist-utilities`` upgrade that renames or restructures
        it would otherwise go unnoticed until our 507 was the only
        error response in the API that looked different from the other
        305 ``sf_api.error()`` call sites. This canary fails first,
        here, and says "re-sync" instead.
        """
        resp = sf_api.error(507, 'some message', suppress_traceback=True)

        self.assertEqual(
            {'error': 'some message', 'status': 507},
            json.loads(resp.get_data(as_text=True)))
