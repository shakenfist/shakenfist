# Copyright 2026 Michael Still and contributors

"""Every 503 a wait can clear says how long to wait.

``handle_database_unavailable`` answers the database outage for every
endpoint in the API, and it answered a bare ``503`` with no
``Retry-After`` while the two create-path 503s -- which answer the same
condition, from inside handlers the generic decorator also wraps -- set
one by hand. OpenAPI keys responses by status code, so ``POST
/instances`` published a header it served on one of its two 503 paths
and not the other, and every other endpoint in the API told its clients
to retry without saying when (issue 5030).

The fix is one header-setting site, ``api_base.retryable_error()``.
``test_response_headers.py`` keeps it one, since a second call site
assigning the header itself is how the two contracts diverged in the
first place; the guard at the bottom of this file keeps a new 503 from
skipping the helper altogether.
"""

import ast
import inspect
import json
import os

from shakenfist import exceptions
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.tests import base


class RetryableErrorTestCase(base.ShakenFistTestCase):
    def test_the_helper_sets_the_header_and_keeps_the_body(self):
        with external_api.app.test_request_context('/artifacts'):
            resp = api_base.retryable_error(503, 'come back later')

        self.assertEqual(503, resp.status_code)
        self.assertEqual(
            str(api_base.TRANSIENT_RETRY_AFTER_SECONDS),
            resp.headers.get('Retry-After'))
        # Whole-body equality: the helper must not restructure
        # sf_api.error()'s shape the way capacity_error() deliberately
        # does.
        self.assertEqual(
            {'error': 'come back later', 'status': 503},
            json.loads(resp.get_data(as_text=True)))

    def test_the_figure_is_the_one_constant(self):
        # Not a second literal 15. The capacity 507 and these 503s are
        # the same promise about the same cluster, so they are the same
        # number by construction.
        with external_api.app.test_request_context('/artifacts'):
            retryable = api_base.retryable_error(503, 'come back later')
            capacity = api_base.capacity_error(
                'no nodes remaining', 'sufficient_idle_cpu')

        self.assertEqual(
            capacity.headers.get('Retry-After'),
            retryable.headers.get('Retry-After'))


class DatabaseUnavailableHeaderTestCase(base.ShakenFistTestCase):
    def _call(self, exc):
        def handler():
            raise exc

        with external_api.app.test_request_context('/artifacts'):
            return api_base.handle_database_unavailable(handler)()

    def test_an_outage_names_a_wait(self):
        resp = self._call(exceptions.DatabaseUnavailable('gone'))

        self.assertEqual(503, resp.status_code)
        self.assertIn('retry', json.loads(resp.get_data(as_text=True))['error'])
        self.assertEqual(
            str(api_base.TRANSIENT_RETRY_AFTER_SECONDS),
            resp.headers.get('Retry-After'))

    def test_a_refused_filter_names_no_wait(self):
        # The other arm of the same decorator is a caller bug which no
        # retry fixes, so it must not inherit the header: a client
        # sleeping and replaying a 400 would replay it until its
        # deadline.
        resp = self._call(exceptions.UnservableFilterCriteria(
            'artifact cannot be filtered on network_uuid'))

        self.assertEqual(400, resp.status_code)
        self.assertNotIn('Retry-After', resp.headers)


class NodelockUnavailableHeaderTestCase(base.ShakenFistTestCase):
    def test_a_restarting_nodelock_names_a_wait(self):
        # Retryable on better evidence than the database outage: the
        # commonest cause is this node's own sf-nodelock restarting
        # during a deploy, which finishes in seconds.
        @api_base.handle_nodelock_unavailable
        def handler():
            raise exceptions.NodeLockUnavailable('did not answer')

        with external_api.app.test_request_context(
                '/instances/abc/poweroff', method='POST'):
            resp = handler()

        self.assertEqual(503, resp.status_code)
        self.assertEqual(
            str(api_base.TRANSIENT_RETRY_AFTER_SECONDS),
            resp.headers.get('Retry-After'))


class BareServiceUnavailableTestCase(base.ShakenFistTestCase):
    """Every 503 in the API layer either names a wait or says why not.

    The other half of issue 5030. That ``Retry-After`` is set in one
    place, and that every site setting it is published, is held by
    ``test_response_headers.py``; neither notices a 503 built without
    the helper at all, which is what ``handle_database_unavailable``
    was.
    """

    def _api_sources(self):
        directory = os.path.dirname(inspect.getfile(api_base))
        for entry in sorted(os.listdir(directory)):
            if entry.endswith('.py'):
                path = os.path.join(directory, entry)
                with open(path) as f:
                    yield entry, f.read()

    def _nodes_by_function(self, source):
        """Every node, paired with the name of the function it sits in.

        The *nearest* enclosing function, which matters here: most of
        this layer's 503s are raised inside a decorator's ``wrapper``,
        and attributing a node to every function it is nested inside
        would let an allowlist entry for an outer function excuse a new
        site added to an inner one.
        """
        def walk(node, enclosing):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef,
                                      ast.AsyncFunctionDef)):
                    yield from walk(child, child.name)
                    continue
                if enclosing is not None:
                    yield child, enclosing
                yield from walk(child, enclosing)

        yield from walk(ast.parse(source), None)

    # The 503s which deliberately promise nothing, and why. A 503 not
    # listed here must come from retryable_error(), because a client
    # told to retry with no interval invents one, which is what issue
    # 5030 was.
    #
    # A status read out of a mapping rather than written as a literal
    # is invisible to this guard -- the claim endpoints' 503s come from
    # CLAIM_REFUSAL_STATUS and are not checked here.
    PERMANENT = {
        # FEDERATION_JWKS_CA_BUNDLE points at a file sf-api cannot
        # load, on the discovery path and on both federation paths. An
        # operator has to fix the cluster, so a Retry-After would have
        # the caller replay the request until its deadline. The login
        # path's second 503 is a failed read of which mapping rules are
        # active, which is refused rather than guessed and is the one
        # entry here a wait might clear.
        ('auth.py', '_run_discovery'),
        ('auth.py', 'post'),
        # NODE_UUID is unresolved, so this node cannot work out whether
        # to proxy. A configuration condition on this node.
        ('base.py', 'wrapper'),
        # A peer node did not answer, here and on the blob read path.
        # Retryable, but what has to recover is the peer rather than
        # this cluster's deferral interval, so these promise nothing
        # (issue 3743, issue 4161).
        ('base.py', 'proxy_request_to_node'),
        ('blob.py', 'get'),
    }

    def test_no_api_handler_builds_a_bare_503(self):
        offenders = []
        for name, source in self._api_sources():
            for node, function in self._nodes_by_function(source):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, 'attr', None) != 'error':
                    continue
                if not node.args:
                    continue
                first = node.args[0]
                if not isinstance(first, ast.Constant) or first.value != 503:
                    continue
                if (name, function) in self.PERMANENT:
                    continue
                offenders.append(
                    '%s:%d in %s() answers a bare 503; either call '
                    'api_base.retryable_error() or record in PERMANENT why '
                    'no wait clears it' % (name, node.lineno, function))

        self.assertEqual([], offenders, '\n'.join(offenders))
