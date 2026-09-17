# Copyright 2019 Michael Still and contributors
"""``BaseTestCase.assertRefusedAtStage()``'s phase 4 contract, standalone.

Step 4f of
``docs/plans/PLAN-transient-capacity-refusals-phase-04-retry-after.md``
(D34): the helper now asserts ``body['stage']`` and
``body['transient'] is True`` unconditionally, and asserts a
``Retry-After`` header only when the exception exposes one.

``shakenfist_ci/base.py`` is loaded the same way
``test_ci_capacity_wait.py`` loads it -- by path, with
``shakenfist_client`` and ``prettytable`` stubbed out, because neither
is a test dependency of this repository. The stub ``APIException``
here additionally accepts and stores a ``headers`` keyword, mirroring
client-python's unreleased ``headers=None`` addition (commit 80019a0 on
its ``transient-capacity-refusals-phase-04-client`` branch), so both
the "old client, no headers attribute" and "new client, headers
present" shapes can be exercised.

``assertRefusedAtStage()`` is only ever called bound to a real
``testtools.TestCase`` (it uses ``self.assertEqual`` etc.), so these
tests call it unbound -- ``ci_base.BaseTestCase.assertRefusedAtStage(self,
...)`` -- passing a ``ShakenFistTestCase`` instance as ``self`` rather
than running the suite's own heavyweight ``setUp()``.
"""

import json
import logging
import os
import sys
import types

from shakenfist.tests import base as test_base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')


def _client_stubs():
    """A shakenfist_client stub whose APIException can carry headers.

    Unlike ``test_ci_capacity_wait.py``'s stub -- which predates step 4d
    and has no ``headers`` slot at all -- this one accepts
    ``headers=None`` and stores ``self.headers = headers or {}``,
    matching client-python's unreleased shape exactly, so a test here
    can construct both a pre-4d-shaped exception (omit the argument,
    which still yields an empty ``{}`` under that stub -- see
    ``NoHeadersAttributeAPIException`` below for the "attribute does
    not exist at all" shape) and a post-4d one carrying a real header.
    """
    client = types.ModuleType('shakenfist_client')
    apiclient = types.ModuleType('shakenfist_client.apiclient')

    class APIException(Exception):
        def __init__(self, message='', method=None, url=None,
                     status_code=None, text=None, headers=None):
            super().__init__(message)
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

    apiclient.APIException = APIException
    for name in ('InsufficientResourcesException',
                 'ResourceStateConflictException',
                 'ResourceNotFoundException',
                 'RequestMalformedException',
                 'ServiceUnavailableException',
                 'IncapableException',
                 'AgentCommandError',
                 'AgentOperationFailed',
                 'AgentAwaitTimeout'):
        setattr(apiclient, name, type(name, (APIException,), {}))
    apiclient.ASYNC_PAUSE = 'pause'
    apiclient.Client = type('Client', (), {'__init__': lambda self, *a, **kw: None})

    client.apiclient = apiclient
    return client, apiclient


def _load_ci_base():
    """Import the functional suite's base.py without its dependencies.

    Everything mutated here is put back, because stestr runs the whole
    unit suite in one process and a stubbed shakenfist_client left in
    sys.modules would be a trap for a later test rather than a
    convenience for this one. Copied from ``test_ci_capacity_wait.py``'s
    helper of the same name/shape.
    """
    stub_names = ('prettytable', 'shakenfist_client',
                  'shakenfist_client.apiclient', 'shakenfist_ci',
                  'shakenfist_ci.base', 'shakenfist_ci.process',
                  'shakenfist_ci.retries')
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in stub_names}
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level

    try:
        sys.path.insert(0, os.path.dirname(CI_SUITE))
        for name in stub_names:
            sys.modules.pop(name, None)

        prettytable = types.ModuleType('prettytable')
        prettytable.PrettyTable = type('PrettyTable', (), {})
        sys.modules['prettytable'] = prettytable

        client, apiclient = _client_stubs()
        sys.modules['shakenfist_client'] = client
        sys.modules['shakenfist_client.apiclient'] = apiclient

        import shakenfist_ci.base as ci_base
        return ci_base, apiclient

    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


ci_base, ci_apiclient = _load_ci_base()


