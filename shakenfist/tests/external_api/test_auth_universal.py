"""Authentication is the default, not something each endpoint remembers.

Before this, every resource method carried its own
`@api_base.verify_token`. 120 of the 124 did; the four which did not
were the correct four. That is a good record, but the failure mode was
wrong: forgetting the decorator on a new endpoint left it silently
open, and nothing would have caught it.

Authentication now lives on `Resource.method_decorators`, so it runs
for every method whether or not anyone remembered, and the only way
out is an explicit `@api_base.public`. These tests make that a
property of the tree rather than a habit.
"""

import flask_restful

from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.tests import base


# The complete set of deliberately unauthenticated endpoints. Adding to
# this list is a security decision and should be argued for in review,
# which is the whole point of keeping it written down here rather than
# deriving it.
EXPECTED_PUBLIC = {
    ('Root', 'get'),        # API landing page and capability listing
    ('Livez', 'get'),       # liveness probe, no dependencies consulted
    ('Readyz', 'get'),      # readiness probe, ditto; also serves
                            # /healthz on a second path
    ('AuthEndpoint', 'post'),  # trades a key for a token; cannot need one
    # Trades an identity token from a trusted issuer for a namespace
    # key. Unauthenticated by nature -- the caller has no Shaken Fist
    # credential yet, which is the entire point of federating. What
    # stands in place of authentication is a signature check against a
    # configured issuer's published keys plus a mapping rule the
    # namespace owner wrote; see
    # docs/plans/PLAN-auth-federation-phase-03-exchange.md.
    ('AuthFederatedEndpoint', 'post'),
}

HTTP_METHODS = ('get', 'post', 'put', 'delete', 'patch')


def _resource_methods():
    """Every (resource class, http method) pair Flask has registered."""
    seen = []
    for rule in external_api.app.url_map.iter_rules():
        view = external_api.app.view_functions.get(rule.endpoint)
        resource = getattr(view, 'view_class', None)
        if resource is None or not issubclass(
                resource, flask_restful.Resource):
            # flasgger registers its own views for the swagger UI at
            # /apidocs. Those are not flask_restful resources, so they
            # never pass through Resource.method_decorators and this
            # file's guarantee does not reach them. That is deliberate,
            # not an oversight: the API documentation is published
            # unauthenticated on purpose (operator, 2026-07-29).
            continue
        for verb in HTTP_METHODS:
            handler = getattr(resource, verb, None)
            if handler is None:
                continue
            if (resource.__name__, verb) in [(c, v) for c, v, _ in seen]:
                continue
            seen.append((resource.__name__, verb, handler))
    return seen


class UniversalAuthenticationTestCase(base.ShakenFistTestCase):
    def test_every_endpoint_authenticates_or_is_explicitly_public(self):
        methods = _resource_methods()

        # Guard against the enumeration silently finding nothing and
        # the test passing vacuously.
        self.assertGreater(len(methods), 100)

        public = {(cls, verb) for cls, verb, handler in methods
                  if getattr(handler, '_sf_public', False)}

        self.assertEqual(
            EXPECTED_PUBLIC, public,
            'The set of unauthenticated endpoints changed. Every entry '
            'here is reachable with no credential at all, so adding one '
            'is a security decision: justify it in review and update '
            'EXPECTED_PUBLIC. Removing one means an endpoint gained '
            'authentication, which is fine -- just update the list.')

    def test_resources_inherit_the_authenticating_base(self):
        # A resource which subclasses flask_restful.Resource directly
        # rather than api_base.Resource would miss method_decorators
        # entirely and be silently open.
        for rule in external_api.app.url_map.iter_rules():
            view = external_api.app.view_functions.get(rule.endpoint)
            resource = getattr(view, 'view_class', None)
            if resource is None or not issubclass(
                    resource, flask_restful.Resource):
                continue
            self.assertTrue(
                issubclass(resource, api_base.Resource),
                f'{resource.__name__} does not subclass api_base.Resource, '
                f'so it does not authenticate')

    def test_authentication_is_in_the_decorator_chain(self):
        self.assertIn(
            api_base._authenticate_unless_public,
            api_base.Resource.method_decorators)


class DecoratorOrderingTestCase(base.ShakenFistTestCase):
    """Authentication must precede the per-method ownership checks.

    Ownership decorators such as requires_namespace_ownership read the
    authenticated identity, so if authentication ran after them they
    would be reading an identity that had not been established. This
    ordering is the load-bearing assumption behind moving
    authentication to the class level, so it is asserted rather than
    assumed.
    """

    def test_class_decorators_wrap_outside_per_method_decorators(self):
        calls = []

        def per_method(func):
            def wrapper(*args, **kwargs):
                calls.append('per_method')
                return func(*args, **kwargs)
            return wrapper

        def class_level(func):
            def wrapper(*args, **kwargs):
                calls.append('class_level')
                return func(*args, **kwargs)
            return wrapper

        class Fake(api_base.Resource):
            method_decorators = [class_level]

            @per_method
            def get(self):
                calls.append('body')
                return 'ok'

        with external_api.app.test_request_context('/', method='GET'):
            Fake().dispatch_request()

        self.assertEqual(['class_level', 'per_method', 'body'], calls)

    def test_authentication_runs_after_request_logging(self):
        # log_request should record the attempt even when the caller
        # turns out to be unauthenticated, so it must sit outside
        # authentication. In method_decorators the last entry is
        # outermost and therefore runs first, so authentication being
        # at index 0 puts it innermost of the class-level set.
        decorators = api_base.Resource.method_decorators
        self.assertEqual(
            api_base._authenticate_unless_public, decorators[0],
            'authentication must be first in the list, which makes it '
            'the last class-level decorator to run and therefore the '
            'closest to the endpoint body')
        self.assertLess(
            decorators.index(api_base._authenticate_unless_public),
            decorators.index(api_base.handle_authorization_exceptions),
            'authentication must sit inside handle_authorization_'
            'exceptions so the errors it raises become responses')
