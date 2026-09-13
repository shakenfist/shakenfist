# Copyright 2019 Michael Still and contributors

"""Ask every handler what an omission does.

Step 1 of ``docs/plans/PLAN-api-input-validation-phase-06-required.md``.
Finding F1 of that plan says every parameter declared ``required=True``
outside a route segment is optional in fact -- the handler gives it a
default, so Python has never raised for a single omission. Enforcing
required-ness is therefore a contract change for all of them, and
decision D32 says the declarations get corrected before enforcement is
turned on rather than after.

That correction needs to know, per declaration, what the server does
today when the parameter is not supplied. This file asks, by sending
the request: decision D36, and what
[#4167](https://github.com/shakenfist/shakenfist/issues/4167) asks for
by name. Phase 5's finding F5 is why it is not done by reading the AST
-- the same family of analysis over the same declarations got the
answer wrong last time by asking a well formed question about the
wrong property.

Three things make an answer here trustworthy, and all three are
deliberate:

* **The enumeration is derived.** ``required_declarations()`` walks
  ``declarations.handlers()`` and reports every body or query parameter
  declared required. A hand written list would silently omit whatever
  it forgot, and "the census found 76 and the table has 76 rows" is a
  definition-of-done item precisely because that is the error mode.
* **Every omission is paired with a control.** ``RECIPES`` carries the
  complete, valid request for each handler and the status it answers.
  A request which 404s because a fixture was not built looks exactly
  like a handler refusing an omission, and the control is what tells
  them apart: it is asserted on every run, so a rotted fixture fails
  this file instead of quietly producing a wrong verdict.
* **The verdicts are pinned.** ``SWEEP`` is the table published in the
  plan. A change in what a handler does with an omission fails here,
  which is what keeps the plan's evidence and the code agreeing while
  the remaining steps act on it.

A verdict is one of three words, from the step 1 brief:

``guarded``
    the handler answers 4xx -- it already refuses the omission, so
    declaring it required changes nothing a client can see.
``faults``
    a 5xx, or an exception was recorded. The omission reaches code
    which cannot cope with it; this is the population #4167 is about.
``accepted``
    2xx or 3xx. The handler does something sensible without it, so the
    declaration is the thing that is wrong.

Nothing here changes a declaration or a handler. Step 1 measures.
"""

import ast
import json
import shutil
import tempfile
import time
from unittest import mock
from uuid import uuid4

from shakenfist import baseobject
from shakenfist.artifact import Artifact
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.external_api import instance as instance_api
from shakenfist.instance import Instance
from shakenfist.mapping_rule import MappingRule
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.tests.external_api.test_request_validation import (
    AuthenticatedStackTestCase)
from shakenfist.trusted_issuer import TrustedIssuer


def _handler_defaults(fn):
    """Which of a handler's parameters have a default, and which it takes.

    Lifted from the census script in the plan's appendix, which is the
    enumeration finding F1 counted. Keeping the same shape means this
    file and that script cannot disagree about which declarations are
    in scope.
    """
    args = fn.args
    positional = args.posonlyargs + args.args
    out = set()
    if args.defaults:
        for a in positional[-len(args.defaults):]:
            out.add(a.arg)
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        if d is not None:
            out.add(a.arg)
    return out, {a.arg for a in positional} | {a.arg for a in args.kwonlyargs}


def required_declarations():
    """Every body or query parameter the API declares required.

    Returns ``(class, method, name, location, argtype, has_default,
    is_handler_kwarg)`` tuples, sorted. Path parameters are excluded
    because required-ness there is a tautology: werkzeug does not match
    the route without the segment.
    """
    rows = []
    for _, _, cls, fn in declarations.handlers():
        have_default, accepted = _handler_defaults(fn)
        for dec in fn.decorator_list:
            if 'swagger_helper' not in ast.unparse(dec):
                continue
            call = (dec.args[0]
                    if isinstance(dec, ast.Call) and dec.args else None)
            if not (isinstance(call, ast.Call) and len(call.args) >= 3
                    and isinstance(call.args[2], ast.List)):
                continue
            for item in call.args[2].elts:
                if not (isinstance(item, ast.Tuple)
                        and len(item.elts) in (5, 6)):
                    continue
                name = declarations.literal(item.elts[0])
                location = declarations.literal(item.elts[1])
                argtype = declarations.literal(item.elts[2])
                required = declarations.literal(item.elts[4])
                if location not in ('body', 'query') or required is not True:
                    continue
                rows.append((cls.name, fn.name, name, location, argtype,
                             name in have_default, name in accepted))
    return sorted(rows)


