# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: both
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage: yes
import base64
import re
import time
from functools import partial

import bcrypt
import flask
from flasgger import swag_from
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as sf_random  # noreorder

from shakenfist import artifact
from shakenfist import baseobject
from shakenfist import exceptions
from shakenfist import federation
from shakenfist import instance
from shakenfist import locks
from shakenfist import oidc_discovery
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.external_api import scopes as api_scopes
from shakenfist.external_api import validation
from shakenfist.mapping_rule import MappingRule
from shakenfist.mapping_rule import MappingRules
from shakenfist.mapping_rule import MAX_KEY_TTL_SECONDS
from shakenfist.mapping_rule import RuleValidationError
from shakenfist.namespace import Namespace
from shakenfist.namespace import namespace_is_trusted
from shakenfist.namespace import revoke_inbound_trust
from shakenfist.namespace import Namespaces
from shakenfist.namespace_claim import ClaimRefused
from shakenfist.namespace_claim import NamespaceClaim
from shakenfist.namespace_claim import NamespaceClaims
from shakenfist.namespace_key import NamespaceKey
from shakenfist.trusted_issuer import TrustedIssuer
from shakenfist.trusted_issuer import TrustedIssuers
from shakenfist.util import access_tokens
from shakenfist.util import credentials
from shakenfist.util import general as util_general
from shakenfist.util.access_tokens import parse_jwt_identity
from shakenfist.util.access_tokens import request_namespace


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


# What a namespace name may look like, published in the OpenAPI
# specification and enforced by a handler guard on create (issue
# #4250). The name is rendered verbatim into dnsmasq's conf-file on
# network nodes (domain=<namespace>.<zone> and local=/<namespace>.<zone>/
# in deploy/collection/roles/network/files/dhcp.tmpl), so the character
# set has to keep it inert there: a newline injects arbitrary dnsmasq
# directives, and comma, slash, hash and whitespace all carry meaning
# in that file. 63 is the DNS label length limit, the same ceiling
# instance names carry for the same reason. Underscore is permitted
# because existing deployments (and this repository's own CI) already
# use it; dot is not, because it would silently add DNS domain levels,
# and widening later is compatible where narrowing is not. This is
# deliberately checked only on create: every other namespace route
# resolves the name against the database and answers 404, which is a
# stronger check than a format one (see validation.py's module
# docstring), but on create there is nothing to resolve.
NAMESPACE_NAME_PATTERN = '^[a-zA-Z0-9_-]{1,63}$'


def _live_namespace(name):
    """Return the named namespace if it exists and is not deleted.

    Factored out of arg_is_namespace so a handler which names a second
    namespace in its body -- the trust grant -- tests it with the same
    predicate as the one in the path, rather than restating it.
    """
    ns = Namespace.from_db(name, suppress_failure_audit=True)
    if not ns:
        return None
    if ns.state.value == dbo.STATE_DELETED:
        return None
    return ns


def arg_is_namespace(func):
    def wrapper(*args, **kwargs):
        if 'namespace' not in kwargs:
            return sf_api.error(400, 'missing namespace in request')

        ns = _live_namespace(kwargs.get('namespace'))
        if not ns:
            LOG.with_fields({'namespace': kwargs.get('namespace')}).info(
                'Namespace not found, missing or deleted')
            return sf_api.error(404, 'namespace not found')

        kwargs['namespace_from_db'] = ns
        return func(*args, **kwargs)
    return wrapper


def requires_namespace_ownership(func):
    def wrapper(*args, **kwargs):
        ns = kwargs.get('namespace')
        if not namespace_is_trusted(ns, request_namespace()):
            LOG.info('Namespace not found, ownership test in decorator')
            return sf_api.error(404, 'namespace not found')

        return func(*args, **kwargs)
    return wrapper


auth_token_example = """{
    "namespace": "system",
    "key": "oisoSe7T",
    "apiurl": "https://shakenfist/api"
}
"""


def _validate_key_name(key_name):
    """Reject key names reserved for internally minted service keys.

    Namespace creation used to reject only the exact name
    'service_key' while key creation rejected only the '_service_key'
    prefix, which meant each path let through what the other blocked.
    Both patterns are now rejected on both paths, which changes
    behaviour only for names nobody can have legitimately wanted.
    Returns an error response, or None if the name is acceptable.

    The patterns themselves live in util.credentials, because a mapping
    rule's key_name_prefix has to be held to the same standard and
    mapping_rule.py cannot import a module that returns Flask
    responses.
    """
    if credentials.is_reserved_key_name(key_name):
        return sf_api.error(403, 'illegal key name')
    return None


def _validate_key_expiry(expiry):
    """Validate the optional expiry body parameter.

    Absent means the key never expires, which is what every client
    sent before this parameter existed. Anything else must be a number
    of epoch seconds in the future: an expiry in the past would create
    a key which was unusable the instant it existed, which is far more
    likely to be a units mistake than an intent.

    Returns (expiry, error_response), exactly one of which is None.
    """
    if expiry is None:
        return None, None

    # bool is a subclass of int, and "expiry": true is not a time.
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
        return None, sf_api.error(400, 'expiry is not a number')
    if expiry <= time.time():
        return None, sf_api.error(400, 'expiry must be in the future')
    return float(expiry), None


class AuthEndpoint(api_base.Resource):
    # Unauthenticated by definition: this is where a caller trades a
    # namespace key for a token, so it cannot require a token.
    @api_base.public
    @swag_from(api_base.swagger_helper(
        'auth', 'Authenticate and create access token.',
        [
            ('namespace', 'body', 'string',
             'The namespace to authenticate against.', True),
            ('key', 'body', 'string',
             'The secret for the key you wish to use.', True)
        ],
        [(200, 'An access token.', auth_token_example),
         (400, 'Missing namepsace or key in request or key is not a string.', None),
         (404, 'Namespace not found.', None)]))
    @arg_is_namespace
    def post(self, namespace=None, key=None, namespace_from_db=None):
        if not key:
            return sf_api.error(400, 'missing key in request')
        if not isinstance(key, str):
            # Must be a string to encode()
            return sf_api.error(400, 'key is not a string')

        # A secret carrying the reserved prefix but failing its
        # checksum cannot match any stored key, because operator
        # supplied secrets may not carry that prefix. Reject it before
        # spending a bcrypt comparison per key on it. Note this is a
        # cost optimisation and a corruption check, not a security
        # boundary: a well formed but wrong secret still goes through
        # the full comparison below.
        #
        # This deliberately writes no event of its own. /auth is public,
        # so anyone who knows a namespace name can reach this line, and
        # an event here would let them drive writes into that
        # namespace's audit log at network speed for less work than the
        # bcrypt path costs. The failure is still recorded, by the
        # ordinary "incorrect namespace key" event at the bottom of this
        # handler, so the event count per attempt is unchanged from
        # before the checksum check existed. A checksum failure tells
        # the namespace owner nothing they could act on that the
        # ordinary event does not.
        malformed = (credentials.has_prefix(key)
                     and not credentials.looks_valid(key))
        if malformed:
            LOG.with_fields({'namespace': namespace}).info(
                'Malformed cluster generated key presented')

        # The accessor is an indexed listing of the namespace's keys
        # with the expiry filter pushed into SQL, so expired keys are
        # never bcrypt compared here -- they simply are not returned.
        keys = {} if malformed else namespace_from_db.keys.get(
            'nonced_keys', {})
        for keyname in keys:
            # The accessor hands out the hash as a SecretStr; bcrypt
            # needs the real bytes, so this is one of the two places
            # which unwraps. The nonce below stays wrapped -- it is
            # passed straight to create_token(), which unwraps it into
            # the JWT claim itself.
            possible_key = base64.b64decode(
                keys[keyname]['key'].get_secret_value())
            try:
                if bcrypt.checkpw(key.encode('utf-8'), possible_key):
                    # One extra point read, and only on the successful
                    # match rather than per candidate key. The legacy
                    # accessor shape above is pinned by the namespace key
                    # behaviour preservation tests, so scopes are
                    # fetched here rather than widening it.
                    matched = namespace_from_db.lookup_key(keyname)
                    return access_tokens.create_token(
                        namespace_from_db, keyname, keys[keyname]['nonce'],
                        scopes=matched.scopes if matched else None)
            except ValueError as e:
                # The key body held the stored hash and the nonce, so it
                # cannot go in the event. The error and the key name are
                # enough to find the malformed key and look at it
                # directly.
                namespace_from_db.add_event(
                    EVENT_TYPE_AUDIT, 'namespace key is invalid',
                    extra={
                        'error': str(e),
                        'key_name': keyname
                    })

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'attempt to use incorrect namespace key')
        return sf_api.error(401, 'unauthorized')


namespace_get_example = """{
    "name": "system",
    "keys": [
        "deploy"
    ],
    "metadata": {}
}"""


namespace_list_example = """[
    ...,
    {
        "name": "system",
        "keys": [
            "deploy"
        ],
        "metadata": {}
    }
]"""


class AuthNamespacesEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Create a namespace.',
        [
            ('namespace', 'body', 'string', 'The namespace to create.', True,
             {'pattern': NAMESPACE_NAME_PATTERN}),
            ('key_name', 'body', 'string',
             'Name of an optional first key created at the same time.', False),
            ('key', 'body', 'string',
             'Secret for an optional first key created at the same time.', False)
        ],
        [(200, 'The namespace as created.', namespace_get_example),
         (400, 'No or invalid namespace specified, no key specified, or key is not a string.', None),
         (403, 'Illegal key name.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')

        # The declaration publishes NAMESPACE_NAME_PATTERN, but a
        # schema check is rolled back by API_VALIDATION_MODE=warn/off,
        # and what this refuses is dnsmasq configuration injection on
        # the network node (issue #4250) -- so the guard lives here and
        # holds in every mode. The isinstance arm is unreachable while
        # the mode is 'enforce', where a non-string is refused as a
        # type mismatch before the handler is entered.
        if not isinstance(namespace, str):
            return sf_api.error(400, 'namespace name must be a string')
        if not re.fullmatch(NAMESPACE_NAME_PATTERN, namespace):
            return sf_api.error(
                400, ('namespace name %s is invalid. Names are at most 63 '
                      'characters from the set: a-z, A-Z, 0-9, hyphen (-), '
                      'or underscore (_).' % namespace))

        if Namespace.from_db(namespace, suppress_failure_audit=True):
            return sf_api.error(403, 'namespace exists')

        if key_name:
            if not key:
                return sf_api.error(400, 'no key specified')
            err = _validate_key_name(key_name)
            if err:
                return err
            key, err = _validate_key_secret(key)
            if err:
                return err

        ns = Namespace.new(namespace)
        ns.add_event(EVENT_TYPE_AUDIT, 'creation request from REST API')

        # Log this special case of a token being used. As everywhere
        # else, the token itself is deliberately absent from the event:
        # the key name says which credential was used, without leaving
        # a replayable credential in the event log.
        invoking_namespace, keyname = parse_jwt_identity()
        parent_ns = Namespace.from_db(invoking_namespace)
        if parent_ns:
            parent_ns.add_event(
                EVENT_TYPE_AUDIT, 'token used to create namespace %s' % namespace,
                extra={
                    'keyname': keyname,
                    'method': flask.request.environ['REQUEST_METHOD'],
                    'path': flask.request.environ['PATH_INFO'],
                    'remote-address': flask.request.remote_addr,
                    'created-namespace': namespace
                })
        ns.add_event(
            EVENT_TYPE_AUDIT, 'token used to create namespace',
            extra={
                'keyname': keyname,
                'method': flask.request.environ['REQUEST_METHOD'],
                'path': flask.request.environ['PATH_INFO'],
                'remote-address': flask.request.remote_addr
            })

        # Allow shortcut of creating key at same time as the namespace
        if key_name:
            ns.add_key(key_name, key)

        return ns.external_view()

    @swag_from(api_base.swagger_helper(
        'auth', 'List all namespaces visible to this namespace.', [],
        [(200, 'The namespace as created.', namespace_list_example)]))
    @api_base.log_token_use
    def get(self):
        retval = []
        for ns in Namespaces(filters=[], prefilter='active'):
            if namespace_is_trusted(ns.uuid, request_namespace()):
                retval.append(ns.external_view())
        return retval


class AuthNamespaceEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Delete a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to delete.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'You cannot delete a namespace with instances or networks.', None),
         (403, 'You cannot delete the system namespace.', None),
         (404, 'Namespace not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @arg_is_namespace
    @api_base.log_token_use
    def delete(self, namespace=None, namespace_from_db=None):
        if namespace == 'system':
            return sf_api.error(403, 'you cannot delete the system namespace')

        # The namespace must be empty. Only a deleted instance counts as
        # gone: it has released everything it held, and the deleted
        # object sweep hard deletes its row on its own schedule whether
        # its namespace still exists or not. Every other state, error
        # included, is a live row, and an instance is reachable only
        # through its namespace -- so a namespace deleted over the top
        # of one leaves a row with no owner and nothing which will ever
        # sweep it (issue 4847).
        #
        # Counting error as gone also made this refusal expire into
        # permission. An instance whose delete fails goes delete-wait ->
        # delete-wait-error -> error, and only the middle state blocked,
        # so a caller who retried the namespace delete a moment later
        # succeeded over the top of exactly the instance the first
        # refusal had protected.
        instances = []
        for i in instance.instances_in_namespace(namespace):
            if i.state.value == dbo.STATE_DELETED:
                continue
            LOG.with_fields({'instance': i.uuid,
                             'state': i.state.value}).info('Blocks namespace delete')
            instances.append(i.uuid)
        if len(instances) > 0:
            return sf_api.error(400, 'you cannot delete a namespace with instances')

        # Networks still count error and delete_wait as gone, through
        # is_dead(), which is the defect fixed above for instances. That
        # is known and tracked as issue 4863 rather than fixed here:
        # is_dead() is shared, and its other callers rely on it meaning
        # "do not act on this network", so the fix is a predicate of
        # this handler's own rather than narrowing that one.
        networks = []
        for n in network.networks_in_namespace(namespace):
            if not n.is_dead():
                LOG.with_fields({'network': n.uuid,
                                 'state': n.state.value}).info('Blocks namespace delete')
                networks.append(n.uuid)
        if len(networks) > 0:
            return sf_api.error(400, 'you cannot delete a namespace with networks')

        for a in artifact.artifacts_in_namespace(namespace):
            a.add_event(
                EVENT_TYPE_AUDIT, 'deletion request via namespace deletion from REST API')
            a.delete()

        # Revoke every trust naming this namespace before it goes. A
        # trust names a namespace by name and namespace names can be
        # reused once the cluster maintainer hard deletes the static
        # row, so a trust left behind is inherited by whoever creates
        # the name next. Namespace.hard_delete() sweeps again as a
        # backstop, but waiting for it would leave the trust standing
        # for the length of the hard delete delay, and there is no
        # reason for a deleted namespace to be trusted for a moment
        # longer than it exists. See namespace.revoke_inbound_trust().
        revoked = revoke_inbound_trust(namespace, 'namespace deleted')
        if revoked:
            namespace_from_db.add_event(
                EVENT_TYPE_AUDIT, 'revoked trusts naming this namespace',
                extra={'revoked-from': revoked})

        namespace_from_db.state = dbo.STATE_DELETED
        namespace_from_db.add_event(EVENT_TYPE_AUDIT, 'deletion request from REST API')

    @swag_from(api_base.swagger_helper(
        'auth', 'Get namespace information.',
        [
            ('namespace', 'path', 'string', 'The namespace to get.', True)
        ],
        [(200, 'Information about a single namespace.', namespace_get_example),
         (404, 'Namespace not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def get(self, namespace=None, namespace_from_db=None):
        return namespace_from_db.external_view()


def _validate_key_secret(key):
    """Check an operator-supplied key secret.

    Returns (key, error_response), exactly one of which is None.

    The sfk_ prefix is reserved for secrets the cluster generates. That
    reservation is load bearing rather than cosmetic: /auth rejects a
    presented secret which carries the prefix but fails its checksum,
    without bcrypt comparing it against anything, and that is only
    sound if no legitimate operator secret can be shaped that way.
    """
    if not isinstance(key, str):
        # Must be a string to encode()
        return None, sf_api.error(400, 'key is not a string')
    if len(key) > 72:
        return None, sf_api.error(
            422, 'keys cannot be longer than 72 characters')
    if credentials.has_prefix(key):
        return None, sf_api.error(
            400, 'the %s prefix is reserved for cluster generated keys; '
                 'omit the key entirely to have one generated for you'
                 % credentials.PREFIX)
    return key, None


def _namespace_keys_putpost(ns=None, key_name=None, key=None, expiry=None,
                            allow_generation=False):
    """Create or rotate a namespace key. ``ns`` is a Namespace object.

    Returns the key name, or a dict carrying the generated secret when
    the caller asked the cluster to pick one.

    ``allow_generation`` is set only by the create path. Rotation must
    not treat a missing secret as "pick one for me": a PUT which
    forgot its body would silently replace a live credential with one
    the caller then has to notice in the response, which is a
    destructive outcome for a typo.
    """
    if not key_name:
        return sf_api.error(400, 'no key name specified')
    err = _validate_key_name(key_name)
    if err:
        return err

    # A caller who supplies no secret is asking the cluster to generate
    # one. The generated form is the only way to get a secret carrying
    # the sfk_ prefix, and it is returned exactly once -- only the
    # bcrypt hash is stored, so it cannot be recovered afterwards.
    generated = False
    if not key:
        if not allow_generation:
            return sf_api.error(400, 'no key specified')
        key = credentials.generate()
        generated = True
    else:
        key, err = _validate_key_secret(key)
        if err:
            return err

    expiry, err = _validate_key_expiry(expiry)
    if err:
        return err

    # A key may not be created with more privilege than the caller
    # creating it. Key creation is gated by namespace ownership rather
    # than by caller_is_admin, and a namespace always owns itself, so
    # without this a token scoped auth.write (or auth.*, which the
    # family wildcard actively encourages) could mint an unscoped key
    # in its own namespace and re-authenticate carrying the wildcard.
    # In the system namespace that reaches cluster-admin, which would
    # route straight around the rule that admin endpoints require the
    # cluster-admin scope.
    #
    # None from caller_scopes() means the caller is unrestricted, which
    # is every operator holding a legacy key, so their keys keep being
    # created unscoped exactly as before.
    inherited = api_base.caller_scopes()

    ns.add_key(key_name, key, expiry=expiry, scopes=inherited)
    if generated:
        return {'key_name': key_name, 'key': key}
    return key_name


namespace_events_example = """[
    ...,
    {
        "duration": null,
        "extra": {
            "claim": "0b2b4f76-0a1b-4d0f-8b3f-6f1a2c4d5e6f",
            "clamped": false,
            "returned_cpus": 40,
            "returned_disk_gb": 2000,
            "returned_memory_mb": 81920
        },
        "fqdn": "sf-1",
        "message": "namespace claim deleted, capacity returned",
        "timestamp": 1755300000.0,
        "type": "audit"
    },
    ...
]"""


class AuthNamespaceEventsEndpoint(api_base.Resource):
    # Cluster administrators only, and deliberately not the namespace's
    # own owner. A namespace's event trail names the instances, nodes
    # and other namespaces its capacity accounting involved, so serving
    # it to the namespace itself would widen what a tenant can learn
    # about the cluster. Whether an owner may read their own
    # namespace's events is a real request to argue on its own merits,
    # and is recorded as future work by G2 of
    # docs/plans/PLAN-scheduler-reservations-phase-07-diagnostics.md.
    @swag_from(api_base.swagger_helper(
        'auth', 'Get namespace event information.',
        [
            ('namespace', 'path', 'string', 'The namespace.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100 and is '
             'capped at 1000.', False, {'minimum': 1, 'maximum': 1000})
        ],
        [(200, 'Event information about a single namespace.',
          namespace_events_example),
         (401, 'The caller is not a cluster administrator.', None),
         (404, 'Namespace not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def get(self, namespace=None, event_type=None, limit=100,
            namespace_from_db=None):
        # A namespace is keyed by its name rather than a uuid, and
        # Namespace.uuid returns that name, so this is the identifier
        # every writer of a namespace event already passes.
        return api_base.object_events_response(
            'namespace', namespace_from_db.uuid, limit, event_type)


class AuthNamespaceKeysEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Get the authentication keys for a namespace.',
        [
            ('namespace', 'path', 'string',
             'The namespace to fetch authentication keys for.', True)
        ],
        [(200, 'A list of keynames for the namespace.', '["deploy", ...]'),
         (404, 'Namespace not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def get(self, namespace=None, namespace_from_db=None):
        out = []
        for keyname in namespace_from_db.keys.get('nonced_keys', {}):
            out.append(keyname)
        return out

    @swag_from(api_base.swagger_helper(
        'auth', 'Add an authentication key for the namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to add a key to.', True),
            ('key_name', 'body', 'string', 'The name of the key.', True),
            ('key', 'body', 'string',
             'Optional. The authentication key. If omitted the cluster '
             'generates one and returns it in the response, which is the '
             'only time it can be read.', False),
            ('expiry', 'body', 'number',
             'Optional. The time, in seconds since the unix epoch, at which '
             'this key stops working. Must be in the future. If omitted the '
             'key never expires.', False)
        ],
        [(200, 'The name of the created key, or an object carrying the '
               'generated secret when no key was supplied.',
          '{"key_name": "newkey", "key": "sfk_..."}'),
         (400, 'Expiry is not a number, or is not in the future.', None),
         (403, 'Illegal key name.', None),
         (404, 'Namespace not found.', None),
         (422, 'Keys cannot be longer than 72 characters.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def post(self, namespace=None, key_name=None, key=None, expiry=None,
             namespace_from_db=None):
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'create auth key request from REST API',
            extra={'key': key_name})
        return _namespace_keys_putpost(
            namespace_from_db, key_name, key, expiry=expiry,
            allow_generation=True)


class AuthNamespaceKeyEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Update an authentication key for a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to add a key to.', True),
            ('key_name', 'path', 'string', 'The name of the key.', True),
            ('key', 'body', 'string', 'The authentication key.', True),
            ('expiry', 'body', 'number',
             'Optional. The time, in seconds since the unix epoch, at which '
             'this key stops working. Must be in the future. If omitted the '
             'key never expires, and any expiry it previously had is '
             'cleared.', False)
        ],
        [(200, 'The name of the updated key.', 'newkey'),
         (400, 'Expiry is not a number, or is not in the future.', None),
         (403, 'Illegal key name.', None),
         (404, 'Namespace or key not found.', None),
         (422, 'Keys cannot be longer than 72 characters.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def put(self, namespace=None, key_name=None, key=None, expiry=None,
            namespace_from_db=None):
        # NOTE(mikal): this endpoint used to test membership against the
        # namespace's keys dict itself rather than its 'nonced_keys'
        # entry, and then passed the namespace name where a Namespace
        # object was expected. It also lacked the @arg_is_namespace
        # decorator which populates that object at all, so it has never
        # worked. It was fixed rather than preserved because there is no
        # client which could depend on the old behaviour.
        if key_name not in namespace_from_db.keys.get('nonced_keys', {}):
            return sf_api.error(404, 'key does not exist')
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'update auth key request from REST API',
            extra={'key': key_name})
        return _namespace_keys_putpost(
            namespace_from_db, key_name, key, expiry=expiry)

    @swag_from(api_base.swagger_helper(
        'auth', 'Delete an authentication key for a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to remove a key from.', True),
            ('key_name', 'path', 'string', 'The name of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'No key name specified.', None),
         (404, 'Namespace or key not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def delete(self, namespace=None, key_name=None, namespace_from_db=None):
        if not key_name:
            return sf_api.error(400, 'no key name specified')

        if key_name in namespace_from_db.keys.get('nonced_keys', {}):
            namespace_from_db.add_event(
                EVENT_TYPE_AUDIT, 'remove auth key request from REST API',
                extra={'key': key_name})
            namespace_from_db.remove_key(key_name)
        else:
            return sf_api.error(404, 'key name not found in namespace')


class AuthMetadatasEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Fetch metadata for a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to fetch metadata for.', True)
        ],
        [(200, 'Namespace metadata, if any.', None),
         (404, 'Namespace not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def get(self, namespace=None, namespace_from_db=None):
        return namespace_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'auth', 'Add metadata for a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'any',
             'The value of the key. Stored verbatim as any JSON value and never '
             'interpreted by the API.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Namespace not found.', None)]))
    @requires_namespace_ownership
    @api_base.requires_namespace_exist_if_specified
    @arg_is_namespace
    @api_base.log_token_use
    def post(self, namespace=None, key=None, value=None, namespace_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'post'})
        namespace_from_db.add_metadata_key(key, value)


class AuthMetadataEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Update a metadata key for a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to add a key to.', True),
            ('key', 'path', 'string', 'The metadata key to set', True),
            ('value', 'body', 'any',
             'The value of the key. Stored verbatim as any JSON value and never '
             'interpreted by the API.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Namespace not found.', None)]))
    @requires_namespace_ownership
    @api_base.requires_namespace_exist_if_specified
    @arg_is_namespace
    @api_base.log_token_use
    def put(self, namespace=None, key=None, value=None, namespace_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'put'})
        namespace_from_db.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'auth', 'Delete a metadata key for a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to remove a key from.', True),
            ('key', 'path', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Namespace not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def delete(self, namespace=None, key=None, namespace_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete metadata key request from REST API',
            extra={'key': key})
        namespace_from_db.remove_metadata_key(key)


class AuthNamespaceTrustsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Trust an external namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace to trust.', True),
            ('external_namespace', 'body', 'namespace',
             'The namespace being granted trust.', True)
        ],
        [(200, 'The current state of the namespace.', namespace_get_example),
         (400, 'No external namespace specified.', None),
         (404, 'Namespace or external namespace not found.', None)]))
    @arg_is_namespace
    @api_base.log_token_use
    def post(self, namespace=None, external_namespace=None, namespace_from_db=None):
        if not external_namespace:
            return sf_api.error(400, 'no external namespace specified')

        if not namespace_is_trusted(namespace, request_namespace()):
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found, trust test failed')
            return sf_api.error(404, 'namespace not found')

        # A trust names a namespace by name, and a name with no
        # namespace behind it is free for anyone to create -- so
        # granting one is granting a trust to whoever takes the name
        # next. That is the hazard revoke_inbound_trust() exists to
        # prevent on the delete side; refuse to create it here.
        if not _live_namespace(external_namespace):
            LOG.with_fields({
                'namespace': namespace,
                'external_namespace': external_namespace
            }).info('Refusing trust naming a namespace which does not exist')
            return sf_api.error(404, 'external namespace not found')

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'add trust request from REST API')
        namespace_from_db.add_trust(external_namespace)
        return namespace_from_db.external_view()


class AuthNamespaceTrustEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Remove trust from an external namespace.',
        [
            ('namespace', 'path', 'string',
             'The namespace to alter.', True),
            ('external_namespace', 'path', 'namespace',
             'The namespace to no longer trust.', True)
        ],
        [(200, 'The current state of the namespace.', namespace_get_example),
         (400, 'No external namespace specified.', None),
         (404, 'Namespace not found.', None)]))
    @arg_is_namespace
    @api_base.log_token_use
    def delete(self, namespace=None, external_namespace=None, namespace_from_db=None):
        if not external_namespace:
            return sf_api.error(400, 'no external namespace specified')

        if not namespace_is_trusted(namespace, request_namespace()):
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found, trust test failed')
            return sf_api.error(404, 'namespace not found')

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'remove trust request from REST API')
        namespace_from_db.remove_trust(external_namespace)
        return namespace_from_db.external_view()


trusted_issuer_example = """{
    "audience": "https://shakenfist.example.com",
    "authorization_endpoint": null,
    "client_id": null,
    "device_authorization_endpoint": null,
    "discovered_at": null,
    "issuer_url": "https://token.actions.githubusercontent.com",
    "jwks_uri": "https://token.actions.githubusercontent.com/.well-known/jwks",
    "name": "github",
    "oidc_scopes": null,
    "state": "created",
    "token_endpoint": null,
    "uuid": "b2b3a04e-8f22-4a1e-8f8e-2f3b1f7a41ab",
    "version": 1
}"""


def _validate_issuer_arguments(issuer_url, jwks_uri, audience,
                               client_id=None, oidc_scopes=None,
                               discover=True):
    """Check the configuration of a trusted issuer.

    Returns an error response, or None.

    issuer_url and audience are required and there is no sensible
    default for either: an issuer missing its audience would accept
    tokens minted for someone else.

    jwks_uri is optional, because an issuer which publishes a discovery
    document says where its keys are in it, and making an operator find
    and paste the value anyway is the thing discovery exists to stop.
    What is not optional is that the issuer ends up with one from
    somewhere, so omitting it means the discovery fetch has to succeed
    -- and asking for no discovery while supplying no JWKS URI asks for
    an issuer which can never verify a token, which is refused here
    rather than created and left broken.

    client_id and oidc_scopes are optional and are only read by a human
    login flow. An issuer configured to verify a workload's tokens --
    GitHub Actions, say -- has no client registration at the provider
    and never will, so requiring one would break every issuer in
    existence to serve a flow that issuer will never run. Their limit is
    255 rather than the 1024 the three fields above carry, because that
    is what their columns hold.
    """
    for name, value in (('issuer_url', issuer_url),
                        ('audience', audience)):
        if not value:
            return sf_api.error(400, f'no {name} specified')
        if not isinstance(value, str):
            return sf_api.error(400, f'{name} is not a string')
        if len(value) > 1024:
            return sf_api.error(
                422, f'{name} cannot be longer than 1024 characters')

    for name, value in (('client_id', client_id),
                        ('oidc_scopes', oidc_scopes)):
        if value is None:
            continue
        if not isinstance(value, str):
            return sf_api.error(400, f'{name} is not a string')
        if len(value) > 255:
            return sf_api.error(
                422, f'{name} cannot be longer than 255 characters')

    if jwks_uri is None:
        if not discover:
            return sf_api.error(
                400, 'jwks_uri is required when discovery is not requested, '
                     'because an issuer with no JWKS URI cannot verify a '
                     'signature at all')
        return None

    if not isinstance(jwks_uri, str):
        return sf_api.error(400, 'jwks_uri is not a string')
    # An empty string is a malformed value rather than an omission. A
    # caller who means "discover it" leaves the key out or sends null,
    # both of which arrive here as None above.
    if not jwks_uri:
        return sf_api.error(400, 'no jwks_uri specified')
    if len(jwks_uri) > 1024:
        return sf_api.error(
            422, 'jwks_uri cannot be longer than 1024 characters')

    # Signing keys must come from somewhere we control the choice of.
    # Refusing plaintext here is not paranoia: a JWKS fetched over HTTP
    # can be substituted by anyone on the path, which turns signature
    # verification into theatre.
    if not jwks_uri.startswith('https://'):
        return sf_api.error(400, 'jwks_uri must be https')
    return None


def _issuer_url_lock():
    """Serialise the check-then-write on issuer_url.

    issuer_url lives in trusted_issuer_attributes and has no unique
    index to enforce it -- and could not easily have one, because a
    soft-deleted issuer keeps its row and its URL is deliberately
    available for reuse. So uniqueness here is a read followed by a
    write, and without a lock two administrators configuring the same
    provider at the same moment can both read "free" and both write.

    That is the same shape as the vsock CID allocator in instance.py,
    and it takes the same remedy: one cluster wide lock held across
    both halves. Cheap, because these are admin-only endpoints that
    run about as often as a cluster gains an identity provider.
    """
    return locks.ClusterLock(
        'trusted_issuer_urls', None, 'global',
        op='Claim trusted issuer URL', timeout=30)


def _issuer_url_taken(issuer_url, by_someone_other_than=None):
    """Refuse a second issuer record for one iss value.

    Token validation resolves an issuer by its URL, so two live records
    claiming the same URL make which provider's keys we trust depend on
    listing order. An operator repointing an issuer would believe they
    had, while some requests kept verifying against the old JWKS.

    Callers must hold _issuer_url_lock() across this check and the
    write it guards, or the check is advisory only.
    """
    existing = federation.issuer_claiming_url(issuer_url)
    if not existing or existing.name == by_someone_other_than:
        return None
    return sf_api.error(
        409, f'issuer {existing.name} is already configured for '
             f'{issuer_url}')


