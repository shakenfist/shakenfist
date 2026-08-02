# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: both
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage: yes
import base64
import time
from functools import partial

import bcrypt
import flask
from flasgger import swag_from
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder

from shakenfist import artifact
from shakenfist import baseobject
from shakenfist import instance
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.mapping_rule import MappingRule
from shakenfist.mapping_rule import MappingRules
from shakenfist.mapping_rule import RuleValidationError
from shakenfist.namespace import Namespace
from shakenfist.namespace import namespace_is_trusted
from shakenfist.namespace import Namespaces
from shakenfist.trusted_issuer import TrustedIssuer
from shakenfist.trusted_issuer import TrustedIssuers
from shakenfist.util import access_tokens
from shakenfist.util import credentials
from shakenfist.util.access_tokens import parse_jwt_identity
from shakenfist.util.access_tokens import request_namespace


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def arg_is_namespace(func):
    def wrapper(*args, **kwargs):
        if 'namespace' not in kwargs:
            return sf_api.error(400, 'missing namespace in request')

        ns = Namespace.from_db(kwargs.get('namespace'), suppress_failure_audit=True)
        if not ns:
            LOG.with_fields({'namespace': kwargs.get('namespace')}).info(
                'Namespace not found, missing or deleted')
            return sf_api.error(404, 'namespace not found')
        if ns.state.value == dbo.STATE_DELETED:
            LOG.with_fields({'namespace': kwargs.get('namespace')}).info(
                'Namespace is deleted')
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
    Both patterns are now rejected on both paths (phase 2 Decision 2
    of the auth federation plan). Returns an error response, or None
    if the name is acceptable.
    """
    if key_name == 'service_key' or key_name.startswith('_service_key'):
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
            possible_key = base64.b64decode(keys[keyname]['key'])
            try:
                if bcrypt.checkpw(key.encode('utf-8'), possible_key):
                    # One extra point read, and only on the successful
                    # match rather than per candidate key. The legacy
                    # accessor shape above is pinned by the phase 2
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
            ('namespace', 'body', 'string', 'The namespace to create.', True),
            ('key_name', 'body', 'string',
             'Name of an optional first key created at the same time.', False),
            ('key', 'body', 'string',
             'Secret for an optional first key created at the same time.', False)
        ],
        [(200, 'The namespace as created.', namespace_get_example),
         (400, 'No namespace specified, no key specified, or key is not a string.', None),
         (403, 'Illegal key name.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')

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
            ('namespace', 'body', 'string', 'The namespace to delete.', True)
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

        # The namespace must be empty
        instances = []
        deleted_instances = []
        for i in instance.instances_in_namespace(namespace):
            if i.state.value in [dbo.STATE_DELETED, dbo.STATE_ERROR]:
                deleted_instances.append(i.uuid)
            else:
                LOG.with_fields({'instance': i.uuid,
                                 'state': i.state}).info('Blocks namespace delete')
                instances.append(i.uuid)
        if len(instances) > 0:
            return sf_api.error(400, 'you cannot delete a namespace with instances')

        networks = []
        for n in network.networks_in_namespace(namespace):
            if not n.is_dead():
                LOG.with_fields({'network': n.uuid,
                                 'state': n.state}).info('Blocks namespace delete')
                networks.append(n.uuid)
        if len(networks) > 0:
            return sf_api.error(400, 'you cannot delete a namespace with networks')

        for a in artifact.artifacts_in_namespace(namespace):
            a.add_event(
                EVENT_TYPE_AUDIT, 'deletion request via namespace deletion from REST API')
            a.delete()

        namespace_from_db.state = dbo.STATE_DELETED
        namespace_from_db.add_event(EVENT_TYPE_AUDIT, 'deletion request from REST API')

    @swag_from(api_base.swagger_helper(
        'auth', 'Get namespace information.',
        [
            ('namespace', 'body', 'string', 'The namespace to get.', True)
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
    # route straight around Decision 3.
    #
    # None from caller_scopes() means the caller is unrestricted, which
    # is every operator holding a legacy key, so their keys keep being
    # created unscoped exactly as before.
    inherited = api_base.caller_scopes()

    ns.add_key(key_name, key, expiry=expiry, scopes=inherited)
    if generated:
        return {'key_name': key_name, 'key': key}
    return key_name


class AuthNamespaceKeysEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Get the authentication keys for a namespace.',
        [
            ('namespace', 'body', 'string',
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
            ('namespace', 'body', 'string', 'The namespace to add a key to.', True),
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
            ('namespace', 'body', 'string', 'The namespace to add a key to.', True),
            ('key_name', 'body', 'string', 'The name of the key.', True),
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
        # worked. Fixed per phase 2 Decision 4 of the auth federation
        # plan; there is no client which could depend on the old
        # behaviour.
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
            ('namespace', 'body', 'string', 'The namespace to remove a key from.', True),
            ('key_name', 'body', 'string', 'The name of the key.', True)
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
            ('namespace', 'query', 'string', 'The namespace to fetch metadata for.', True)
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
            ('namespace', 'query', 'string', 'The namespace to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
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
            ('namespace', 'query', 'string', 'The namespace to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
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
            ('namespace', 'query', 'string', 'The namespace to remove a key from.', True),
            ('key', 'query', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Namespace not found.', None)]))
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def delete(self, namespace=None, key=None, value=None, namespace_from_db=None):
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
            ('namespace', 'query', 'string', 'The namespace to trust.', True)
        ],
        [(200, 'The current state of the namespace.', namespace_get_example),
         (400, 'No external namespace specified.', None),
         (404, 'Namespace not found.', None)],
        requires_admin=True))
    @arg_is_namespace
    @api_base.log_token_use
    def post(self, namespace=None, external_namespace=None, namespace_from_db=None):
        if not external_namespace:
            return sf_api.error(400, 'no external namespace specified')

        if not namespace_is_trusted(namespace, request_namespace()):
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found, trust test failed')
            return sf_api.error(404, 'namespace not found')

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'add trust request from REST API')
        namespace_from_db.add_trust(external_namespace)
        return namespace_from_db.external_view()


class AuthNamespaceTrustEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Remove trust from an external namespace.',
        [
            ('namespace', 'query', 'string',
             'The namespace to alter.', True),
            ('external_namespace', 'query', 'string',
             'The namespace to no longer trust.', True)
        ],
        [(200, 'The current state of the namespace.', namespace_get_example),
         (400, 'No external namespace specified.', None),
         (404, 'Namespace not found.', None)],
        requires_admin=True))
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
    "issuer_url": "https://token.actions.githubusercontent.com",
    "jwks_uri": "https://token.actions.githubusercontent.com/.well-known/jwks",
    "name": "github",
    "state": "created",
    "uuid": "b2b3a04e-8f22-4a1e-8f8e-2f3b1f7a41ab",
    "version": 1
}"""