# The one declaration in the census which is not a handler keyword
# argument at all. ``UploadDataEndpoint.post`` declares the raw request
# body with ``api_base.RAW_BODY_PARAMETER``, which is the marker
# compile_parameters() keys ``raw_body`` on -- and a raw body is never
# added to ``required_names``, so this declaration cannot produce a
# missing-required finding and enforcement will never reach it. It is
# swept anyway, because "the count of rows equals the count the census
# script reports" is how the plan holds the table complete.
#
# Finding F1 names ``namespace`` on ``AuthFederatedEndpoint.post`` as
# this case. That is wrong: that handler's signature is
# ``post(self, token=None, namespace=None, rule=None)``, so the
# parameter has a default like the other 74. The counts F1 reports are
# right; the example is not.
RAW_BODY_DECLARATION = ('UploadDataEndpoint', 'post', 'body')


# A complete, valid request for every handler which declares a required
# body or query parameter, and the status that request answers.
#
# ``url`` is formatted against the fixture attributes built in setUp.
# ``body`` carries every required parameter and whatever else the
# request needs to be valid; ``query`` carries query-located ones.
# ``control`` is asserted on every run: see the module docstring for
# why a sweep without one is not evidence.
#
# Several controls are deliberately not 2xx. Each says why, because
# "the request reached its handler" is the property that matters and a
# 4xx can be the handler's own honest answer to a valid request.
RECIPES = {
    ('ArtifactMetadataEndpoint', 'put'): {
        'url': '/artifacts/{artifact}/metadata/sweepkey',
        'body': {'value': 'sweepvalue'},
        'control': 200,
    },
    ('ArtifactMetadatasEndpoint', 'post'): {
        'url': '/artifacts/{artifact}/metadata',
        'body': {'key': 'sweepkey', 'value': 'sweepvalue'},
        'control': 200,
    },
    ('ArtifactsEndpoint', 'delete'): {
        # Aimed at the empty scratch namespace rather than at system,
        # so the control deletes nothing and the artifact fixture the
        # rows above depend on survives.
        'url': '/artifacts',
        'body': {'confirm': True, 'namespace': 'scratch'},
        'control': 200,
    },
    ('ArtifactsEndpoint', 'post'): {
        'url': '/artifacts',
        'body': {'url': 'http://example.com/sweep.qcow2', 'shared': False},
        'control': 200,
    },
    ('AuthEndpoint', 'post'): {
        # The real key, so the control is the 200 a working client
        # gets. Public, so no token is involved.
        'url': '/auth',
        'body': {'namespace': 'scratch', 'key': 'scratchkey'},
        'control': 200,
        'unauthenticated': True,
    },
    ('AuthFederatedEndpoint', 'post'): {
        # 401: the exchange is refused because the token is not a real
        # one from a real issuer. Minting a valid federated identity
        # would be a whole fixture of its own and would not change what
        # the three argument guards above it do, which is what this
        # sweep is asking about.
        'url': '/auth/federated',
        'body': {'token': 'not-a-real-token', 'namespace': 'scratch',
                 'rule': 'sweeprule'},
        'control': 401,
        'unauthenticated': True,
    },
    ('AuthIssuerEndpoint', 'put'): {
        'url': '/auth/issuers/sweepissuer',
        'body': {'issuer_url': 'https://issuer.example.com',
                 'jwks_uri': 'https://issuer.example.com/jwks',
                 'audience': 'sweep'},
        'control': 200,
    },
    ('AuthIssuersEndpoint', 'post'): {
        # A distinct name and URL per request: an issuer URL is
        # unique by constraint, so a second identical create answers
        # 409 rather than reaching the guards this row is about.
        'url': '/auth/issuers',
        'body': {'name': 'sweepissuer{unique}',
                 'issuer_url': 'https://issuer{unique}.example.com',
                 'jwks_uri': 'https://issuer{unique}.example.com/jwks',
                 'audience': 'sweep'},
        'control': 200,
    },
    ('AuthMetadataEndpoint', 'put'): {
        'url': '/auth/namespaces/scratch/metadata/sweepkey',
        'body': {'value': 'sweepvalue'},
        'control': 200,
    },
    ('AuthMetadatasEndpoint', 'post'): {
        'url': '/auth/namespaces/scratch/metadata',
        'body': {'key': 'sweepkey', 'value': 'sweepvalue'},
        'control': 200,
    },
    ('AuthNamespaceClaimsEndpoint', 'post'): {
        # A namespace holds at most one active claim, so the claim the
        # previous request left behind has to go before the next one
        # can be a valid request rather than a 409.
        'reset': 'clear_namespace_claims',
        'url': '/auth/namespaces/scratch/claims',
        'body': {'limit_cpus': 1, 'limit_memory_mb': 1024,
                 'limit_disk_gb': 10, 'expires_in_seconds': 3600},
        'control': 200,
    },
    ('AuthNamespaceKeyEndpoint', 'put'): {
        'url': '/auth/namespaces/scratch/keys/key1',
        'body': {'key': 'a-replacement-secret'},
        'control': 200,
    },
    ('AuthNamespaceKeysEndpoint', 'post'): {
        'url': '/auth/namespaces/scratch/keys',
        'body': {'key_name': 'sweepkeyname', 'key': 'a-new-secret'},
        'control': 200,
    },
    ('AuthNamespaceRuleEndpoint', 'put'): {
        'url': '/auth/namespaces/scratch/rules/sweeprule',
        'body': {'issuer': 'sweepissuer', 'bound_claims': {'sub': 'someone'},
                 'scopes': ['blob.read'], 'key_ttl': 3600,
                 'key_name_prefix': 'sweep'},
        'control': 200,
    },
    ('AuthNamespaceRulesEndpoint', 'post'): {
        'url': '/auth/namespaces/scratch/rules',
        'body': {'name': 'sweeprule{unique}', 'issuer': 'sweepissuer',
                 'bound_claims': {'sub': 'someone'},
                 'scopes': ['blob.read'], 'key_ttl': 3600,
                 'key_name_prefix': 'sweep'},
        'control': 200,
    },
    ('AuthNamespaceTrustsEndpoint', 'post'): {
        'url': '/auth/namespaces/scratch/trust',
        'body': {'external_namespace': 'system'},
        'control': 200,
    },
    ('AuthNamespacesEndpoint', 'post'): {
        'url': '/auth/namespaces',
        'body': {'namespace': 'sweepnamespace{unique}'},
        'control': 200,
    },
    ('BlobMetadataEndpoint', 'put'): {
        'url': '/blobs/{blob}/metadata/sweepkey',
        'body': {'value': 'sweepvalue'},
        'control': 200,
    },
    ('BlobMetadatasEndpoint', 'post'): {
        'url': '/blobs/{blob}/metadata',
        'body': {'key': 'sweepkey', 'value': 'sweepvalue'},
        'control': 200,
    },
    ('ClusterOperationsEndpoint', 'get'): {
        'url': '/clusteroperations',
        'query': {'target_object_type': 'instance',
                  'target_uuid': '{instance}'},
        'control': 200,
    },
    ('InstanceAgentExecuteEndpoint', 'post'): {
        'url': '/instances/{instance}/agent/execute',
        'body': {'command_line': 'id'},
        'control': 200,
    },
    ('InstanceAgentGetEndpoint', 'post'): {
        'url': '/instances/{instance}/agent/get',
        'body': {'path': '/etc/hostname'},
        'control': 200,
    },
    ('InstanceAgentPutEndpoint', 'post'): {
        'url': '/instances/{instance}/agent/put',
        'body': {'blob_uuid': '{blob}', 'path': '/etc/hostname',
                 'mode': '0644'},
        'control': 200,
    },
    ('InstanceInterfacesEndpoint', 'post'): {
        'url': '/instances/{instance}/interfaces',
        'body': {'network': {'network_uuid': '{network}'}},
        'control': 200,
    },
    ('InstanceMetadataEndpoint', 'put'): {
        'url': '/instances/{instance}/metadata/sweepkey',
        'body': {'value': 'sweepvalue'},
        'control': 200,
    },
    ('InstanceMetadatasEndpoint', 'post'): {
        'url': '/instances/{instance}/metadata',
        'body': {'key': 'sweepkey', 'value': 'sweepvalue'},
        'control': 200,
    },
    ('InstancesEndpoint', 'delete'): {
        # Scoped to the empty scratch namespace, so the instance
        # fixture every other instance row depends on is not deleted
        # out from under them.
        'url': '/instances',
        'body': {'confirm': True, 'namespace': 'scratch'},
        'control': 200,
    },
    ('InstancesEndpoint', 'post'): {
        # 507: this fixture's single node is not a hypervisor, so a
        # well formed create reaches placement and is refused there.
        # That is the same control test_instance_create_validation.py
        # uses, and for the same reason -- a 507 is proof the request
        # got past every guard in the handler, which is what a control
        # here has to establish.
        'url': '/instances',
        'body': {'name': 'sweepinstance', 'cpus': 1, 'memory': 1024,
                 'disk': [{'size': 8}]},
        'control': 507,
    },
    ('InterfaceMetadataEndpoint', 'put'): {
        'url': '/interfaces/{interface}/metadata/sweepkey',
        'body': {'value': 'sweepvalue'},
        'control': 200,
    },
    ('InterfaceMetadatasEndpoint', 'post'): {
        'url': '/interfaces/{interface}/metadata',
        'body': {'key': 'sweepkey', 'value': 'sweepvalue'},
        'control': 200,
    },
    ('LabelEndpoint', 'post'): {
        'url': '/label/sweeplabel',
        'body': {'blob_uuid': '{blob}'},
        'control': 200,
    },
    ('NetworkDNSAddressEndpoint', 'delete'): {
        'url': '/networks/{network}/dns',
        'body': {'name': 'sweephost'},
        'control': 200,
    },
    ('NetworkDNSAddressEndpoint', 'post'): {
        'url': '/networks/{network}/dns',
        'body': {'name': 'sweephost', 'value': '10.9.8.5'},
        'control': 200,
    },
    ('NetworkMetadataEndpoint', 'put'): {
        'url': '/networks/{network}/metadata/sweepkey',
        'body': {'value': 'sweepvalue'},
        'control': 200,
    },
    ('NetworkMetadatasEndpoint', 'post'): {
        'url': '/networks/{network}/metadata',
        'body': {'key': 'sweepkey', 'value': 'sweepvalue'},
        'control': 200,
    },
    ('NetworksEndpoint', 'delete'): {
        # Scoped to scratch for the same reason the other delete-alls
        # are. 202 because phase 7 of PLAN-network-facade moved this
        # endpoint to the acknowledge-and-poll contract.
        'url': '/networks',
        'body': {'confirm': True, 'namespace': 'scratch'},
        'control': 202,
    },
    ('NetworksEndpoint', 'post'): {
        'url': '/networks',
        'body': {'netblock': '10.11.12.0/24', 'name': 'sweepnetwork{unique}'},
        'control': 200,
    },
    ('NodeMetadataEndpoint', 'put'): {
        'url': '/nodes/{node}/metadata/sweepkey',
        'body': {'value': 'sweepvalue'},
        'control': 200,
    },
    ('NodeMetadatasEndpoint', 'post'): {
        'url': '/nodes/{node}/metadata',
        'body': {'key': 'sweepkey', 'value': 'sweepvalue'},
        'control': 200,
    },
    ('UploadDataEndpoint', 'post'): {
        'url': '/upload/{upload}',
        'raw_body': b'some bytes',
        'control': 200,
    },
}