def _run_discovery(issuer_url, jwks_uri, mandatory):
    """Fetch an issuer's discovery document and say what it changes.

    Returns a four-tuple of the endpoint columns a successful run
    writes, the JWKS URI to store, the audit events the caller owes the
    issuer, and an error response. Only one of the last two is ever
    interesting: an error means nothing was decided, and otherwise the
    caller writes what it was handed and records the events once it has
    an object to record them against.

    `mandatory` is the whole design of this function, and the asymmetry
    is deliberate.

    An operator who supplied no JWKS URI is relying on the document for
    it, so a failed fetch leaves nothing to verify a token with and the
    request fails with the provider's own symptom in the response. An
    operator who supplied one is relying on discovery for the login
    endpoints only, and those matter to a flow that issuer may never
    run -- so a failure is logged and audited and the request succeeds.
    Every issuer configured before discovery existed supplies a JWKS
    URI, so the second half is what keeps a cluster which has never
    heard of human login working against a provider which publishes no
    document at all, or none we can reach.

    `mandatory` is therefore "a failure fails the request" rather than
    "the JWKS URI is missing", which is why it is an argument and not
    derived from jwks_uri here. The refresh endpoint passes it true
    whatever the issuer holds, because a fetch is the only thing that
    request asked for.

    Where both the operator and the document name a JWKS URI and they
    differ, the operator's value is written and the difference is
    audited rather than refused. The operator guide describes a
    deployment where the right value is deliberately not the published
    one -- a provider whose published JWKS path answers with a redirect
    to a CDN, which the fetch refuses to follow -- so refusing a
    disagreement would break a configuration the documentation tells
    operators to build.
    """
    audits = []
    try:
        document = oidc_discovery.fetch(issuer_url)
    except exceptions.JWKSTrustAnchorUnusable as e:
        # Ours, not theirs: FEDERATION_JWKS_CA_BUNDLE points at a file
        # this process cannot load, so no provider was contacted and
        # naming one in the refusal would send an administrator to
        # debug a system which is not at fault.
        LOG.with_fields({'issuer_url': issuer_url}).error(
            f'OIDC discovery cannot build a TLS trust store: {e}')
        if mandatory:
            # suppress_traceback because the line above is the
            # deliberate log of this failure: an unloadable bundle is
            # an ordinary refusal here, and a traceback at INFO would
            # make it look like a bug to anything mining for them.
            return None, None, audits, sf_api.error(
                503, f'OIDC discovery is unavailable on this cluster: {e}',
                suppress_traceback=True)
        audits.append((
            'OIDC discovery skipped, this cluster cannot build a TLS '
            'trust store',
            {'issuer_url': issuer_url, 'error': str(e)}))
        return None, jwks_uri, audits, None
    except exceptions.OIDCDiscoveryFailed as e:
        LOG.with_fields({'issuer_url': issuer_url}).warning(
            f'OIDC discovery failed: {e}')
        if mandatory:
            # 502 rather than 400: the request was well formed and it
            # is the provider, or the path to it, which did not answer
            # as it should have. The traceback is suppressed for the
            # same reason as above -- a provider which does not publish
            # a document is a thing an operator has done, not a bug --
            # and the warning above is this failure's deliberate log.
            return None, None, audits, sf_api.error(
                502, f'OIDC discovery failed: {e}', suppress_traceback=True)
        audits.append((
            'OIDC discovery failed, keeping the configured jwks_uri',
            {'issuer_url': issuer_url, 'error': str(e)}))
        return None, jwks_uri, audits, None

    # The four columns a run owns, written as a set. An endpoint the
    # document does not name is stored as null, because a provider
    # which has withdrawn a flow should stop advertising it here.
    discovered = {
        'authorization_endpoint': document.get('authorization_endpoint'),
        'token_endpoint': document.get('token_endpoint'),
        'device_authorization_endpoint': document.get(
            'device_authorization_endpoint'),
        'discovered_at': time.time(),
    }

    # The document's issuer member is ignored on purpose. There is no
    # column for it, and the fetch refuses any document whose issuer is
    # not byte for byte the issuer_url passed in, so it is the
    # configured value by construction.
    if jwks_uri is None:
        return discovered, document['jwks_uri'], audits, None

    if document['jwks_uri'] != jwks_uri:
        audits.append((
            'kept the configured jwks_uri, which differs from the one this '
            'issuer publishes',
            {'issuer_url': issuer_url, 'configured_jwks_uri': jwks_uri,
             'published_jwks_uri': document['jwks_uri']}))
    return discovered, jwks_uri, audits, None


def _record_discovery_audits(issuer, audits):
    """Add the events _run_discovery() decided were owed.

    Audit rather than mutate: nothing here changed the issuer, and what
    is being recorded is that this cluster asked a provider a question
    and did or did not like the answer. "Which administrator configured
    this issuer while its provider was unreachable" is exactly the
    question asked after a login flow stops working.
    """
    for message, extra in audits:
        issuer.add_event(EVENT_TYPE_AUDIT, message, extra=extra)


