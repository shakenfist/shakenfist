# Copyright 2019 Michael Still and contributors
#
# Parametrised tests for the arg_is_*_ref decorator family.
#
# The decorators (arg_is_instance_ref, arg_is_network_ref,
# arg_is_artifact_ref and arg_is_visible_artifact_ref) historically
# passed request_namespace() straight to
# Object.from_db_by_ref, which collapsed to "search every namespace" for
# system callers regardless of what namespace the request body asked for.
# A system admin invoking `client.get_instance(name, namespace='ns1')`
# could therefore receive a same-named object living in `ns2`.
#
# These tests pin the new contract:
#   * If the request body carries `namespace`, the lookup is strictly
#     scoped to it (both at the SQL layer for name lookups and via a
#     post-lookup check for UUID lookups).
#   * A non-system caller may not pass a foreign namespace.
#   * Without a body namespace, the existing behaviour ("system" sentinel
#     means cross-namespace) is preserved.

from unittest import mock

from shakenfist.external_api import artifact as api_artifact
from shakenfist.external_api import base as api_base
from shakenfist.tests import base


_REF = 'some-name'


def _fake_obj(namespace):
    obj = mock.MagicMock()
    obj.namespace = namespace
    return obj


class _DecoratorCase:
    """Bundle the wiring needed to exercise one *_ref decorator."""

    def __init__(self, kind, decorator, lookup_target, ref_kwarg, obj_kwarg):
        self.kind = kind
        self.decorator = decorator
        self.lookup_target = lookup_target
        self.ref_kwarg = ref_kwarg
        self.obj_kwarg = obj_kwarg


# All three decorators route through api_base.resolve_lookup_namespace, so
# patching base.request_namespace is sufficient regardless of which
# decorator is under test.
_REQUEST_NS_TARGET = 'shakenfist.external_api.base.request_namespace'


_CASES = [
    _DecoratorCase(
        kind='instance',
        decorator=api_base.arg_is_instance_ref,
        lookup_target='shakenfist.external_api.base.Instance.from_db_by_ref',
        ref_kwarg='instance_ref',
        obj_kwarg='instance_from_db',
    ),
    _DecoratorCase(
        kind='network',
        decorator=api_base.arg_is_network_ref,
        lookup_target=('shakenfist.external_api.base.network.Network.'
                       'from_db_by_ref'),
        ref_kwarg='network_ref',
        obj_kwarg='network_from_db',
    ),
    _DecoratorCase(
        kind='artifact',
        decorator=api_artifact.arg_is_artifact_ref,
        lookup_target='shakenfist.external_api.artifact.Artifact.from_db_by_ref',
        ref_kwarg='artifact_ref',
        obj_kwarg='artifact_from_db',
    ),
]