# The answer. One entry per declaration the census finds, mapping to
# ``(status, recorded_exception, verdict)``.
#
# This is the table published under *Sweep results* in
# docs/plans/PLAN-api-input-validation-phase-06-required.md, and steps
# 2 and 3 act on it. It is pinned here rather than merely printed so
# that a handler which starts answering an omission differently fails a
# test instead of silently invalidating the plan's evidence.
SWEEP = {
    ('ArtifactMetadataEndpoint', 'put', 'value'):
        (400, False, 'guarded'),
    ('ArtifactMetadatasEndpoint', 'post', 'key'):
        (400, False, 'guarded'),
    ('ArtifactMetadatasEndpoint', 'post', 'value'):
        (400, False, 'guarded'),
    ('ArtifactsEndpoint', 'delete', 'confirm'):
        (400, False, 'guarded'),
    ('ArtifactsEndpoint', 'post', 'shared'):
        (200, False, 'accepted'),
    ('ArtifactsEndpoint', 'post', 'url'):
        (500, True, 'faults'),
    ('AuthEndpoint', 'post', 'key'):
        (400, False, 'guarded'),
    ('AuthEndpoint', 'post', 'namespace'):
        (400, False, 'guarded'),
    ('AuthFederatedEndpoint', 'post', 'namespace'):
        (400, False, 'guarded'),
    ('AuthFederatedEndpoint', 'post', 'rule'):
        (400, False, 'guarded'),
    ('AuthFederatedEndpoint', 'post', 'token'):
        (400, False, 'guarded'),
    ('AuthIssuerEndpoint', 'put', 'audience'):
        (400, False, 'guarded'),
    ('AuthIssuerEndpoint', 'put', 'issuer_url'):
        (400, False, 'guarded'),
    ('AuthIssuerEndpoint', 'put', 'jwks_uri'):
        (400, False, 'guarded'),
    ('AuthIssuersEndpoint', 'post', 'audience'):
        (400, False, 'guarded'),
    ('AuthIssuersEndpoint', 'post', 'issuer_url'):
        (400, False, 'guarded'),
    ('AuthIssuersEndpoint', 'post', 'jwks_uri'):
        (400, False, 'guarded'),
    ('AuthIssuersEndpoint', 'post', 'name'):
        (400, False, 'guarded'),
    ('AuthMetadataEndpoint', 'put', 'value'):
        (400, False, 'guarded'),
    ('AuthMetadatasEndpoint', 'post', 'key'):
        (400, False, 'guarded'),
    ('AuthMetadatasEndpoint', 'post', 'value'):
        (400, False, 'guarded'),
    ('AuthNamespaceClaimsEndpoint', 'post', 'expires_in_seconds'):
        (400, False, 'guarded'),
    ('AuthNamespaceClaimsEndpoint', 'post', 'limit_cpus'):
        (400, False, 'guarded'),
    ('AuthNamespaceClaimsEndpoint', 'post', 'limit_disk_gb'):
        (400, False, 'guarded'),
    ('AuthNamespaceClaimsEndpoint', 'post', 'limit_memory_mb'):
        (400, False, 'guarded'),
    ('AuthNamespaceKeyEndpoint', 'put', 'key'):
        (400, False, 'guarded'),
    ('AuthNamespaceKeysEndpoint', 'post', 'key_name'):
        (400, False, 'guarded'),
    ('AuthNamespaceRuleEndpoint', 'put', 'bound_claims'):
        (400, False, 'guarded'),
    ('AuthNamespaceRuleEndpoint', 'put', 'issuer'):
        (400, False, 'guarded'),
    ('AuthNamespaceRuleEndpoint', 'put', 'key_name_prefix'):
        (400, False, 'guarded'),
    ('AuthNamespaceRuleEndpoint', 'put', 'key_ttl'):
        (400, False, 'guarded'),
    ('AuthNamespaceRuleEndpoint', 'put', 'scopes'):
        (400, False, 'guarded'),
    ('AuthNamespaceRulesEndpoint', 'post', 'bound_claims'):
        (400, False, 'guarded'),
    ('AuthNamespaceRulesEndpoint', 'post', 'issuer'):
        (400, False, 'guarded'),
    ('AuthNamespaceRulesEndpoint', 'post', 'key_name_prefix'):
        (400, False, 'guarded'),
    ('AuthNamespaceRulesEndpoint', 'post', 'key_ttl'):
        (400, False, 'guarded'),
    ('AuthNamespaceRulesEndpoint', 'post', 'name'):
        (400, False, 'guarded'),
    ('AuthNamespaceRulesEndpoint', 'post', 'scopes'):
        (400, False, 'guarded'),
    ('AuthNamespaceTrustsEndpoint', 'post', 'external_namespace'):
        (400, False, 'guarded'),
    ('AuthNamespacesEndpoint', 'post', 'namespace'):
        (400, False, 'guarded'),
    ('BlobMetadataEndpoint', 'put', 'value'):
        (400, False, 'guarded'),
    ('BlobMetadatasEndpoint', 'post', 'key'):
        (400, False, 'guarded'),
    ('BlobMetadatasEndpoint', 'post', 'value'):
        (400, False, 'guarded'),
    ('ClusterOperationsEndpoint', 'get', 'target_object_type'):
        (400, False, 'guarded'),
    ('ClusterOperationsEndpoint', 'get', 'target_uuid'):
        (400, False, 'guarded'),
    ('InstanceAgentExecuteEndpoint', 'post', 'command_line'):
        (200, False, 'accepted'),
    ('InstanceAgentGetEndpoint', 'post', 'path'):
        (200, False, 'accepted'),
    ('InstanceAgentPutEndpoint', 'post', 'blob_uuid'):
        (500, True, 'faults'),
    ('InstanceAgentPutEndpoint', 'post', 'mode'):
        (500, True, 'faults'),
    ('InstanceAgentPutEndpoint', 'post', 'path'):
        (200, False, 'accepted'),
    ('InstanceInterfacesEndpoint', 'post', 'network'):
        (400, False, 'guarded'),
    ('InstanceMetadataEndpoint', 'put', 'value'):
        (400, False, 'guarded'),
    ('InstanceMetadatasEndpoint', 'post', 'key'):
        (400, False, 'guarded'),
    ('InstanceMetadatasEndpoint', 'post', 'value'):
        (400, False, 'guarded'),
    ('InstancesEndpoint', 'delete', 'confirm'):
        (400, False, 'guarded'),
    ('InstancesEndpoint', 'post', 'cpus'):
        (500, True, 'faults'),
    ('InstancesEndpoint', 'post', 'disk'):
        (400, False, 'guarded'),
    ('InstancesEndpoint', 'post', 'memory'):
        (500, True, 'faults'),
    ('InstancesEndpoint', 'post', 'name'):
        (400, False, 'guarded'),
    ('InterfaceMetadataEndpoint', 'put', 'value'):
        (400, False, 'guarded'),
    ('InterfaceMetadatasEndpoint', 'post', 'key'):
        (400, False, 'guarded'),
    ('InterfaceMetadatasEndpoint', 'post', 'value'):
        (400, False, 'guarded'),
    ('LabelEndpoint', 'post', 'blob_uuid'):
        (500, True, 'faults'),
    ('NetworkDNSAddressEndpoint', 'delete', 'name'):
        (406, False, 'guarded'),
    ('NetworkDNSAddressEndpoint', 'post', 'name'):
        (406, False, 'guarded'),
    ('NetworkDNSAddressEndpoint', 'post', 'value'):
        (200, False, 'accepted'),
    ('NetworkMetadataEndpoint', 'put', 'value'):
        (400, False, 'guarded'),
    ('NetworkMetadatasEndpoint', 'post', 'key'):
        (400, False, 'guarded'),
    ('NetworkMetadatasEndpoint', 'post', 'value'):
        (400, False, 'guarded'),
    ('NetworksEndpoint', 'delete', 'confirm'):
        (400, False, 'guarded'),
    ('NetworksEndpoint', 'post', 'name'):
        (500, True, 'faults'),
    ('NetworksEndpoint', 'post', 'netblock'):
        (400, False, 'guarded'),
    ('NodeMetadataEndpoint', 'put', 'value'):
        (400, False, 'guarded'),
    ('NodeMetadatasEndpoint', 'post', 'key'):
        (400, False, 'guarded'),
    ('NodeMetadatasEndpoint', 'post', 'value'):
        (400, False, 'guarded'),
    ('UploadDataEndpoint', 'post', 'body'):
        (200, False, 'accepted'),
}