class AuthIssuersEndpoint(api_base.Resource):
    scope_family = 'issuer'

    @swag_from(api_base.swagger_helper(
        'auth', 'List the trusted identity issuers.',
        [],
        [(200, 'The configured trusted issuers.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self):
        # Soft-deleted issuers are gone as far as an operator is
        # concerned: they no longer resolve by name and no longer vouch
        # for anyone, so listing them would misrepresent who this
        # cluster trusts.
        return [i.external_view() for i in TrustedIssuers(
            [partial(baseobject.state_filter, TrustedIssuer.ACTIVE_STATES)])]

    @swag_from(api_base.swagger_helper(
        'auth', 'Configure a trusted identity issuer.',
        [
            ('name', 'body', 'string',
             'A unique name for this issuer.', True),
            ('issuer_url', 'body', 'string',
             'The exact value expected in a token\'s iss claim.', True),
            ('jwks_uri', 'body', 'url',
             'Where the issuer publishes its signing keys. Optional: '
             'discovery fills it in from the issuer\'s published '
             'configuration when it is not supplied.', False),
            ('audience', 'body', 'string',
             'The value expected in a token\'s aud claim.', True),
            ('client_id', 'body', 'string',
             'The OAuth client identifier registered at this issuer for '
             'human login. Only needed for human login.', False),
            ('oidc_scopes', 'body', 'string',
             'The OpenID Connect scopes to request at login, as a single '
             'space separated string. Only needed for human login.', False),
            ('discover', 'body', 'boolean',
             'Whether to fetch the issuer\'s OpenID Connect discovery '
             'document. Defaults to true.', False)
        ],
        [(200, 'The issuer as created.', trusted_issuer_example),
         (400, 'A required field is missing or malformed.', None),
         (409, 'An issuer of that name already exists.', None),
         (502, 'Discovery was required and the issuer did not answer.', None),
         (503, 'Discovery is unavailable on this cluster.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def post(self, name=None, issuer_url=None, jwks_uri=None, audience=None,
             client_id=None, oidc_scopes=None, discover=None):
        if not name:
            return sf_api.error(400, 'no name specified')
        if not isinstance(name, str) or len(name) > 255:
            return sf_api.error(400, 'name is not a valid string')

        # An omitted discover is not the same as an explicit false --
        # the default is to discover, and a caller who says
        # {"discover": false} has asked for the opposite -- so the None
        # test comes first and declared_boolean() reads what is left.
        # The raw body reaches a handler unconverted, and the string
        # 'false' is true to Python while the published schema says it
        # is false.
        if discover is None:
            discover = True
        else:
            discover = validation.declared_boolean(discover)

        err = _validate_issuer_arguments(
            issuer_url, jwks_uri, audience, client_id=client_id,
            oidc_scopes=oidc_scopes, discover=discover)
        if err:
            return err

        # Before the lock, because discovery is an outbound fetch with
        # a timeout on it and the lock is a cluster wide one held
        # across a read and a write. Nothing here writes, so there is
        # nothing for the lock to protect yet.
        discovered = None
        audits = []
        if discover:
            discovered, jwks_uri, audits, err = _run_discovery(
                issuer_url, jwks_uri, mandatory=jwks_uri is None)
            if err:
                return err

        # The URL check and the create are one decision, so they are
        # taken together. See _issuer_url_lock.
        with _issuer_url_lock():
            err = _issuer_url_taken(issuer_url)
            if err:
                return err

            found = discovered or {}
            issuer = TrustedIssuer.new(
                name, issuer_url, jwks_uri, audience,
                # An empty string stores as NULL here as well, so that
                # a create and an update which were sent the same body
                # leave the same row behind, and so that a column which
                # is not set has one spelling.
                client_id=client_id or None,
                oidc_scopes=oidc_scopes or None,
                authorization_endpoint=found.get('authorization_endpoint'),
                token_endpoint=found.get('token_endpoint'),
                device_authorization_endpoint=found.get(
                    'device_authorization_endpoint'),
                discovered_at=found.get('discovered_at'))

        if not issuer:
            return sf_api.error(409, 'issuer already exists')

        _record_discovery_audits(issuer, audits)
        return issuer.external_view()


class AuthIssuerEndpoint(api_base.Resource):
    scope_family = 'issuer'

    @swag_from(api_base.swagger_helper(
        'auth', 'Fetch a trusted identity issuer.',
        [('issuer_name', 'path', 'string', 'The issuer name.', True)],
        [(200, 'The issuer.', trusted_issuer_example),
         (404, 'Issuer not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self, issuer_name=None):
        issuer = TrustedIssuer.from_db_by_name(issuer_name)
        if not issuer:
            return sf_api.error(404, 'issuer not found')
        return issuer.external_view()

    @swag_from(api_base.swagger_helper(
        'auth', 'Update a trusted identity issuer.',
        [
            ('issuer_name', 'path', 'string', 'The issuer name.', True),
            ('issuer_url', 'body', 'string',
             'The exact value expected in a token\'s iss claim.', True),
            ('jwks_uri', 'body', 'url',
             'Where the issuer publishes its signing keys. Optional: '
             'discovery fills it in from the issuer\'s published '
             'configuration when it is not supplied.', False),
            ('audience', 'body', 'string',
             'The value expected in a token\'s aud claim.', True),
            ('client_id', 'body', 'string',
             'The OAuth client identifier registered at this issuer for '
             'human login. Only needed for human login.', False),
            ('oidc_scopes', 'body', 'string',
             'The OpenID Connect scopes to request at login, as a single '
             'space separated string. Only needed for human login.', False),
            ('discover', 'body', 'boolean',
             'Whether to fetch the issuer\'s OpenID Connect discovery '
             'document. Defaults to true.', False)
        ],
        [(200, 'The updated issuer.', trusted_issuer_example),
         (400, 'A required field is missing or malformed.', None),
         (404, 'Issuer not found.', None),
         (502, 'Discovery was required and the issuer did not answer.', None),
         (503, 'Discovery is unavailable on this cluster.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def put(self, issuer_name=None, issuer_url=None, jwks_uri=None,
            audience=None, client_id=None, oidc_scopes=None, discover=None):
        issuer = TrustedIssuer.from_db_by_name(issuer_name)
        if not issuer:
            return sf_api.error(404, 'issuer not found')

        # See the create path for why an omitted discover is read
        # before declared_boolean() rather than through it.
        if discover is None:
            discover = True
        else:
            discover = validation.declared_boolean(discover)

        err = _validate_issuer_arguments(
            issuer_url, jwks_uri, audience, client_id=client_id,
            oidc_scopes=oidc_scopes, discover=discover)
        if err:
            return err

        # Outside the lock, as on the create path: the fetch writes
        # nothing and holding a cluster wide lock across somebody
        # else's network timeout is a cost with no benefit.
        discovered = None
        audits = []
        if discover:
            discovered, jwks_uri, audits, err = _run_discovery(
                issuer_url, jwks_uri, mandatory=jwks_uri is None)
            if err:
                return err

        # Excluding this issuer, or repointing anything else about an
        # issuer while leaving its URL alone would conflict with itself.
        # Held under the same lock as the create path, for the same
        # reason: two concurrent repoints onto one URL would otherwise
        # both see it free.
        with _issuer_url_lock():
            err = _issuer_url_taken(
                issuer_url, by_someone_other_than=issuer_name)
            if err:
                return err

            issuer.update(issuer_url, jwks_uri, audience,
                          client_id=client_id, oidc_scopes=oidc_scopes,
                          discovered=discovered)

        # Belt and braces, and cheap. JWKSCache._client_and_lock()
        # already replaces a cached client whose uri no longer matches
        # the issuer's, so a repointed JWKS URI is picked up without
        # this -- but a cache which keeps serving the old provider's
        # keys after an operator has disowned it is the kind of failure
        # nobody would find quickly, and the write above is exactly the
        # moment we know the value may have moved.
        federation.JWKS_CACHE.forget(str(issuer.uuid))

        _record_discovery_audits(issuer, audits)
        return issuer.external_view()

    @swag_from(api_base.swagger_helper(
        'auth', 'Delete a trusted identity issuer.',
        [('issuer_name', 'path', 'string', 'The issuer name.', True)],
        [(200, 'The issuer was deleted.', None),
         (404, 'Issuer not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def delete(self, issuer_name=None):
        issuer = TrustedIssuer.from_db_by_name(issuer_name)
        if not issuer:
            return sf_api.error(404, 'issuer not found')

        issuer.add_event(
            EVENT_TYPE_AUDIT, 'delete issuer request from REST API')
        issuer.delete()
        return issuer.external_view()


class AuthIssuerDiscoverEndpoint(api_base.Resource):
    # Named for the issuer family rather than the auth one, because the
    # leading word of the class name would otherwise derive 'auth' and
    # a token scoped for issuer administration would not reach it.
    scope_family = 'issuer'

    @swag_from(api_base.swagger_helper(
        'auth', 'Re-run OIDC discovery against a trusted issuer.',
        [('issuer_name', 'path', 'string', 'The issuer name.', True)],
        [(200, 'The issuer, with what discovery found.',
          trusted_issuer_example),
         (404, 'Issuer not found.', None),
         (502, 'The issuer did not answer with a usable document.', None),
         (503, 'Discovery is unavailable on this cluster.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def post(self, issuer_name=None):
        """Ask the provider where its endpoints are now.

        Discovery otherwise runs only when an issuer is configured,
        which is deliberate -- putting an identity provider on a path
        that cluster operation depends on is a reliability regression
        sold as a security improvement -- and the cost of that choice is
        that an endpoint which moves at the provider stays stale until
        somebody asks. This is how an administrator asks.

        A failure here is a refusal rather than an audited warning,
        which is the opposite of what a create or an update does with
        the same failure. The difference is what was requested: a create
        wants an issuer and discovery is a convenience on the way, while
        this request *is* the discovery, so answering 200 to a fetch
        that did not happen would be a lie about the only thing the
        caller asked for.
        """
        issuer = TrustedIssuer.from_db_by_name(issuer_name)
        if not issuer:
            return sf_api.error(404, 'issuer not found')

        # One read rather than two properties, see
        # TrustedIssuer.configuration().
        attrs = issuer.configuration()
        if not attrs:
            return sf_api.error(404, 'issuer not found')

        discovered, jwks_uri, audits, err = _run_discovery(
            attrs.issuer_url, attrs.jwks_uri, mandatory=True)
        if err:
            return err

        # A stored jwks_uri is never replaced by a discovered one, even
        # though a refresh is as good a moment as any to notice that
        # the two differ. The reason is that we cannot tell a value an
        # operator typed from one a previous discovery run wrote, and
        # overwriting the first kind is documented as breaking a real
        # deployment: a provider whose published JWKS path answers with
        # a redirect to a CDN needs the CDN path configured by hand. An
        # operator who wants the published value can update the issuer
        # without one and have discovery fill it in.
        issuer.record_discovery(jwks_uri, discovered)

        # The write above may have filled in a jwks_uri this issuer did
        # not have. See the update path for why forgetting the cached
        # client is belt and braces rather than the mechanism.
        federation.JWKS_CACHE.forget(str(issuer.uuid))

        issuer.add_event(
            EVENT_TYPE_AUDIT, 'discovery request from REST API',
            extra={'issuer_url': attrs.issuer_url})
        _record_discovery_audits(issuer, audits)
        return issuer.external_view()


mapping_rule_example = """{
    "namespace": "ci",
    "name": "ryll-develop",
    "issuer": "github",
    "bound_claims": {
        "repository": "shakenfist/ryll",
        "ref": ["refs/heads/develop", "refs/heads/main"]
    },
    "scopes": ["blob.read", "artifact.*"],
    "key_ttl": 3600,
    "key_name_prefix": "ryll-ci"
}
"""


def _rule_arguments(issuer, bound_claims, scopes, key_ttl, key_name_prefix):
    """Normalise the rule body, or return an error response.

    Returns (kwargs, error_response), exactly one of which is None.
    Only presence is checked here; the meaning of each value is the
    MappingRule's business, so that a rule created through the API and
    a rule created any other way cannot diverge on what is safe.
    """
    missing = [
        field for field, value in [
            ('issuer', issuer), ('bound_claims', bound_claims),
            ('scopes', scopes), ('key_ttl', key_ttl),
            ('key_name_prefix', key_name_prefix)]
        if value is None
    ]
    if missing:
        return None, sf_api.error(
            400, 'missing required field(s): %s' % ', '.join(missing))

    # A rule may not grant scopes its author does not itself hold.
    #
    # This is the cap `_namespace_keys_putpost` applies when minting a
    # key directly, and it belongs here for the same reason with one
    # extra hop in between. A rule is a standing instruction to mint a
    # key, so without this a token scoped `rule.write` could write a
    # rule granting `*`, satisfy that rule's own bound claims with an
    # identity token from a trusted issuer, and exchange it for a
    # wildcard key. In the system namespace the wildcard reaches
    # cluster-admin, routing straight around the rule that admin
    # endpoints require the cluster-admin scope. The exchange
    # endpoint cannot catch it either, because by that point the rule
    # is indistinguishable from a legitimately authored one.
    #
    # None from caller_scopes() means unrestricted, which is every
    # operator holding a legacy key, so their rules are unaffected.
    held = api_base.caller_scopes()
    if held is not None and isinstance(scopes, list):
        ungranted = sorted({
            s for s in scopes
            if isinstance(s, str) and not api_scopes.satisfies(held, s)
        })
        if ungranted:
            return None, sf_api.error(
                403, 'a rule cannot grant scopes you do not hold: %s'
                     % ', '.join(ungranted))

    return {
        'issuer': issuer,
        'bound_claims': bound_claims,
        'scopes': scopes,
        'key_ttl': key_ttl,
        'key_name_prefix': key_name_prefix
    }, None


class AuthNamespaceRulesEndpoint(api_base.Resource):
    scope_family = 'rule'

    @swag_from(api_base.swagger_helper(
        'auth', 'List the identity mapping rules for a namespace.',
        [('namespace', 'path', 'string', 'The namespace.', True)],
        [(200, 'The namespace\'s mapping rules.', None),
         (404, 'Namespace not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def get(self, namespace=None, namespace_from_db=None):
        # Soft-deleted rules are gone as far as an operator is
        # concerned: they no longer resolve by name and no longer mint
        # anything, so listing them would misrepresent who this
        # namespace federates with.
        return [
            r.external_view() for r in MappingRules(
                [partial(baseobject.state_filter, MappingRule.ACTIVE_STATES)],
                namespace=namespace)
        ]

    @swag_from(api_base.swagger_helper(
        'auth', 'Create an identity mapping rule for a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace.', True),
            ('name', 'body', 'string',
             'A name for this rule, unique within the namespace.', True),
            ('issuer', 'body', 'string',
             'The name of the trusted issuer whose tokens this rule '
             'accepts.', True),
            # Deliberately a bare 'dict', with no element schema (D47).
            # The claim names are the issuer's, and the matchers are
            # already completely guarded by hand below, better than a
            # schema could manage: federation.claim_matches() returns
            # False *silently* for a matcher which is not a string or a
            # list of strings, so a rule stored with any other shape
            # would look like a grant and would never match an
            # identity. This endpoint refusing it at write time is the
            # only place that failure is ever visible, and the messages
            # it writes name the claim and the type it was given.
            # Census finding F3.
            ('bound_claims', 'body', 'dict',
             'Claim name to matcher. A matcher is an exact string, or a '
             'list of acceptable strings. Matching is exact: no globbing, '
             'no regular expressions, no prefix matching. At least one '
             'claim must be bound.', True),
            ('scopes', 'body', 'arrayofstring',
             'The scopes granted to keys minted through this rule. Must be '
             'non-empty.', True),
            # Not unsignedinteger: validate_key_ttl() refuses zero and
            # negatives outright, so a published minimum of 0 would
            # document a value the server answers 400 to. Its upper
            # bound was enforced and invisible, which is the same
            # defect as the events limit cap this phase exists to
            # publish.
            ('key_ttl', 'body', 'integer',
             'Seconds of life for keys minted through this rule. Must be '
             f'positive, and no greater than {MAX_KEY_TTL_SECONDS}.', True,
             {'minimum': 1, 'maximum': MAX_KEY_TTL_SECONDS}),
            ('key_name_prefix', 'body', 'string',
             'Prefix for minted key names. The cluster appends a random '
             'discriminator, so minted names never collide.', True)
        ],
        [(200, 'The rule as created.', mapping_rule_example),
         (400, 'A required field is missing or malformed.', None),
         (404, 'Namespace not found.', None),
         (409, 'A rule of that name already exists in this namespace.',
          None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def post(self, namespace=None, name=None, issuer=None, bound_claims=None,
             scopes=None, key_ttl=None, key_name_prefix=None,
             namespace_from_db=None):
        if not name:
            return sf_api.error(400, 'no name specified')
        if not isinstance(name, str) or len(name) > 255:
            return sf_api.error(400, 'name is not a valid string')

        kwargs, err = _rule_arguments(
            issuer, bound_claims, scopes, key_ttl, key_name_prefix)
        if err:
            return err

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'create mapping rule request from REST API',
            extra={'rule': name})

        try:
            rule = MappingRule.new(namespace, name, **kwargs)
        except RuleValidationError as e:
            return sf_api.error(400, str(e), suppress_traceback=True)

        if not rule:
            return sf_api.error(409, 'rule already exists')
        return rule.external_view()


class AuthNamespaceRuleEndpoint(api_base.Resource):
    scope_family = 'rule'

    @swag_from(api_base.swagger_helper(
        'auth', 'Fetch an identity mapping rule.',
        [('namespace', 'path', 'string', 'The namespace.', True),
         ('rule_name', 'path', 'string', 'The rule name.', True)],
        [(200, 'The rule.', mapping_rule_example),
         (404, 'Namespace or rule not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def get(self, namespace=None, rule_name=None, namespace_from_db=None):
        rule = MappingRule.from_db_by_name(namespace, rule_name)
        if not rule:
            return sf_api.error(404, 'rule not found')
        return rule.external_view()

    @swag_from(api_base.swagger_helper(
        'auth', 'Update an identity mapping rule.',
        [
            ('namespace', 'path', 'string', 'The namespace.', True),
            ('rule_name', 'path', 'string', 'The rule name.', True),
            ('issuer', 'body', 'string',
             'The name of the trusted issuer whose tokens this rule '
             'accepts.', True),
            # Unstructured for the same reason as the create endpoint
            # above: the hand written matcher guards are both wider and
            # more precise than a properties block, and they are the
            # only thing standing between a malformed matcher and a
            # rule which silently matches nobody (D47, census finding
            # F3).
            ('bound_claims', 'body', 'dict',
             'Claim name to matcher, as for creation.', True),
            ('scopes', 'body', 'arrayofstring',
             'The scopes granted to keys minted through this rule.', True),
            # Bounded for the same reason as the create endpoint's
            # declaration above; both reach validate_key_ttl().
            ('key_ttl', 'body', 'integer',
             'Seconds of life for keys minted through this rule. Must be '
             f'positive, and no greater than {MAX_KEY_TTL_SECONDS}.', True,
             {'minimum': 1, 'maximum': MAX_KEY_TTL_SECONDS}),
            ('key_name_prefix', 'body', 'string',
             'Prefix for minted key names.', True)
        ],
        [(200, 'The updated rule.', mapping_rule_example),
         (400, 'A required field is missing or malformed.', None),
         (404, 'Namespace or rule not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def put(self, namespace=None, rule_name=None, issuer=None,
            bound_claims=None, scopes=None, key_ttl=None,
            key_name_prefix=None, namespace_from_db=None):
        rule = MappingRule.from_db_by_name(namespace, rule_name)
        if not rule:
            return sf_api.error(404, 'rule not found')

        kwargs, err = _rule_arguments(
            issuer, bound_claims, scopes, key_ttl, key_name_prefix)
        if err:
            return err

        # Updating a rule does not touch keys already minted from it.
        # A minted key stands alone and its provenance records the
        # claims it actually satisfied, so narrowing a rule does not
        # retroactively narrow a live key -- delete the key for that.
        try:
            rule.update(**kwargs)
        except RuleValidationError as e:
            return sf_api.error(400, str(e), suppress_traceback=True)

        return rule.external_view()

    @swag_from(api_base.swagger_helper(
        'auth', 'Delete an identity mapping rule.',
        [('namespace', 'path', 'string', 'The namespace.', True),
         ('rule_name', 'path', 'string', 'The rule name.', True)],
        [(200, 'The rule was deleted.', None),
         (404, 'Namespace or rule not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def delete(self, namespace=None, rule_name=None, namespace_from_db=None):
        rule = MappingRule.from_db_by_name(namespace, rule_name)
        if not rule:
            return sf_api.error(404, 'rule not found')

        rule.add_event(
            EVENT_TYPE_AUDIT, 'delete mapping rule request from REST API')
        rule.delete()
        return rule.external_view()


# Namespace capacity claims.
#
# A claim is a namespace's promise of aggregate cluster capacity, so
# every verb here is a cluster administration operation even though the
# resource hangs off a namespace: capacity promised to one namespace is
# capacity refused to every other one. That is why these carry
# caller_is_admin, where the sibling key and rule endpoints are gated on
# namespace ownership alone -- a namespace administering its own
# credentials or its own federation affects nobody else. Delegated
# (non-admin) claim creation is named as future work in
# docs/plans/PLAN-scheduler-reservations-phase-00-decisions.md.
#
# The scope family is the derived 'auth', deliberately not an override.
# A new family is an addition to the vocabulary operators write in
# mapping rules, and cluster-admin is the gate that actually matters
# here: a token needs both that and the derived scope.

claim_example = """{
    "uuid": "0b2b4f76-0a1b-4d0f-8b3f-6f1a2c4d5e6f",
    "namespace": "ci",
    "state": "created",
    "version": 1,
    "metadata": {},
    "coverage_state": "active",
    "limit_cpus": 40,
    "limit_memory_mb": 81920,
    "limit_disk_gb": 2000,
    "used_cpus": 12,
    "used_memory_mb": 24576,
    "used_disk_gb": 600,
    "peak_used_cpus": 28,
    "peak_used_memory_mb": 57344,
    "peak_used_disk_gb": 1400,
    "expires_at": 1755300000.0,
    "updated_at": 1755213600.0
}
"""


# How a claim refusal becomes an HTTP status.
#
# A refusal is not a failure: the guarded transaction ran and decided
# no, so the response has to say which kind of no it was.
#
# * 'capacity' is 507, which is what this API already answers for every
#   other capacity exhaustion -- instance scheduling, network address
#   exhaustion, floating address exhaustion. The cluster is full and no
#   change to the request will help until something is released.
# * 'no_cluster_capacity' and 'conflict' are 503, the code api_base
#   already answers when the database tier is unreachable, because both
#   are transient and the correct client behaviour is to retry. The
#   first means the reconciler has not built the cluster capacity
#   singleton yet; the second means the claim row kept moving under a
#   concurrent writer until the optimistic retry budget ran out.
#   Deliberately not 409: nothing about the request is wrong, and a
#   caller that read it as a durable conflict would abandon a claim it
#   could have had a second later.
# * 'exists', 'below_usage' and 'not_active' are 409, which is what
#   this API already answers when a request conflicts with the durable
#   state of a resource (a duplicate mapping rule name). The caller has
#   to change something -- delete the claim it already has, ask for a
#   smaller shrink, replace the expired claim -- before it can succeed.
# * 'not_found' is 404, and is only reachable as a race: the claim
#   resolved through arg_is_claim_ref and was deleted before the
#   mutation reached it.
CLAIM_REFUSAL_STATUS = {
    'capacity': 507,
    'no_cluster_capacity': 503,
    'conflict': 503,
    'exists': 409,
    'below_usage': 409,
    'not_active': 409,
    'not_found': 404
}

CLAIM_REFUSAL_MESSAGE = {
    'capacity': 'the cluster does not have the capacity to promise this claim',
    'no_cluster_capacity': (
        'the cluster capacity accounting is not available yet, please retry'),
    'conflict': (
        'this claim was being changed concurrently and the update gave up, '
        'please retry'),
    'exists': 'this namespace already holds an active claim',
    'below_usage': (
        'a claim cannot be shrunk below what it is already using'),
    'not_active': (
        'this claim is no longer active and cannot be changed, delete it and '
        'create a new one'),
    'not_found': 'namespace claim not found'
}


def _claim_number(value):
    """Render one dimension number.

    The per-dimension detail is float on the wire, because it shares a
    shape with the scheduler's node denials where a decayed demand
    figure really is fractional. Claim limits are whole cpus, megabytes
    and gigabytes, so print them as such rather than as "40.0".
    """
    if float(value).is_integer():
        return '%d' % int(value)
    return '%s' % value


def _claim_dimension_detail(dimensions):
    """The dimensions a guard refused on, rendered for a caller.

    The point of a capacity guard that reports per-dimension detail is
    that the caller finds out which dimension did not fit, so it
    reaches the response body rather than only the logs. sf_api.error()
    carries a message and nothing else, so the detail goes in the
    message.

    Only the dimensions which actually failed, when the reply says
    which; the whole set otherwise, because a refusal with no exceeded
    dimension means the read-back after the rollback could not
    reconstruct which one it was and reporting nothing would be worse.
    """
    exceeded = [d for d in dimensions if d.get('exceeded')] or list(dimensions)
    return ', '.join(
        '%s (limit %s, used %s, requested %s)'
        % (d['dimension'], _claim_number(d['limit']),
           _claim_number(d['used']), _claim_number(d['requested']))
        for d in exceeded)


def _claim_refusal(refusal):
    """Turn a ClaimRefused into the response a caller can act on."""
    status = CLAIM_REFUSAL_STATUS.get(refusal.reason)
    if status is None:
        # The database layer grew a refusal reason this module has not
        # been taught. That is a server side gap, so it must not be
        # reported as the caller's fault.
        LOG.with_fields({'reason': refusal.reason}).error(
            'Unrecognised namespace claim refusal reason')
        return sf_api.error(
            500, 'namespace claim refused for an unrecognised reason: %s'
                 % refusal.reason, suppress_traceback=True)

    message = CLAIM_REFUSAL_MESSAGE[refusal.reason]
    detail = _claim_dimension_detail(refusal.dimensions)
    if detail:
        message = '%s: %s' % (message, detail)
    return sf_api.error(status, message, suppress_traceback=True)


def _claim_limit(name, value):
    """One claim limit from the request body, or an error response.

    Returns (value, error), exactly one of which is None. bool is
    rejected explicitly because it is an int in Python, and a body
    saying `"limit_cpus": true` would otherwise quietly claim one cpu.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None, sf_api.error(400, '%s is not an integer' % name)
    if value < 0:
        return None, sf_api.error(400, '%s cannot be negative' % name)
    return value, None


def _claim_expiry(value):
    """The claim duration from the request body, or an error response.

    A duration in seconds, not an absolute time, for the same reason
    the database layer takes one: the expiry sweep only ever compares
    against the cluster's own clock, so a client computed timestamp
    would be evaluated against a clock the client never saw. Accepting
    one would mean documenting a skew the API cannot measure.

    Zero and negatives are refused rather than clamped. A claim which
    is already expired the moment it is created holds no capacity for
    anybody and cannot be grown (an inactive claim is refused), so it
    is a trap rather than a shorthand.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None, sf_api.error(400, 'expires_in_seconds is not an integer')
    if value < 1:
        return None, sf_api.error(400, 'expires_in_seconds must be positive')
    return value, None


def arg_is_claim_ref(func):
    """Resolve a claim, scoped to the namespace in the route.

    A claim has no name, so the reference is always a uuid. A uuid on
    its own would let a caller address a claim through any namespace's
    URL, which would make the namespace segment decorative -- and the
    day D15's delegated claim creation lands, decorative is a
    cross-tenant read. The namespace in the path is therefore checked
    against the claim's own, and a mismatch answers the same 404 a
    missing claim does, so the URL does not disclose which claims exist
    in namespaces the caller was not asking about.
    """
    def wrapper(*args, **kwargs):
        claim_ref = kwargs.get('claim_ref')
        if not claim_ref:
            return sf_api.error(400, 'missing claim in request')

        if not util_general.valid_uuid4(claim_ref):
            # There is no name to fall back to, so this cannot name a
            # claim at all.
            return sf_api.error(404, 'namespace claim not found')

        c = NamespaceClaim.from_db(claim_ref, suppress_failure_audit=True)
        if not c or c.state.value == dbo.STATE_DELETED:
            return sf_api.error(404, 'namespace claim not found')

        if c.namespace != kwargs.get('namespace'):
            LOG.with_fields({
                'namespace': kwargs.get('namespace'),
                'claim': claim_ref,
                'claim_namespace': c.namespace
            }).info('Namespace claim not found, it belongs to another '
                    'namespace')
            return sf_api.error(404, 'namespace claim not found')

        kwargs['claim_from_db'] = c
        return func(*args, **kwargs)
    return wrapper


class AuthNamespaceClaimsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'List the capacity claims held by a namespace.',
        [('namespace', 'path', 'string', 'The namespace.', True)],
        [(200, 'The namespace\'s capacity claims.', '[%s]' % claim_example),
         (401, 'The caller is not a cluster administrator.', None),
         (404, 'Namespace not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def get(self, namespace=None, namespace_from_db=None):
        # Every claim the namespace holds, whatever its coverage state.
        # An expired claim still has a row, still has to be deleted by
        # hand, and is the only claim whose existence explains why a
        # namespace's placements stopped being charged to it -- so
        # hiding it would hide the one thing an operator can act on.
        return [c.external_view(row=row)
                for c, row in NamespaceClaims(namespace=namespace).with_rows()]

    @swag_from(api_base.swagger_helper(
        'auth', 'Claim aggregate cluster capacity for a namespace.',
        [
            ('namespace', 'path', 'string', 'The namespace.', True),
            ('limit_cpus', 'body', 'unsignedinteger',
             'The number of vCPUs this namespace may hold at once.', True),
            ('limit_memory_mb', 'body', 'unsignedinteger',
             'The instance memory, in megabytes, this namespace may hold '
             'at once.', True),
            ('limit_disk_gb', 'body', 'unsignedinteger',
             'The instance disk, in gigabytes, this namespace may hold at '
             'once.', True),
            # A duration, not a timestamp, and positive: see
            # _claim_expiry(), which is what backs both halves of that.
            ('expires_in_seconds', 'body', 'integer',
             'How long this claim covers placements for, in seconds from '
             'now. A duration rather than a timestamp: the expiry is '
             'computed from the cluster\'s clock, which is the only clock '
             'the expiry sweep ever compares against. Must be positive.',
             True, {'minimum': 1})
        ],
        [(200, 'The claim as created.', claim_example),
         (400, 'A required field is missing or malformed.', None),
         (401, 'The caller is not a cluster administrator.', None),
         (404, 'Namespace not found.', None),
         (409, 'This namespace already holds an active claim.', None),
         (503, 'The cluster capacity accounting is not available yet. '
               'Retry.', None),
         (507, 'The cluster does not have the capacity to promise this '
               'claim.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def post(self, namespace=None, limit_cpus=None, limit_memory_mb=None,
             limit_disk_gb=None, expires_in_seconds=None,
             namespace_from_db=None):
        limits = {}
        for name, value in [('limit_cpus', limit_cpus),
                            ('limit_memory_mb', limit_memory_mb),
                            ('limit_disk_gb', limit_disk_gb)]:
            if value is None:
                return sf_api.error(400, 'no %s specified' % name)
            limits[name], err = _claim_limit(name, value)
            if err:
                return err

        if expires_in_seconds is None:
            return sf_api.error(400, 'no expires_in_seconds specified')
        expiry, err = _claim_expiry(expires_in_seconds)
        if err:
            return err

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'create namespace claim request from REST API',
            extra=dict(limits, expires_in_seconds=expiry))

        try:
            c = NamespaceClaim.new(
                namespace, limits['limit_cpus'], limits['limit_memory_mb'],
                limits['limit_disk_gb'], expiry)
        except ClaimRefused as e:
            return _claim_refusal(e)

        return c.external_view()


class AuthNamespaceClaimEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Fetch a capacity claim.',
        [('namespace', 'path', 'string', 'The namespace.', True),
         ('claim_ref', 'path', 'uuid', 'The claim UUID.', True)],
        [(200, 'The claim.', claim_example),
         (401, 'The caller is not a cluster administrator.', None),
         (404, 'Namespace or claim not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @arg_is_namespace
    @arg_is_claim_ref
    @api_base.log_token_use
    def get(self, namespace=None, claim_ref=None, namespace_from_db=None,
            claim_from_db=None):
        return claim_from_db.external_view()

    @swag_from(api_base.swagger_helper(
        'auth', 'Grow, shrink or re-date a capacity claim.',
        [
            ('namespace', 'path', 'string', 'The namespace.', True),
            ('claim_ref', 'path', 'uuid', 'The claim UUID.', True),
            ('limit_cpus', 'body', 'unsignedinteger',
             'Optional. The new vCPU limit. Growing is an admission '
             'decision against the cluster; shrinking is permitted down to '
             'what the claim is already using and no further.', False),
            ('limit_memory_mb', 'body', 'unsignedinteger',
             'Optional. The new memory limit, in megabytes.', False),
            ('limit_disk_gb', 'body', 'unsignedinteger',
             'Optional. The new disk limit, in gigabytes.', False),
            ('expires_in_seconds', 'body', 'integer',
             'Optional. A new expiry, in seconds from now. A duration '
             'rather than a timestamp, computed from the cluster\'s clock. '
             'Must be positive.', False, {'minimum': 1})
        ],
        [(200, 'The updated claim.', claim_example),
         (400, 'A field is malformed, or nothing was asked for.', None),
         (401, 'The caller is not a cluster administrator.', None),
         (404, 'Namespace or claim not found.', None),
         (409, 'The claim is not active, or the requested limit is below '
               'what the claim is already using.', None),
         (503, 'The cluster capacity accounting is not available yet, or '
               'the claim was contended. Retry.', None),
         (507, 'The cluster does not have the capacity to grow this '
               'claim.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @arg_is_namespace
    @arg_is_claim_ref
    @api_base.log_token_use
    def put(self, namespace=None, claim_ref=None, limit_cpus=None,
            limit_memory_mb=None, limit_disk_gb=None, expires_in_seconds=None,
            namespace_from_db=None, claim_from_db=None):
        # A field mask, exactly as the database layer requires: without
        # one there is no way to tell a deliberate zero from an argument
        # the caller never sent, and an unmasked write would shrink
        # every dimension the caller did not mention to nothing
        # (see "Attribute updates use field masks" in
        # docs/developer_guide/standards.md).
        fields = []
        values = {'limit_cpus': 0, 'limit_memory_mb': 0, 'limit_disk_gb': 0,
                  'expires_in_seconds': 0}

        for name, value in [('limit_cpus', limit_cpus),
                            ('limit_memory_mb', limit_memory_mb),
                            ('limit_disk_gb', limit_disk_gb)]:
            if value is None:
                continue
            values[name], err = _claim_limit(name, value)
            if err:
                return err
            fields.append(name)

        if expires_in_seconds is not None:
            values['expires_in_seconds'], err = _claim_expiry(
                expires_in_seconds)
            if err:
                return err
            fields.append('expires_in_seconds')

        if not fields:
            return sf_api.error(400, 'no claim fields to update specified')

        try:
            claim_from_db.update(fields=fields, **values)
        except ClaimRefused as e:
            return _claim_refusal(e)

        return claim_from_db.external_view()

    @swag_from(api_base.swagger_helper(
        'auth', 'Delete a capacity claim, returning its capacity to the '
                'cluster.',
        [('namespace', 'path', 'string', 'The namespace.', True),
         ('claim_ref', 'path', 'uuid', 'The claim UUID.', True)],
        [(200, 'The claim as it was immediately before deletion.',
          claim_example),
         (401, 'The caller is not a cluster administrator.', None),
         (404, 'Namespace or claim not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @arg_is_namespace
    @arg_is_claim_ref
    @api_base.log_token_use
    def delete(self, namespace=None, claim_ref=None, namespace_from_db=None,
               claim_from_db=None):
        # Recorded against the namespace rather than the claim.
        # hard_delete() removes the claim's own events along with the
        # claim, so an event written here would be destroyed by the call
        # it exists to explain -- and "who asked for my namespace's
        # claim to go" is exactly the question that outlives it. The
        # claim's own hard_delete() records the outcome the same way.
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete namespace claim request from REST API',
            extra={'claim': str(claim_from_db.uuid)})

        # Read the view before deleting rather than after. A claim has
        # no soft delete -- hard_delete() removes the row inside the
        # transaction which gives its capacity back -- so afterwards
        # there is nothing left to describe, and the sibling rule
        # endpoint's return-the-view-after-delete shape would answer
        # with a body full of nulls.
        view = claim_from_db.external_view()
        claim_from_db.hard_delete()
        return view


claim_events_example = """[
    ...,
    {
        "duration": null,
        "extra": {
            "limit_cpus": 40,
            "limit_disk_gb": 2000,
            "limit_memory_mb": 81920
        },
        "fqdn": "sf-1",
        "message": "db record created",
        "timestamp": 1755213600.0,
        "type": "audit"
    },
    ...
]"""


class AuthNamespaceClaimEventsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Get capacity claim event information.',
        [
            ('namespace', 'path', 'string', 'The namespace.', True),
            ('claim_ref', 'path', 'uuid', 'The claim UUID.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100 and is '
             'capped at 1000.', False, {'minimum': 1, 'maximum': 1000})
        ],
        [(200, 'Event information about a single capacity claim.',
          claim_events_example),
         (401, 'The caller is not a cluster administrator.', None),
         (404, 'Namespace or claim not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @arg_is_namespace
    @arg_is_claim_ref
    @api_base.log_token_use
    def get(self, namespace=None, claim_ref=None, event_type=None, limit=100,
            namespace_from_db=None, claim_from_db=None):
        # Only the events the claim itself still holds. A claim has no
        # soft delete, and hard_delete() removes its events inside the
        # transaction which returns its capacity -- which is why the
        # deletion event is recorded against the namespace instead, and
        # read through AuthNamespaceEventsEndpoint.
        return api_base.object_events_response(
            'namespace_claim', claim_from_db.uuid, limit, event_type)


federated_example = """{
    "namespace": "ci",
    "key_name": "ryll-ci-8fJ2mQ",
    "key": "sfk_..."
}
"""


def _federated_refusal(rule, reason, detail, namespace=None):
    """Refuse an exchange, auditing it to the rule's owner if we can.

    A stream of near-miss claim failures is what probing looks like,
    and the namespace owner is the person who needs to see it. So a
    failure against a rule we resolved is evented against that rule.

    A failure where no owner can be identified -- an unknown namespace,
    an unknown rule, a token from nobody we trust -- is logged and not
    evented. /auth/federated is unauthenticated, so eventing those
    would hand an anonymous caller a way to write unbounded rows into
    a namespace's audit log, or into no namespace at all.

    That restraint is about the *namespace* audit log, and it should
    not be read as a claim that an anonymous request writes nothing.
    app.py's log_request_info hook events every request to
    API_REQUESTS before routing, this endpoint included, and it runs
    ahead of the rate limit inside the method. So a flood does still
    write a row per request, just against the API's own log rather
    than a tenant's. That is pre-existing and shared with POST /auth,
    which is the other public route -- worth knowing, and not
    something this function fixes.

    The caller is told less than the log records. "federated exchange
    refused" plus a category is enough for an operator debugging their
    own workflow, and withholding which claim missed avoids turning
    the endpoint into an oracle for guessing a rule's contents.
    """
    if rule:
        rule.add_event(
            EVENT_TYPE_AUDIT, 'federated exchange refused',
            extra={'reason': reason, 'detail': detail})
    else:
        LOG.with_fields({
            'namespace': namespace, 'reason': reason
        }).info(f'Federated exchange refused: {detail}')

    return sf_api.error(401, f'federated exchange refused: {reason}')


class AuthFederatedEndpoint(api_base.Resource):
    # Unauthenticated by nature: the whole point is that the caller has
    # no Shaken Fist credential yet, only an identity from somewhere we
    # have been told to believe.
    @api_base.public
    @swag_from(api_base.swagger_helper(
        'auth', 'Exchange an identity token for a namespace key.',
        [
            ('token', 'body', 'string',
             'A signed identity token from a trusted issuer.', True),
            ('namespace', 'body', 'string',
             'The namespace to mint a key in.', True),
            ('rule', 'body', 'string',
             'The name of the mapping rule to exchange through.', True)
        ],
        [(200, 'The minted key. The secret is returned once and never '
               'again.', federated_example),
         (400, 'A required field is missing.', None),
         (401, 'The exchange was refused.', None),
         (413, 'The request body is too large.', None),
         (429, 'Too many exchange attempts from this source.', None),
         (503, 'The database is unavailable, so the exchange could not '
               'be checked for replay, or the JWKS CA bundle this '
               'deployment is configured with could not be loaded.',
          None)],
        requires_auth=False))
    def post(self, token=None, namespace=None, rule=None):
        # The order below is a security property rather than a style.
        # Each step is cheaper than the one after it, and the expensive
        # ones -- a JWKS fetch, a bcrypt hash -- sit behind checks that
        # an anonymous caller cannot pass by guessing.

        # 1. Size. The refusal that actually protects us is
        #    app.py's limit_federated_body_size hook, because by the
        #    time this method runs log_request has already parsed the
        #    body -- a check here cannot stop work that has happened.
        #    This copy stays as a backstop for callers that reach the
        #    method without going through the app's request hooks, and
        #    it is deliberately the same limit.
        if (flask.request.content_length or 0) > \
                config.FEDERATION_MAX_TOKEN_BYTES:
            return sf_api.error(413, 'request body too large')

        if not token or not isinstance(token, str):
            return sf_api.error(400, 'no token specified')
        if not namespace or not isinstance(namespace, str):
            return sf_api.error(400, 'no namespace specified')
        if not rule or not isinstance(rule, str):
            return sf_api.error(400, 'no rule specified')

        # 2. Rate limit per source address. Here, rather than earlier,
        #    because the argument checks above touch nothing but the
        #    request; and here, rather than after the issuer lookup,
        #    because that lookup is not free.
        #
        #    The design section had this the other way around, on the
        #    grounds that resolving an issuer was free and writing a
        #    counter row was not. That was wrong about the first half:
        #    issuer_claiming_url scans every configured issuer and
        #    reads state and attributes per row, so an anonymous caller
        #    who can produce a syntactically valid JWT with any iss at
        #    all could drive that scan as fast as they could send. It
        #    was the one unmetered database amplification path in the
        #    exchange, and it sat above the meter.
        #
        #    Counting the request costs one row per source per window,
        #    which is the same row that source would get by naming a
        #    real issuer, so nothing about the table's growth changes.
        source = flask.request.remote_addr or 'unknown'
        try:
            federation.enforce_rate_limit(source)
        except exceptions.RateLimited as e:
            LOG.with_fields({
                'namespace': namespace, 'source': source
            }).info(f'Federated exchange rate limited: {e}')
            return sf_api.error(429, 'too many federated exchange attempts',
                                suppress_traceback=True)

        # 3. Resolve the issuer from the unverified iss. No network yet:
        #    a made-up issuer must not be able to make us dial out.
        try:
            issuer = federation.issuer_for_token(token)
        except exceptions.UntrustedIssuer as e:
            return _federated_refusal(
                None, 'untrusted issuer', str(e), namespace=namespace)

        # 4 and 5. Signature, then audience, issuer and lifetime.
        try:
            claims = federation.validate_token(token, issuer)
        except exceptions.JWKSTrustAnchorUnusable as e:
            # Ours, not theirs. This is FEDERATION_JWKS_CA_BUNDLE
            # pointing at a file sf-api cannot load, so the token was
            # never examined and calling it rejected would be a lie
            # that sends the caller to their identity provider. The
            # detail goes to our log and not to the response, for the
            # same reason every other refusal here is terse.
            LOG.with_fields({'namespace': namespace}).error(
                f'Federated exchange misconfigured: {e}')
            return sf_api.error(
                503, 'federated exchange is unavailable, please retry',
                suppress_traceback=True)
        except exceptions.TokenValidationFailed as e:
            return _federated_refusal(
                None, 'token rejected', str(e), namespace=namespace)

        # 6. Only now look up the rule. Doing it after verification
        #    means an anonymous caller holding no valid token cannot
        #    use this endpoint to discover which rules exist.
        rule_from_db = MappingRule.from_db_by_name(namespace, rule)
        if not rule_from_db:
            return _federated_refusal(
                None, 'no such rule', f'no rule {namespace}/{rule}',
                namespace=namespace)

        #    Read the whole policy in one go, and refuse a damaged row
        #    rather than let it escape as an exception. Decision D31
        #    stopped the generic 500 handler putting repr(e) in the
        #    body, so CorruptMappingRule's rule UUID no longer reaches
        #    a caller that way either -- but going through this guard
        #    still turns an internal fault into a categorised refusal
        #    evented against the rule's owner via _federated_refusal,
        #    rather than an unevented 500 nobody but the log sees.
        #
        #    The guard belongs here and not around the lookup above.
        #    from_db_by_name reads the static row and the object state,
        #    neither of which decodes bound_claims or scopes, so it
        #    cannot raise this. Wrapping it looked like protection and
        #    was none: the first read that can actually fail was the
        #    issuer comparison below.
        #
        #    The refusal is evented against the rule, as the "rule has
        #    no scopes" one below is. The owner has been identified by
        #    this point, and a damaged rule is precisely the thing they
        #    need told. What the *caller* gets back is still the bare
        #    category, which is what keeps the UUID out of the answer.
        try:
            policy = rule_from_db.policy()
        except exceptions.CorruptMappingRule as e:
            LOG.with_fields({
                'namespace': namespace, 'rule': rule
            }).error(f'Federated exchange hit a damaged rule: {e}')
            return _federated_refusal(
                rule_from_db, 'rule is unusable', 'rule could not be read')

        if not policy:
            # The static row outlived its attributes row. Same refusal
            # as a rule that cannot be decoded: there is no policy to
            # apply, so there is nothing to mint against.
            LOG.with_fields({
                'namespace': namespace, 'rule': rule
            }).error('Federated exchange found a rule with no attributes')
            return _federated_refusal(
                rule_from_db, 'rule is unusable', 'rule has no attributes')

        if policy.issuer != issuer.name:
            return _federated_refusal(
                rule_from_db, 'wrong issuer',
                f'rule accepts {policy.issuer}, token is from '
                f'{issuer.name}')

        try:
            satisfied = federation.match_claims(
                claims, policy.bound_claims or {})
        except exceptions.ClaimMismatch as e:
            return _federated_refusal(
                rule_from_db, 'claims do not match', str(e))

        namespace_from_db = Namespace.from_db(
            namespace, suppress_failure_audit=True)
        if not namespace_from_db or \
                namespace_from_db.state.value == dbo.STATE_DELETED:
            # The rule outlived its namespace, which Namespace
            # hard delete is supposed to prevent. Refuse rather than
            # mint into a namespace that is on its way out.
            return _federated_refusal(
                rule_from_db, 'no such namespace',
                f'rule {namespace}/{rule} names a namespace which is gone')

        scopes = policy.scopes
        key_ttl = policy.key_ttl
        if not scopes or not key_ttl:
            return _federated_refusal(
                rule_from_db, 'rule is unusable',
                'rule has no scopes or no key_ttl')

        # 7. Refuse a replay of this token through this rule.
        #
        # An early draft of the design section numbered this ahead of the
        # rule lookup, which is not implementable: the pair being claimed
        # is (token, rule uuid), and there is no rule uuid until the rule
        # has been read. It sits after claim matching rather than
        # immediately after the lookup so that
        # a refusal for any *other* reason -- claims that do not match, a
        # namespace that has gone, a rule with no scopes -- does not
        # consume the token's single use. A caller who fixes their rule
        # and retries with the same still-valid token should succeed.
        #
        # It is the last gate before minting, which is what makes it
        # effective: two concurrent presentations of the same token
        # cannot both get past this line, because the second one's
        # insert collides with the first one's.
        #
        # If minting then fails, the token's use is spent with no key to
        # show for it. That is the right way round to be wrong -- the
        # caller asks their identity provider for another token, which
        # costs them a second, whereas the alternative is a window in
        # which a token mints twice.
        try:
            federation.refuse_replay(token, claims, rule_from_db)
        except exceptions.TokenReplayed as e:
            return _federated_refusal(
                rule_from_db, 'token already used', str(e))
        except exceptions.TokenValidationFailed as e:
            return _federated_refusal(
                rule_from_db, 'token rejected', str(e))

        # 8. Mint. The name carries a random discriminator so a
        #    workflow re-run gets its own key rather than silently
        #    rotating the secret out from under a still-running job.
        key_name = '%s-%s' % (
            policy.key_name_prefix, sf_random.random_id()[:8])
        secret = credentials.generate()

        minted = NamespaceKey.new(
            namespace, key_name, secret,
            expiry=time.time() + key_ttl,
            scopes=list(scopes),
            provenance={
                'source': 'federated',
                'rule': str(rule_from_db.uuid),
                'rule_name': rule_from_db.name,
                'issuer': issuer.name,
                # The claims that were actually satisfied, not the
                # rule's matchers. An audit should describe the grant
                # as it was made, not as the rule reads today.
                'claims': satisfied,
                'jti': claims.get('jti'),
                'sub': claims.get('sub')
            })

        minted.add_event(
            EVENT_TYPE_AUDIT, 'key minted by federated exchange',
            extra={'rule': str(rule_from_db.uuid), 'issuer': issuer.name,
                   'claims': satisfied, 'scopes': list(scopes)})
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'federated exchange minted a key',
            extra={'key_name': key_name, 'rule': rule_from_db.name,
                   'issuer': issuer.name, 'claims': satisfied})

        # The secret is returned here and never again: nothing stores
        # it, only its bcrypt hash.
        return {
            'namespace': namespace,
            'key_name': key_name,
            'key': secret
        }
