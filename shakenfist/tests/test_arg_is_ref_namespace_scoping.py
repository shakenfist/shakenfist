# Copyright 2019 Michael Still and contributors
#
# Parametrised tests for the arg_is_*_ref decorator family.
#
# The decorators (arg_is_instance_ref, arg_is_network_ref,
# arg_is_artifact_ref) historically passed request_namespace() straight to
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
    # Artifact-specific: the `artifact_uuid` branch goes through
    # Artifact.from_db rather than from_db_by_ref, but must still honour
    # the namespace authz check so a tenant cannot bypass scoping by
    # providing artifact_uuid in the request body.
    # ------------------------------------------------------------------

    def test_artifact_uuid_branch_tenant_foreign_namespace_rejected(self):
        captured = {}

        @api_artifact.arg_is_artifact_ref
        def endpoint(**kwargs):
            captured.update(kwargs)
            return 'ok'

        with mock.patch(_REQUEST_NS_TARGET, return_value='ns1'), \
                mock.patch('shakenfist.external_api.artifact.Artifact'
                           '.from_db') as from_db, \
                mock.patch('shakenfist.external_api.artifact.Artifact'
                           '.from_db_by_ref') as from_db_by_ref:
            response = endpoint(artifact_uuid='some-uuid', namespace='ns2')

        self.assertEqual(
            404, response.status_code,
            f'foreign-namespace tenant should be 404, got {response!r}')
        from_db.assert_not_called()
        from_db_by_ref.assert_not_called()
        self.assertNotIn('artifact_from_db', captured)
