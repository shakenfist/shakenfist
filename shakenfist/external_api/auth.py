# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: both
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage: yes
import base64

import bcrypt
import flask
from flasgger import swag_from
from flask_jwt_extended import get_jwt_identity
from shakenfist_utilities import api as sf_api
from shakenfist_utilities import logs

from shakenfist import artifact
from shakenfist import instance
from shakenfist import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.namespace import Namespace
from shakenfist.namespace import namespace_is_trusted
from shakenfist.namespace import Namespaces
from shakenfist.util import access_tokens


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
        if not namespace_is_trusted(ns, get_jwt_identity()[0]):
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


class AuthEndpoint(sf_api.Resource):
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

        keys = namespace_from_db.keys.get('nonced_keys', {})
        for keyname in keys:
            possible_key = base64.b64decode(keys[keyname]['key'])
            try:
                if bcrypt.checkpw(key.encode('utf-8'), possible_key):
                    return access_tokens.create_token(
                        namespace_from_db, keyname, keys[keyname]['nonce'])
            except ValueError as e:
                namespace_from_db.add_event(
                    EVENT_TYPE_AUDIT, 'namespace key is invalid',
                    extra={
                        'error': str(e),
                        'key_name': keyname,
                        'key-body': keys[keyname]
                    })

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'attempt to use incorrect namespace key')
        return sf_api.error(401, 'unauthorized')


namespace_get_example = """{
    "name": "system",
    "key_names": [
        "deploy"
    ],
    "metadata": {}
}"""


namespace_list_example = """[
    ...,
    {
        "name": "system",
        "key_names": [
            "deploy"
        ],
        "metadata": {}
    }
]"""


class AuthNamespacesEndpoint(sf_api.Resource):
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
    @api_base.verify_token
    @api_base.caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')

        if Namespace.from_db(namespace, suppress_failure_audit=True):
            return sf_api.error(403, 'namespace exists')

        ns = Namespace.new(namespace)
        ns.add_event(EVENT_TYPE_AUDIT, 'creation request from REST API')

        # Log this special case of a token being used
        auth_header = flask.request.headers.get('Authorization', 'Bearer none')
        token = auth_header.split(' ')[1]
        invoking_namespace, keyname = get_jwt_identity()
        parent_ns = Namespace.from_db(invoking_namespace)
        if parent_ns:
            parent_ns.add_event(
                EVENT_TYPE_AUDIT, 'token used to create namespace %s' % namespace,
                extra={
                    'token': token,
                    'keyname': keyname,
                    'method': flask.request.environ['REQUEST_METHOD'],
                    'path': flask.request.environ['PATH_INFO'],
                    'remote-address': flask.request.remote_addr,
                    'created-namespace': namespace
                })
        ns.add_event(
            EVENT_TYPE_AUDIT, 'token used to create namespace',
            extra={
                'token': token,
                'keyname': keyname,
                'method': flask.request.environ['REQUEST_METHOD'],
                'path': flask.request.environ['PATH_INFO'],
                'remote-address': flask.request.remote_addr
            })

        # Allow shortcut of creating key at same time as the namespace
        if key_name:
            if not key:
                return sf_api.error(400, 'no key specified')
            if not isinstance(key, str):
                # Must be a string to encode()
                return sf_api.error(400, 'key is not a string')
            if key_name == 'service_key':
                return sf_api.error(403, 'illegal key name')

            ns.add_key(key_name, key)

        return ns.external_view()

    @swag_from(api_base.swagger_helper(
        'auth', 'List all namespaces visible to this namespace.', [],
        [(200, 'The namespace as created.', namespace_list_example)]))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self):
        retval = []
        for ns in Namespaces(filters=[], prefilter='active'):
            if namespace_is_trusted(ns.uuid, get_jwt_identity()[0]):
                retval.append(ns.external_view())
        return retval


class AuthNamespaceEndpoint(sf_api.Resource):
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
    @api_base.verify_token
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
    @api_base.verify_token
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.log_token_use
    def get(self, namespace=None, namespace_from_db=None):
        return namespace_from_db.external_view()


