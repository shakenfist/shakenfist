# Copyright 2019 Michael Still and contributors

"""The 507-transient-capacity-refusal helper, standalone.

Step 4a of
``docs/plans/PLAN-transient-capacity-refusals-phase-04-retry-after.md``.
No caller is wired to ``transient_capacity_error()`` yet -- that is
step 4c's job -- so this module exercises the helper directly rather
than through a Flask app or a real endpoint.

D28 assembles the response body a second time, in this repository,
because ``sf_api.error()`` itself lives in ``shakenfist-utilities`` (a
pinned third-party dependency) and cannot be edited here.
``transient_capacity_error()`` replaces ``sf_api.error()``'s body
wholesale rather than patching it (D28's "re-serialise rather than
patch"), so a ``shakenfist-utilities`` upgrade that renames or
restructures the ``error``/``status`` keys cannot drop our
``stage``/``transient`` fields -- they are never derived from the
upstream body in the first place. The real risk is the inverse: our
507 quietly becoming the only error response in the API whose
``error``/``status`` keys no longer match the other 305
``sf_api.error()`` call sites, because nothing here would notice a
drift in a shape we only mirror. ``test_sf_api_error_shape_canary``
below exists for that: it asserts ``sf_api.error()``'s own body
directly, so that drift fails a unit test in this repository rather
than shipping a 507 quietly out of step with every other endpoint.

The other tests assert the *whole* decoded body of
``transient_capacity_error()`` by equality, not by membership of the
two new keys, so that a bug in this module's own re-serialisation
fails here too.
"""

import json

from shakenfist_utilities import api as sf_api

from shakenfist.external_api import base as api_base
from shakenfist.tests import base


class TransientCapacityErrorTestCase(base.ShakenFistTestCase):
    def test_body_and_headers(self):
        resp = api_base.transient_capacity_error(
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

    def test_capacity_guard_stage(self):
        # The admission-guard branch (external_api/instance.py:1017, wired
        # in step 4c) has no single filter stage to report, and passes the
        # literal 'capacity_guard' per D30. The helper does not know or
        # care what string it is handed; this pins that any caller-chosen
        # stage round-trips unchanged.
        resp = api_base.transient_capacity_error(
            'schedule failed, every candidate refused by capacity guard',
            'capacity_guard')

        self.assertEqual(
            {
                'error': 'schedule failed, every candidate refused by capacity guard',
                'status': 507,
                'stage': 'capacity_guard',
                'transient': True,
            },
            json.loads(resp.get_data(as_text=True)))

    def test_retry_after_constant(self):
        # D31: 15 seconds, fixed, matching BaseOperation.defer()'s default
        # delay (shakenfist/operations/baseoperation.py:674-678) so the
        # number the client is told and the number the server itself
        # waits before re-examining deferred work are the same number.
        self.assertEqual(15, api_base.TRANSIENT_RETRY_AFTER_SECONDS)

    def test_sf_api_error_shape_canary(self):
        """Pin the shape of a third-party function, not of our own code.

        ``sf_api.error()`` (``shakenfist_utilities.api.error``) is not
        ours to change -- it is pinned at ``pyproject.toml:38`` -- and
        ``transient_capacity_error()`` deliberately mirrors its
        ``{'error': ..., 'status': ...}`` shape rather than extending
        it, per D28. Nothing else in this repository asserts that
        shape directly, so a ``shakenfist-utilities`` upgrade that
        renames or restructures it would otherwise go unnoticed until
        our 507 was the only error response in the API that looked
        different from the other 305 ``sf_api.error()`` call sites.
        This canary fails first, here, and says "re-sync" instead.
        """
        resp = sf_api.error(507, 'some message', suppress_traceback=True)

        self.assertEqual(
            {'error': 'some message', 'status': 507},
            json.loads(resp.get_data(as_text=True)))