class ArgIsRefNamespaceScopingTestCase(base.ShakenFistTestCase):
    """Decorator-level namespace scoping for instance / network / artifact."""

    def _run(self, case, caller_ns, body_namespace, returned_obj_namespace):
        """Drive one decorator end-to-end and return (response, captured).

        The wrapped endpoint records its kwargs in `captured` so the test
        can assert what reached it; if the decorator short-circuits the
        endpoint is never called and `captured` stays empty.
        """
        captured = {}

        @case.decorator
        def endpoint(**kwargs):
            captured.update(kwargs)
            return 'ok'

        returned = _fake_obj(returned_obj_namespace)

        with mock.patch(_REQUEST_NS_TARGET, return_value=caller_ns), \
                mock.patch(case.lookup_target,
                           return_value=returned) as lookup:
            kwargs = {case.ref_kwarg: _REF}
            if body_namespace is not None:
                kwargs['namespace'] = body_namespace
            response = endpoint(**kwargs)

        return response, captured, lookup, returned

    # ------------------------------------------------------------------
    # Body namespace + system caller -> strict scope at lookup layer
    # ------------------------------------------------------------------

    def test_system_caller_body_namespace_scopes_lookup(self):
        for case in _CASES:
            response, captured, lookup, _ = self._run(
                case, caller_ns='system', body_namespace='ns1',
                returned_obj_namespace='ns1')
            self.assertEqual(
                'ok', response,
                f'{case.kind}: decorator should pass through, got {response!r}')
            lookup.assert_called_once_with(_REF, 'ns1')
            self.assertIn(case.obj_kwarg, captured)

    # ------------------------------------------------------------------
    # Body namespace == caller's own namespace -> proceed
    # ------------------------------------------------------------------

    def test_tenant_caller_own_namespace_scopes_lookup(self):
        for case in _CASES:
            response, _, lookup, _ = self._run(
                case, caller_ns='ns1', body_namespace='ns1',
                returned_obj_namespace='ns1')
            self.assertEqual(
                'ok', response,
                f'{case.kind}: matching-namespace tenant should succeed')
            lookup.assert_called_once_with(_REF, 'ns1')

    # ------------------------------------------------------------------
    # Body namespace != caller's namespace (tenant) -> 404 before lookup
    # ------------------------------------------------------------------

    def test_tenant_caller_foreign_namespace_rejected(self):
        for case in _CASES:
            response, captured, lookup, _ = self._run(
                case, caller_ns='ns1', body_namespace='ns2',
                returned_obj_namespace='ns2')
            self.assertEqual(
                404, response.status_code,
                f'{case.kind}: foreign-namespace tenant should be 404, '
                f'got {response!r}')
            lookup.assert_not_called()
            self.assertNotIn(case.obj_kwarg, captured)

    # ------------------------------------------------------------------
    # No body namespace, system caller -> 'system' goes through
    # (preserves the existing cross-namespace behaviour)
    # ------------------------------------------------------------------

    def test_system_caller_no_body_namespace_passes_system(self):
        for case in _CASES:
            response, _, lookup, _ = self._run(
                case, caller_ns='system', body_namespace=None,
                returned_obj_namespace='ns1')
            self.assertEqual(
                'ok', response,
                f'{case.kind}: unqualified system lookup should succeed')
            lookup.assert_called_once_with(_REF, 'system')

    # ------------------------------------------------------------------
    # No body namespace, tenant caller -> tenant ns passed through
    # ------------------------------------------------------------------

    def test_tenant_caller_no_body_namespace_passes_tenant(self):
        for case in _CASES:
            response, _, lookup, _ = self._run(
                case, caller_ns='ns1', body_namespace=None,
                returned_obj_namespace='ns1')
            self.assertEqual(
                'ok', response,
                f'{case.kind}: tenant unqualified lookup should succeed')
            lookup.assert_called_once_with(_REF, 'ns1')

    # ------------------------------------------------------------------
    # Body namespace supplied but resolved object lives elsewhere
    # (UUID lookup path: from_db_by_ref's namespace filter does not
    # apply, so the decorator's post-lookup check must reject)
    # ------------------------------------------------------------------

    def test_resolved_object_in_other_namespace_rejected(self):
        for case in _CASES:
            response, captured, lookup, _ = self._run(
                case, caller_ns='system', body_namespace='ns1',
                returned_obj_namespace='ns2')
            self.assertEqual(
                404, response.status_code,
                f'{case.kind}: UUID lookup returning foreign ns should 404, '
                f'got {response!r}')
            lookup.assert_called_once_with(_REF, 'ns1')
            self.assertNotIn(case.obj_kwarg, captured)

    # ------------------------------------------------------------------
    # Artifact-specific: arg_is_visible_artifact_ref widens a *name*
    # across everything the caller can see, while arg_is_artifact_ref
    # (covered by _CASES above) does not. Both are one liners over
    # _resolve_artifact_ref, so which of its two lookups runs is the
    # only part of that function _CASES cannot reach.
    # ------------------------------------------------------------------

    def _run_visible(self, caller_ns, body_namespace, returned_obj_namespace):
        """Drive arg_is_visible_artifact_ref, mocking both lookups.

        Patching both is the point: the assertion is which one ran, so
        a refactor that widened where it should not, or stopped
        widening where it should, fails here rather than silently
        answering the same 'ok'.
        """
        captured = {}

        @api_artifact.arg_is_visible_artifact_ref
        def endpoint(**kwargs):
            captured.update(kwargs)
            return 'ok'

        returned = _fake_obj(returned_obj_namespace)

        with mock.patch(_REQUEST_NS_TARGET, return_value=caller_ns), \
                mock.patch('shakenfist.external_api.artifact.Artifact'
                           '.from_db_by_ref_visible_to',
                           return_value=returned) as widened, \
                mock.patch('shakenfist.external_api.artifact.Artifact'
                           '.from_db_by_ref',
                           return_value=returned) as scoped:
            kwargs = {'artifact_ref': _REF}
            if body_namespace is not None:
                kwargs['namespace'] = body_namespace
            response = endpoint(**kwargs)

        return response, captured, widened, scoped

    def test_visible_ref_without_body_namespace_widens(self):
        # A shared artifact owned elsewhere reaches the handler: this
        # is the whole reason the widening variant exists, and the
        # post-lookup namespace check must not undo it when the caller
        # named no namespace.
        response, captured, widened, scoped = self._run_visible(
            caller_ns='ns1', body_namespace=None, returned_obj_namespace='ns2')
        self.assertEqual(
            'ok', response,
            f'unqualified visible lookup should succeed, got {response!r}')
        widened.assert_called_once_with(_REF, 'ns1')
        scoped.assert_not_called()
        self.assertIn('artifact_from_db', captured)

    def test_visible_ref_with_body_namespace_does_not_widen(self):
        # Naming a namespace turns the widening off whatever the route
        # asked for: that caller asked about one namespace and must be
        # answered from it or not at all.
        response, captured, widened, scoped = self._run_visible(
            caller_ns='system', body_namespace='ns1',
            returned_obj_namespace='ns1')
        self.assertEqual(
            'ok', response,
            f'scoped visible lookup should succeed, got {response!r}')
        scoped.assert_called_once_with(_REF, 'ns1')
        widened.assert_not_called()
        self.assertIn('artifact_from_db', captured)

    def test_visible_ref_tenant_foreign_namespace_rejected(self):
        # Widening does not weaken the authz posture: a tenant naming
        # somebody else's namespace is refused before either lookup,
        # exactly as it is on the narrow decorator.
        response, captured, widened, scoped = self._run_visible(
            caller_ns='ns1', body_namespace='ns2',
            returned_obj_namespace='ns2')
        self.assertEqual(
            404, response.status_code,
            f'foreign-namespace tenant should be 404, got {response!r}')
        widened.assert_not_called()
        scoped.assert_not_called()
        self.assertNotIn('artifact_from_db', captured)