def _validate_issuer_arguments(issuer_url, jwks_uri, audience):
    """Check the configuration of a trusted issuer.

    Returns an error response, or None. Every field is required: an
    issuer missing its audience would accept tokens minted for someone
    else, and one missing its JWKS URI cannot verify a signature at
    all. There is no sensible default for any of them.
    """
    for name, value in (('issuer_url', issuer_url),
                        ('jwks_uri', jwks_uri),
                        ('audience', audience)):
        if not value:
            return sf_api.error(400, f'no {name} specified')
        if not isinstance(value, str):
            return sf_api.error(400, f'{name} is not a string')
        if len(value) > 1024:
            return sf_api.error(
                422, f'{name} cannot be longer than 1024 characters')

    # Signing keys must come from somewhere we control the choice of.
    # Refusing plaintext here is not paranoia: a JWKS fetched over HTTP
    # can be substituted by anyone on the path, which turns signature
    # verification into theatre.
    if not jwks_uri.startswith('https://'):
        return sf_api.error(400, 'jwks_uri must be https')
    return None


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
             'Where the issuer publishes its signing keys.', True),
            ('audience', 'body', 'string',
             'The value expected in a token\'s aud claim.', True)
        ],
        [(200, 'The issuer as created.', trusted_issuer_example),
         (400, 'A required field is missing or malformed.', None),
         (409, 'An issuer of that name already exists.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def post(self, name=None, issuer_url=None, jwks_uri=None, audience=None):
        if not name:
            return sf_api.error(400, 'no name specified')
        if not isinstance(name, str) or len(name) > 255:
            return sf_api.error(400, 'name is not a valid string')

        err = _validate_issuer_arguments(issuer_url, jwks_uri, audience)
        if err:
            return err

        issuer = TrustedIssuer.new(name, issuer_url, jwks_uri, audience)
        if not issuer:
            return sf_api.error(409, 'issuer already exists')
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
             'Where the issuer publishes its signing keys.', True),
            ('audience', 'body', 'string',
             'The value expected in a token\'s aud claim.', True)
        ],
        [(200, 'The updated issuer.', trusted_issuer_example),
         (400, 'A required field is missing or malformed.', None),
         (404, 'Issuer not found.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def put(self, issuer_name=None, issuer_url=None, jwks_uri=None,
            audience=None):
        issuer = TrustedIssuer.from_db_by_name(issuer_name)
        if not issuer:
            return sf_api.error(404, 'issuer not found')

        err = _validate_issuer_arguments(issuer_url, jwks_uri, audience)
        if err:
            return err

        issuer.update(issuer_url, jwks_uri, audience)
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
            ('bound_claims', 'body', 'dict',
             'Claim name to matcher. A matcher is an exact string, or a '
             'list of acceptable strings. Matching is exact: no globbing, '
             'no regular expressions, no prefix matching. At least one '
             'claim must be bound.', True),
            ('scopes', 'body', 'arrayofstring',
             'The scopes granted to keys minted through this rule. Must be '
             'non-empty.', True),
            ('key_ttl', 'body', 'integer',
             'Seconds of life for keys minted through this rule.', True),
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
            return sf_api.error(400, str(e))

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
            ('bound_claims', 'body', 'dict',
             'Claim name to matcher, as for creation.', True),
            ('scopes', 'body', 'arrayofstring',
             'The scopes granted to keys minted through this rule.', True),
            ('key_ttl', 'body', 'integer',
             'Seconds of life for keys minted through this rule.', True),
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
            return sf_api.error(400, str(e))

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
