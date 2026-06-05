import json
import sys

import flask
import flask_restful
from flask_jwt_extended import decode_token
from flask_jwt_extended import unset_jwt_cookies
from flask_jwt_extended import verify_jwt_in_request
from flask_jwt_extended.exceptions import CSRFError
from flask_jwt_extended.exceptions import FreshTokenRequired
from flask_jwt_extended.exceptions import InvalidHeaderError
from flask_jwt_extended.exceptions import JWTDecodeError
from flask_jwt_extended.exceptions import NoAuthorizationError
from flask_jwt_extended.exceptions import RevokedTokenError
from flask_jwt_extended.exceptions import WrongTokenError
from jwt.exceptions import PyJWTError
from jwt.exceptions import DecodeError
from jwt.exceptions import ExpiredSignatureError
import requests
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.instance import Instance
from shakenfist.namespace import get_api_token
from shakenfist.node import Node
from shakenfist.namespace import Namespace
from shakenfist.upload import Upload
from shakenfist.util.access_tokens import parse_jwt_identity
from shakenfist.util.access_tokens import request_namespace
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def caller_is_admin(func):
    # Ensure only users in the 'system' namespace can call this method
    def wrapper(*args, **kwargs):
        if request_namespace() != 'system':
            return sf_api.error(401, 'unauthorized')

        return func(*args, **kwargs)
    return wrapper


def resolve_lookup_namespace(body_namespace, kind):
    # Decide which namespace a `*_ref` lookup should be scoped to.
    #
    # Why: `from_db_by_ref(name, namespace='system')` is a documented
    # "search everywhere" mode used for unqualified system lookups. The
    # decorators historically passed `request_namespace()` straight in,
    # which meant a system caller asking for a specific namespace via
    # the request body silently got cross-namespace resolution and
    # could find a same-named object in an unrelated namespace.
    #
    # Returns (lookup_namespace, error_response_or_None). A non-system
    # caller may not query a namespace other than their own — match
    # the 404 posture used by the requires_*_ownership decorators
    # rather than 403 to avoid leaking which namespaces exist.
    caller_ns = request_namespace()
    if body_namespace:
        if caller_ns != 'system' and body_namespace != caller_ns:
            return None, sf_api.error(404, f'{kind} not found')
        return body_namespace, None
    return caller_ns, None


# https://swagger.io/specification/v2/ defines the schema for this dictionary
def swagger_helper(section, description, parameters, responses,
                   requires_admin=False, requires_auth=True):
    out = {
        'tags': [section],
        'parameters': [],
        'consumes': [
            'application/json'
        ],
        'produces': [
            'application/json'
        ],
        'security': {
            'bearerAuth': []
        },
        'deprecated': False,
        'description': description,
        'responses': {}
    }

    # Type MUST be one of "string", "number", "integer", "boolean", "array" or "file".
    argtypes = {
        'arrayofdict': {'type': 'string', 'format': 'an array of JSON dictionaries'},
        'arrayofstring': {'type': 'string', 'format': 'an array of strings'},
        'bearer': {'type': 'string', 'format': 'Bearer ...JWT...'},
        'binary': {'type': 'string', 'format': 'Binary data'},
        'boolean': {'type': 'boolean', 'format': 'boolean'},
        'dict': {'type': 'string', 'format': 'a JSON dictionary'},
        'integer': {'type': 'integer', 'type': 'integer'},
        'ipv4': {'type': 'string', 'format': 'an IPv4 address as a string'},
        'namespace': {'type': 'string', 'format': 'the name of a namespace'},
        'node': {'type': 'string', 'format': 'the name of a node'},
        'string': {'type': 'string', 'format': 'string'},
        'url': {'type': 'string', 'format': 'url'},
        'uuid': {'type': 'string', 'format': 'uuid'},
        'uuidorname': {
            'type': 'string',
            'format': 'either a valid UUID or the unique name of an object'
            }
    }

    if requires_auth:
        out['parameters'].append({
            'name': 'Authorization',
            'in': 'header',
            'required': True,
            'description': 'JWT authorization header'
        })
        out['parameters'][-1].update(argtypes['bearer'])

    for (name, location, argtype, argdescription, argrequired) in parameters:
        out['parameters'].append({
            'name': name,
            'in': location,
            'required': argrequired,
            'description': argdescription
        })
        out['parameters'][-1].update(argtypes[argtype])

    if requires_auth:
        responses.append((
            401,
            'You must authenticate. See '
            'https://shakenfist.com/developer_guide/authentication/ for details.',
            None))
    for (httpcode, respdescription, sample) in responses:
        out['responses'][httpcode] = {
            'description': respdescription
        }
        if sample:
            out['responses'][httpcode]['examples'] = {
                'application/json': sample
            }

    constraints = []
    if requires_admin:
        constraints.append(
            'Requires authentication as a member of the system namespace.')

    if constraints:
        out['description'] += \
            '<br/><br/><i>%s</i>' % '<br/>'.join(constraints)

    return out