def _body(stage, transient=True, detail=None):
    error = f'No nodes remaining at scheduling stage {stage}'
    if detail:
        error = f'{error}: {detail}'
    return {'error': error, 'status': 507, 'stage': stage, 'transient': transient}


class AssertRefusedAtStageTestCase(test_base.ShakenFistTestCase):
    """Exercises the helper unbound, per the module docstring."""

    def _assert_refused(self, response, stage):
        return ci_base.BaseTestCase.assertRefusedAtStage(self, response, stage)

    def test_body_fields_pass_with_no_headers_attribute(self):
        # A response object with no .headers at all -- getattr()'s
        # fallback path, modelling a client older than commit 80019a0.
        response = ci_apiclient.InsufficientResourcesException(
            status_code=507, text=json.dumps(_body('sufficient_idle_cpu')))
        del response.headers

        detail = self._assert_refused(response, 'sufficient_idle_cpu')

        self.assertIsNone(detail)

    def test_stage_mismatch_fails(self):
        response = ci_apiclient.InsufficientResourcesException(
            status_code=507,
            text=json.dumps(_body('sufficient_idle_memory')))

        self.assertRaises(
            AssertionError, self._assert_refused, response,
            'sufficient_idle_cpu')

    def test_transient_false_fails(self):
        response = ci_apiclient.InsufficientResourcesException(
            status_code=507,
            text=json.dumps(_body('sufficient_idle_cpu', transient=False)))

        self.assertRaises(
            AssertionError, self._assert_refused, response,
            'sufficient_idle_cpu')

    def test_header_asserted_when_present_and_correct(self):
        response = ci_apiclient.InsufficientResourcesException(
            status_code=507,
            text=json.dumps(_body('sufficient_free_disk')),
            headers={'Retry-After': '15'})

        detail = self._assert_refused(response, 'sufficient_free_disk')

        self.assertIsNone(detail)

    def test_header_asserted_when_present_and_wrong_fails(self):
        response = ci_apiclient.InsufficientResourcesException(
            status_code=507,
            text=json.dumps(_body('sufficient_free_disk')),
            headers={'Retry-After': '30'})

        self.assertRaises(
            AssertionError, self._assert_refused, response,
            'sufficient_free_disk')

    def test_header_skipped_when_empty(self):
        # headers defaults to {} (falsy), same as an older-but-post-4d
        # client which built the exception without passing one -- the
        # guard is `if headers:`, not `if headers is not None:`.
        response = ci_apiclient.InsufficientResourcesException(
            status_code=507, text=json.dumps(_body('sufficient_idle_cpu')))

        detail = self._assert_refused(response, 'sufficient_idle_cpu')

        self.assertIsNone(detail)

    def test_tuple_form_has_no_headers_attribute_either(self):
        # test_namespace_claims.py::_claim_api()'s (status, body) shape:
        # a plain tuple has no .headers, so getattr() falls back and the
        # header assertion is skipped, same as the exception case above.
        response = (507, _body('sufficient_idle_memory', detail='node n1 full'))

        detail = self._assert_refused(response, 'sufficient_idle_memory')

        self.assertEqual('node n1 full', detail)

    def test_body_missing_the_marker_fails_as_an_assertion(self):
        # A server predating phase 4 returns the bare
        # {'error': ..., 'status': ...} body. The stage regexp still
        # matches, so the helper reaches the marker assertions with the
        # keys absent -- it must report that as a crafted failure, not
        # as a KeyError escaping from inside the helper.
        response = ci_apiclient.InsufficientResourcesException(
            status_code=507,
            text=json.dumps({
                'error': ('No nodes remaining at scheduling stage '
                          'sufficient_idle_cpu'),
                'status': 507,
            }))

        self.assertRaises(
            AssertionError, self._assert_refused, response,
            'sufficient_idle_cpu')

    def test_non_dict_body_fails_as_an_assertion(self):
        # A 507 whose body is not JSON at all -- a proxy's HTML error
        # page, say. The status assertion passes, the message assertion
        # is what should fail, and nothing below it may raise a
        # TypeError first.
        response = ci_apiclient.InsufficientResourcesException(
            status_code=507, text='<html>503 from the load balancer</html>')

        self.assertRaises(
            AssertionError, self._assert_refused, response,
            'sufficient_idle_cpu')