class RequiredSweepTestCase(AuthenticatedStackTestCase):
    """Every declared-required parameter, omitted once, through the stack.

    Inherits the whole decorator stack and a real authenticated client
    from AuthenticatedStackTestCase for the reason that class
    documents: phase 3's review found a handler tested in isolation and
    the deployed behaviour giving different answers.

    Runs at ``enforce``, the default since phase 4 and therefore the
    mode a deployment is in. Required-ness is not enforced in any mode
    yet (the filter at base.py:1914), so what answers here is the
    handler -- which is exactly the question.
    """

    mode = 'enforce'

    def setUp(self):
        super().setUp()

        # InstancesEndpoint.post caches its Scheduler in a module
        # global and only builds one if that global is falsy, so a
        # request which reaches placement leaves a real Scheduler
        # behind for every later test in the same worker process --
        # including the ones in test_external_api.py which patch
        # shakenfist.scheduler.Scheduler with a fake in setUp and then
        # never get asked for it. patch.object restores the attribute
        # it saved even though the handler reassigns it, which is the
        # containment wanted here. Copied from
        # test_instance_create_validation.py, where the same escape
        # produced a 507 in unrelated tests.
        scheduler_patch = mock.patch.object(instance_api, 'SCHEDULER', None)
        scheduler_patch.start()
        self.addCleanup(scheduler_patch.stop)

        # The upload route writes into STORAGE_PATH, which defaults to
        # /srv/shakenfist. A test must not depend on that existing, and
        # must certainly not write to it, so it is redirected at a
        # tempdir for the duration.
        storage = tempfile.mkdtemp(prefix='sf-required-sweep-')
        self.addCleanup(shutil.rmtree, storage, ignore_errors=True)
        saved_storage = config.STORAGE_PATH
        self.addCleanup(setattr, config, 'STORAGE_PATH', saved_storage)
        config.STORAGE_PATH = storage

        # Run as though we are the node the instance is placed on, so
        # redirect_instance_request() runs the handler here rather than
        # proxying it to another node.
        self.node_name = self.mock_mariadb.node_names[0]
        saved_node_uuid = config.NODE_UUID
        saved_node_name = config.NODE_NAME
        self.addCleanup(setattr, config, 'NODE_UUID', saved_node_uuid)
        self.addCleanup(setattr, config, 'NODE_NAME', saved_node_name)
        config.NODE_UUID = self.mock_mariadb.node_uuids[self.node_name]
        config.NODE_NAME = self.node_name

        # An empty namespace for the delete-all routes to be pointed
        # at, so their controls delete nothing.
        self.mock_mariadb.create_namespace('scratch', 'key1', 'scratchkey')

        self.artifact = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/sweep.tgz',
            name='sweepartifact', namespace='system')
        self.artifact.state = Artifact.STATE_CREATED

        self.instance = self.mock_mariadb.create_instance(
            'sweepfixture', namespace='system', set_state=dbo.STATE_CREATED,
            place_on_node=config.NODE_UUID)

        self.network = self.mock_mariadb.create_network(
            'sweepfixturenet', namespace='system', netblock='10.9.8.0/24',
            provide_dhcp=True, provide_dns=True)

        self.interface = self.mock_mariadb.create_network_interface(
            netdesc=self.mock_mariadb.generate_netdesc(str(self.network.uuid)),
            instance_uuid=str(self.instance.uuid))

        # The three agent endpoints refuse an instance whose agent is
        # not ready before they look at anything else, so without this
        # every agent row in the table would record that refusal rather
        # than what the omission does.
        agent_state = mock.patch.object(
            Instance, 'agent_state', new_callable=mock.PropertyMock,
            return_value=baseobject.State(
                value='ready', update_time=time.time()))
        agent_state.start()
        self.addCleanup(agent_state.stop)

        # A trusted issuer and a mapping rule, so the two PUT routes
        # which resolve one by name reach their handlers instead of
        # answering 404.
        self.issuer = TrustedIssuer.new(
            'sweepissuer', 'https://issuer.example.com',
            'https://issuer.example.com/jwks', 'sweep')
        self.rule = MappingRule.new(
            'scratch', 'sweeprule', issuer='sweepissuer',
            bound_claims={'sub': 'someone'}, scopes=['blob.read'],
            key_ttl=3600, key_name_prefix='sweep')

        # Blobs and uploads have no storage in MockMariaDB, so their
        # lookups are stubbed the way test_blob_data_bounds.py stubs
        # them. Keyed on the uuid rather than answering unconditionally,
        # because a stub which resolves anything resolves None too --
        # and "the handler accepted a missing blob_uuid" would then be
        # a property of this fixture rather than of the handler. That is
        # exactly the wrong-answer-dressed-as-a-real-one the controls
        # above exist to catch, arriving by a route a control cannot
        # see.
        self.blob_uuid = str(uuid4())
        blob = mock.MagicMock()
        blob.uuid = self.blob_uuid
        # Real values for the fields an external view serialises: a
        # MagicMock reaches flask_restful's json encoder and raises
        # outside the decorator stack, where the API's own error
        # handling cannot see it.
        blob.size = 1024
        blob.ref_count = 1
        blob.depends_on = None
        blob.locations = []
        blob_patch = mock.patch(
            'shakenfist.blob.Blob.from_db',
            side_effect=lambda u, *a, **kw: (
                blob if u and str(u) == self.blob_uuid else None))
        blob_patch.start()
        self.addCleanup(blob_patch.stop)

        self.upload_uuid = str(uuid4())
        upload = mock.MagicMock()
        upload.uuid = self.upload_uuid
        upload.node = config.NODE_NAME
        upload_patch = mock.patch(
            'shakenfist.upload.Upload.from_db',
            side_effect=lambda u, *a, **kw: (
                upload if u and str(u) == self.upload_uuid else None))
        upload_patch.start()
        self.addCleanup(upload_patch.stop)

        # Two reads MockMariaDB does not model, each of which reaches
        # for a real MariaDB and raises RuntimeError('MARIADB_HOST not
        # configured'). Stubbing them is what lets the cluster
        # operations listing and the label create answer 200; neither
        # is reachable before the guards this file measures.
        ops_patch = mock.patch(
            'shakenfist.mariadb.list_cluster_operations_for_target',
            return_value=[])
        ops_patch.start()
        self.addCleanup(ops_patch.stop)
        hash_patch = mock.patch(
            'shakenfist.mariadb.get_valid_hash', return_value=None)
        hash_patch.start()
        self.addCleanup(hash_patch.stop)

        # No worker runs in a unit test, so an operation a handler
        # waits on never reaches a terminal state: raise_for_error()
        # spends API_ASYNC_WAIT seconds polling and then answers 500.
        # Standing in for "a worker exists and the operation
        # succeeded" is what lets the two DNS routes be asked about
        # their parameters at all.
        completed = mock.MagicMock()
        completed.state.value = BaseClusterOperation.STATE_COMPLETE
        poll_patch = mock.patch(
            'shakenfist.operations.baseoperation.poll_until_terminal',
            return_value=completed)
        poll_patch.start()
        self.addCleanup(poll_patch.stop)

        self.fixtures = {
            'artifact': str(self.artifact.uuid),
            'instance': str(self.instance.uuid),
            'network': str(self.network.uuid),
            'interface': str(self.interface.uuid),
            'node': self.node_name,
            'blob': self.blob_uuid,
            'upload': self.upload_uuid,
        }

        # Several recipes create an object whose name or URL is unique
        # by constraint, and every recipe is sent more than once (a
        # control and then one omission per parameter). Without a
        # distinct value per request the second control answers 409 and
        # the sweep measures the collision instead of the handler.
        self.unique = 0

    def clear_namespace_claims(self):
        """Forget every capacity claim, so a create can be valid again."""
        self.mock_mariadb.namespace_claims.clear()

    def _resolve(self, value):
        """Substitute fixture uuids into a recipe value."""
        if isinstance(value, str):
            return value.format(unique=self.unique, **self.fixtures)
        if isinstance(value, dict):
            return {k: self._resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def _request(self, method, recipe, omit=None):
        """Send one request, and say what came back.

        Returns ``(status, recorded, body)``, where ``recorded`` says
        whether an exception record was written -- which is how this
        codebase records a server fault (``record_exception`` in
        ``shakenfist/external_api/base.py``, which calls
        ``shakenfist.util.exceptions.record_exception``; the base test
        case already replaces that with a mock, so this counts its
        calls rather than installing a second spy). The body rides
        along so a failure says what the server actually answered.
        """
        self.unique += 1
        if recipe.get('reset'):
            getattr(self, recipe['reset'])()
        url = self._resolve(recipe['url'])
        query = self._resolve(recipe.get('query', {}))
        body = self._resolve(recipe.get('body', {}))
        raw = recipe.get('raw_body')

        if omit is not None:
            query.pop(omit, None)
            body.pop(omit, None)
            if raw is not None and omit == api_base.RAW_BODY_PARAMETER:
                raw = b''

        if query:
            url = '%s?%s' % (url, '&'.join(
                '%s=%s' % (k, v) for k, v in sorted(query.items())))

        headers = {}
        if not recipe.get('unauthenticated'):
            headers['Authorization'] = self.token

        kwargs = {'headers': headers}
        if raw is not None:
            kwargs['data'] = raw
        elif recipe.get('body') is not None:
            kwargs['data'] = json.dumps(body)
            kwargs['content_type'] = 'application/json'

        before = self.mock_record_exception.call_count
        response = getattr(self.client, method)(url, **kwargs)
        recorded = self.mock_record_exception.call_count > before
        return (response.status_code, recorded,
                response.get_data(as_text=True)[:200])

    @staticmethod
    def _verdict(status, recorded):
        if recorded or status >= 500:
            return 'faults'
        if status >= 400:
            return 'guarded'
        return 'accepted'

    def test_every_required_declaration_has_a_recipe(self):
        """No declaration is swept by accident or skipped by omission.

        The enumeration is derived from the source, so a declaration
        added without a recipe fails here rather than quietly falling
        out of the table the rest of this phase is built on.
        """
        declared = {(cls, method)
                    for cls, method, _, _, _, _, _ in required_declarations()}
        self.assertEqual(
            set(), declared - set(RECIPES),
            'a handler declares a required parameter and has no recipe')
        self.assertEqual(
            set(), set(RECIPES) - declared,
            'a recipe names a handler which declares nothing required')

    def test_the_census_still_finds_seventy_six(self):
        """Finding F1's premise, re-asked here.

        D32 and D35 both rest on the shape of this census rather than
        on its exact total, but a number that has moved means the table
        below is describing a different API than the one in the tree.
        """
        rows = required_declarations()
        self.assertEqual(76, len(rows))
        self.assertEqual(75, len([r for r in rows if r[5]]))
        self.assertEqual(0, len([r for r in rows if not r[5] and r[6]]))
        self.assertEqual(1, len([r for r in rows if not r[6]]))
        self.assertEqual(
            [RAW_BODY_DECLARATION],
            [(r[0], r[1], r[2]) for r in rows if not r[6]])

    def test_the_sweep(self):
        """The measurement, and the whole of this step's evidence.

        One test rather than 76, because the fixture is expensive and
        because the failure worth reading is the whole diff against the
        published table rather than the first row that moved.
        """
        mismatches = []
        actual = {}
        for cls, method, name, _, _, _, _ in required_declarations():
            recipe = RECIPES[(cls, method)]

            control, control_recorded, body = self._request(method, recipe)
            if (control, control_recorded) != (recipe['control'], False):
                mismatches.append(
                    '%s.%s: the control request (nothing omitted) answered '
                    '%s recorded=%s %s, not %s -- the fixture is wrong, so no '
                    'verdict from it can be trusted'
                    % (cls, method, control, control_recorded, body,
                       recipe['control']))
                continue

            status, recorded, body = self._request(method, recipe, omit=name)
            verdict = self._verdict(status, recorded)
            actual[(cls, method, name)] = (status, recorded, verdict)

            expected = SWEEP.get((cls, method, name))
            if expected != (status, recorded, verdict):
                mismatches.append(
                    '%s.%s %s: %r, published as %r -- %s'
                    % (cls, method, name, (status, recorded, verdict),
                       expected, body))

        self.assertEqual(
            [], mismatches,
            'the sweep no longer agrees with the table published in '
            'docs/plans/PLAN-api-input-validation-phase-06-required.md:\n'
            + '\n'.join(mismatches))
        self.assertEqual(
            set(SWEEP), set(actual),
            'the published table and the sweep cover different parameters')