def verify_token(func):
    def wrapper(*args, **kwargs):
        # Ensure there is a valid JWT with a correct signature
        _, jwt_data = verify_jwt_in_request(
            False, False, False, ['headers'], True)

        # Perform SF specific safety checks
        try:
            ns_name, key_name = parse_jwt_identity()
        except (TypeError, ValueError):
            LOG.error('JWT token does not contain a namespace and key name in '
                      'the subject field')
            raise NoAuthorizationError()

        ns = Namespace.from_db(ns_name)
        if not ns:
            LOG.with_fields({'namespace', ns_name}).error(
                'JWT token is for non-existent namespace')
            raise NoAuthorizationError()
        if ns.state.value == dbo.STATE_DELETED:
            LOG.with_fields({'namespace', ns_name}).error(
                'JWT token is for deleted namespace')
            raise NoAuthorizationError()

        if key_name != '_service_key':
            keys = ns.keys.get('nonced_keys', {})
            if key_name not in keys:
                LOG.with_fields({'namespace', ns_name}).error(
                    'JWT token uses non-existent key')
                raise NoAuthorizationError()

            nonce = keys[key_name].get('nonce')
            if 'nonce' not in jwt_data:
                LOG.with_fields({'namespace', ns_name}).error(
                    'JWT token lacks nonce')
                raise NoAuthorizationError()
            if jwt_data['nonce'] != nonce:
                LOG.with_fields({'namespace', ns_name}).error(
                    'JWT token has incorrect nonce')
                raise NoAuthorizationError()

        return func(*args, **kwargs)
    return wrapper


def log_token_use(func):
    def wrapper(*args, **kwargs):
        auth_header = flask.request.headers.get('Authorization', 'Bearer none')
        token = auth_header.split(' ')[1]
        namespace, keyname = parse_jwt_identity()

        ns = Namespace.from_db(namespace)
        if not ns:
            return sf_api.error(401, 'authenticated namespace not known')
        ns.add_event(
            EVENT_TYPE_AUDIT, 'token used to authenticate request',
            extra={
                'token': token,
                'keyname': keyname,
                'method': flask.request.environ['REQUEST_METHOD'],
                'path': flask.request.environ['PATH_INFO'],
                'remote-address': flask.request.remote_addr
            })

        return func(*args, **kwargs)
    return wrapper


def arg_is_instance_ref(func):
    def wrapper(*args, **kwargs):
        body_namespace = kwargs.get('namespace')
        lookup_namespace, err = resolve_lookup_namespace(
            body_namespace, 'instance')
        if err:
            return err

        try:
            inst = Instance.from_db_by_ref(
                kwargs.get('instance_ref'), lookup_namespace)
        except exceptions.MultipleObjects as e:
            return sf_api.error(400, str(e), suppress_traceback=True)

        if not inst:
            LOG.with_fields({'instance': kwargs.get('instance_ref')}).info(
                'Instance not found, missing or deleted')
            return sf_api.error(404, 'instance not found')

        # UUID lookups bypass from_db_by_ref's namespace filter; if the
        # caller explicitly named a namespace, the resolved object must
        # live there or we reject it.
        if body_namespace and inst.namespace != body_namespace:
            LOG.with_fields({
                'instance': inst,
                'requested_namespace': body_namespace,
            }).info('Instance not in requested namespace')
            return sf_api.error(404, 'instance not found')

        kwargs['instance_from_db'] = inst
        return func(*args, **kwargs)
    return wrapper