def _namespace_keys_putpost(ns=None, key_name=None, key=None):
    if not key_name:
        return sf_api.error(400, 'no key name specified')
    if not key:
        return sf_api.error(400, 'no key specified')
    if key_name.startswith('_service_key'):
        return sf_api.error(403, 'illegal key name')

    ns.add_key(key_name, key)
    return key_name


class AuthNamespaceKeysEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Get the authentication keys for a namespace.',
        [
            ('namespace', 'body', 'string',
             'The namespace to fetch authentication keys for.', True)
        ],
        [(200, 'A list of keynames for the namespace.', '["deploy", ...]'),
         (404, 'Namespace not found.', None)]))
    @api_base.verify_token
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
            ('key', 'body', 'string', 'The authentication key.', True)
        ],
        [(200, 'The name of the created key.', 'newkey'),
         (404, 'Namespace not found.', None)]))
    @api_base.verify_token
    @requires_namespace_ownership
    @arg_is_namespace
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def post(self, namespace=None, key_name=None, key=None, namespace_from_db=None):
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'create auth key request from REST API',
            extra={'key': key_name})
        return _namespace_keys_putpost(namespace_from_db, key_name, key)


class AuthNamespaceKeyEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Update an authentication key for a namespace.',
        [
            ('namespace', 'body', 'string', 'The namespace to add a key to.', True),
            ('key_name', 'body', 'string', 'The name of the key.', True),
            ('key', 'body', 'string', 'The authentication key.', True)
        ],
        [(200, 'The name of the updated key.', 'newkey'),
         (404, 'Namespace or key not found.', None)]))
    @api_base.verify_token
    @api_base.caller_is_admin
    @requires_namespace_ownership
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def put(self, namespace=None, key_name=None, key=None, namespace_from_db=None):
        if key_name not in namespace_from_db.keys:
            return sf_api.error(404, 'key does not exist')
        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'update auth key request from REST API',
            extra={'key': key_name})
        return _namespace_keys_putpost(namespace, key_name, key)

    @swag_from(api_base.swagger_helper(
        'auth', 'Update an authentication key for a namespace.',
        [
            ('namespace', 'body', 'string', 'The namespace to add a key to.', True),
            ('key_name', 'body', 'string', 'The name of the key.', True),
            ('key', 'body', 'string', 'The authentication key.', True)
        ],
        [(200, 'The name of the updated key.', 'newkey'),
         (404, 'Namespace or key not found.', None)]))
    @api_base.verify_token
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


class AuthMetadatasEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Fetch metadata for a namespace.',
        [
            ('namespace', 'query', 'string', 'The namespace to fetch metadata for.', True)
        ],
        [(200, 'Namespace metadata, if any.', None),
         (404, 'Namespace not found.', None)]))
    @api_base.verify_token
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
    @api_base.verify_token
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


class AuthMetadataEndpoint(sf_api.Resource):
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
    @api_base.verify_token
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
    @api_base.verify_token
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


class AuthNamespaceTrustsEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'auth', 'Trust an external namespace.',
        [
            ('namespace', 'query', 'string', 'The namespace to trust.', True)
        ],
        [(200, 'The current state of the namespace.', namespace_get_example),
         (400, 'No external namespace specified.', None),
         (404, 'Namespace not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_namespace
    @api_base.log_token_use
    def post(self, namespace=None, external_namespace=None, namespace_from_db=None):
        if not external_namespace:
            return sf_api.error(400, 'no external namespace specified')

        if not namespace_is_trusted(namespace, get_jwt_identity()[0]):
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found, trust test failed')
            return sf_api.error(404, 'namespace not found')

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'add trust request from REST API')
        namespace_from_db.add_trust(external_namespace)
        return namespace_from_db.external_view()


class AuthNamespaceTrustEndpoint(sf_api.Resource):
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
    @api_base.verify_token
    @arg_is_namespace
    @api_base.log_token_use
    def delete(self, namespace=None, external_namespace=None, namespace_from_db=None):
        if not external_namespace:
            return sf_api.error(400, 'no external namespace specified')

        if not namespace_is_trusted(namespace, get_jwt_identity()[0]):
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found, trust test failed')
            return sf_api.error(404, 'namespace not found')

        namespace_from_db.add_event(
            EVENT_TYPE_AUDIT, 'remove trust request from REST API')
        namespace_from_db.remove_trust(external_namespace)
        return namespace_from_db.external_view()