def redirect_instance_request(func):
    # Redirect method to the hypervisor hosting the instance
    def wrapper(*args, **kwargs):
        i = kwargs.get('instance_from_db')
        if not i:
            return

        placement = i.placement
        if not placement:
            return
        if not placement.get('node'):
            return

        if not config.NODE_UUID:
            LOG.warning(
                'NODE_UUID is not set, cannot determine if '
                'request should be proxied')
            return sf_api.error(
                503, 'node UUID not resolved, cannot route request')

        if placement.get('node') != config.NODE_UUID:
            target_node = Node.from_db(placement['node'])
            if not target_node:
                return sf_api.error(404, 'placement node not found')
            target_ip = target_node.ip
            path = flask.request.environ['PATH_INFO']
            url = f'http://{target_ip}:13000{path}'
            api_token = get_api_token(
                f'http://{target_ip}:13000',
                namespace=request_namespace())
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=json.dumps(sf_api.flask_get_post_body()),
                headers={
                    'Authorization': api_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.with_fields({
                'method': flask.request.environ['REQUEST_METHOD'],
                'url': url,
                'status_code': r.status_code,
                'body_bytes': len(r.content)
            }).info('Returning proxied request')
            resp = flask.Response(
                r.content,
                mimetype=r.headers.get('Content-Type', 'application/json'))
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def requires_instance_ownership(func):
    # Requires that @arg_is_instance_ref has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('instance_from_db'):
            LOG.with_fields({'instance': kwargs['instance_ref']}).info(
                'Instance not found, kwarg missing')
            return sf_api.error(404, 'instance not found')

        i = kwargs['instance_from_db']
        if request_namespace() not in [i.namespace, 'system']:
            LOG.with_fields({'instance': i}).info(
                'Instance not found, ownership test in decorator')
            return sf_api.error(404, 'instance not found')

        return func(*args, **kwargs)
    return wrapper


def requires_instance_active(func):
    # Requires that @arg_is_instance_ref has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('instance_from_db'):
            LOG.with_fields({'instance': kwargs['instance_ref']}).info(
                'Instance not found, kwarg missing')
            return sf_api.error(404, 'instance not found')

        i = kwargs['instance_from_db']
        if i.state.value != Instance.STATE_CREATED:
            LOG.with_fields({'instance': i}).info(
                'Instance not ready (%s)' % i.state.value)
            return sf_api.error(406, f'instance {i.uuid} is not ready ({i.state.value})')

        return func(*args, **kwargs)
    return wrapper


def arg_is_network_ref(func):
    # Method uses the network from the db
    def wrapper(*args, **kwargs):
        body_namespace = kwargs.get('namespace')
        lookup_namespace, err = resolve_lookup_namespace(
            body_namespace, 'network')
        if err:
            return err

        try:
            n = network.Network.from_db_by_ref(
                kwargs.get('network_ref'), lookup_namespace)
        except exceptions.MultipleObjects as e:
            return sf_api.error(400, str(e), suppress_traceback=True)

        if not n:
            LOG.with_fields({'network': kwargs.get('network_ref')}).info(
                'Network not found, missing or deleted')
            return sf_api.error(404, 'network not found')

        # UUID lookups bypass from_db_by_ref's namespace filter; if the
        # caller explicitly named a namespace, the resolved object must
        # live there or we reject it. The floating network has
        # namespace=None and is therefore never "in" any namespace from
        # this check's perspective — admins must omit `namespace` to
        # reach it.
        if body_namespace and n.namespace != body_namespace:
            LOG.with_fields({
                'network': n,
                'requested_namespace': body_namespace,
            }).info('Network not in requested namespace')
            return sf_api.error(404, 'network not found')

        kwargs['network_from_db'] = n
        return func(*args, **kwargs)
    return wrapper


def redirect_to_network_node(func):
    # Redirect method to the network node
    def wrapper(*args, **kwargs):
        if not config.NODE_IS_NETWORK_NODE:
            path = flask.request.environ['PATH_INFO']
            admin_token = get_api_token(
                f'http://{config.NETWORK_NODE_IP}:13000', namespace='system')
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                f'http://{config.NETWORK_NODE_IP}:13000{path}',
                data=flask.request.data,
                headers={
                    'Authorization': admin_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.with_fields({
                'method': flask.request.environ['REQUEST_METHOD'],
                'url': path,
                'status_code': r.status_code,
                'body_bytes': len(r.content)
            }).info('Returning proxied request')
            resp = flask.Response(
                r.content,
                mimetype=r.headers.get('Content-Type', 'application/json'))
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def requires_network_ownership(func):
    # Requires that @arg_is_network_ref has already run
    def wrapper(*args, **kwargs):
        log = LOG.with_fields({'network': kwargs['network_ref']})

        if not kwargs.get('network_from_db'):
            log.info('Network not found, kwarg missing')
            return sf_api.error(404, 'network not found')

        if request_namespace() not in [kwargs['network_from_db'].namespace, 'system']:
            log.info('Network not found, ownership test in decorator')
            return sf_api.error(404, 'network not found')

        return func(*args, **kwargs)
    return wrapper


def requires_network_active(func):
    # Requires that @arg_is_network_ref has already run
    def wrapper(*args, **kwargs):
        log = LOG.with_fields({'network': kwargs['network_ref']})

        if not kwargs.get('network_from_db'):
            log.info('Network not found, kwarg missing')
            return sf_api.error(404, 'network not found')

        state = kwargs['network_from_db'].state
        if state.value != dbo.STATE_CREATED:
            log.info('Network not ready (%s)' % state.value)
            return sf_api.error(406,
                                'network %s is not ready (%s)'
                                % (kwargs['network_from_db'].uuid, state.value))

        return func(*args, **kwargs)
    return wrapper


def requires_namespace_exist_if_specified(func):
    def wrapper(*args, **kwargs):
        if kwargs.get('namespace'):
            if not Namespace.from_db(kwargs['namespace']):
                LOG.with_fields({'namespace': kwargs['namespace']}).warning(
                    'Attempt to use non-existent namespace')
                return sf_api.error(404, 'namespace not found')

        return func(*args, **kwargs)
    return wrapper


def arg_is_upload_uuid(func):
    # Method uses the upload from the db
    def wrapper(*args, **kwargs):
        if 'upload_uuid' in kwargs:
            kwargs['upload_from_db'] = Upload.from_db(
                kwargs['upload_uuid'])
        if not kwargs.get('upload_from_db'):
            LOG.with_fields({'upload': kwargs['upload_uuid']}).info(
                'Upload not found, genuinely missing')
            return sf_api.error(404, 'upload not found')

        return func(*args, **kwargs)
    return wrapper


def redirect_upload_request(func):
    # Redirect method to the hypervisor hosting the upload
    def wrapper(*args, **kwargs):
        u = kwargs.get('upload_from_db')
        if not u:
            return

        if not u.node:
            return

        if u.node != config.NODE_NAME:
            path = flask.request.environ['PATH_INFO']
            url = f'http://{u.node}:13000{path}'
            api_token = get_api_token(
                f'http://{u.node}:13000', namespace=request_namespace())
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=flask.request.get_data(cache=False, as_text=False,
                                            parse_form_data=False),
                headers={
                    'Authorization': api_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.with_fields({
                'method': flask.request.environ['REQUEST_METHOD'],
                'url': url,
                'status_code': r.status_code,
                'body_bytes': len(r.content)
            }).info('Returning proxied request')
            resp = flask.Response(
                r.content,
                mimetype=r.headers.get('Content-Type', 'application/json'))
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def log_request(func):
    def wrapper(*args, **kwargs):
        j = sf_api.flask_get_post_body()

        if j:
            for key in j:
                if key == 'uuid':
                    destkey = 'passed_uuid'
                else:
                    destkey = key
                kwargs[destkey] = j[key]

        formatted_headers = []
        for header in flask.request.headers:
            formatted_headers.append(str(header))

        # Ensure key does not appear in logs
        kwargs_log = kwargs.copy()
        if 'key' in kwargs_log:
            kwargs_log['key'] = '*****'

        # Redact a password if any
        if 'password' in kwargs_log:
            kwargs_log['password'] = '*****'

        # Redact the JWT auth token in headers as well
        headers_log = dict(flask.request.headers)
        if 'Authorization' in headers_log:
            headers_log = 'Bearer *****'

        # Attempt to lookup the identity from JWT token. This doesn't use
        # the usual get_jwt_identity() because that requires that the
        # require_jwt() decorator has been run, and that is not the case
        # for all paths this wrapper covers. Its ok for there to be no
        # identity here, for example unprotected paths.
        identity = None
        try:
            auth = flask.request.headers.get('Authorization')
            if auth:
                token = auth.split(' ')[1]
                dt = decode_token(token)
                identity = dt.get('identity')
        except Exception:
            pass

        log = LOG.with_fields({
            'request-id': flask.request.environ.get('FLASK_REQUEST_ID', 'none'),
            'identity': identity,
            'method': flask.request.method,
            'url': flask.request.url,
            'path': flask.request.path,
            'args': args,
            'kwargs': kwargs_log,
            'headers': headers_log
        })
        if flask.request.path == '/':
            # This is likely a load balancer health check
            log.debug('API request parsed')
        else:
            log.info('API request parsed')

        return func(*args, **kwargs)

    return wrapper


def handle_authorization_exceptions(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)

        except TypeError as e:
            return sf_api.error(400, str(e), suppress_traceback=False)

        except DecodeError:
            # Send a more informative message than 'Not enough segments'. If this
            # is a web browser, redirect them back to the root URL. Otherwise just
            # return a 401.
            if flask.request.headers.get('Accept', 'text/html').find('text/html') != -1:
                resp = flask.redirect('/', code=302)
                unset_jwt_cookies(resp)
                return resp
            return sf_api.error(401, 'invalid JWT in Authorization header',
                                suppress_traceback=True)

        except ExpiredSignatureError as e:
            # The JWT looked valid, except it has expired. If this is a web
            # browser, redirect them back to the root URL. Otherwise just return
            # a 401.
            if flask.request.headers.get('Accept', 'text/html').find('text/html') != -1:
                resp = flask.redirect('/', code=302)
                unset_jwt_cookies(resp)
                return resp
            return sf_api.error(401, str(e), suppress_traceback=True)

        except (JWTDecodeError,
                NoAuthorizationError,
                InvalidHeaderError,
                WrongTokenError,
                RevokedTokenError,
                FreshTokenRequired,
                CSRFError,
                PyJWTError,
                ) as e:
            return sf_api.error(401, str(e), suppress_traceback=True)

    return wrapper


def record_exception(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            util_exceptions.record_exception(*sys.exc_info())
            raise e

    return wrapper


def suppress_exceptions_to_client(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            LOG.exception('Server error')
            return sf_api.error(500, 'server error: %s' % repr(e),
                                suppress_traceback=True)

    return wrapper


def object_events_response(object_type, object_uuid, limit, event_type):
    """Build the per-object events REST response.

    Shared by the /{instance,artifact,network,node,blob}/<u>/events
    endpoints: each handler does authn / authz / object lookup, then
    delegates the read-and-shape step here so the wire-format change
    only happens in one place.
    """
    return [
        row.model_dump(mode='json')
        for row in mariadb.get_object_events(
            object_type, object_uuid,
            limit=limit, event_type=event_type)
    ]


class Resource(flask_restful.Resource):
    # Remember that order here matters, the record_exception
    # wrapper deliberately reraises the exception so that
    # generic_wrapper can handle the response after logging.
    method_decorators = [
        log_request,
        handle_authorization_exceptions,
        record_exception,
        suppress_exceptions_to_client
        ]
