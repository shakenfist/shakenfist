import flask
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from flask_jwt_extended.exceptions import NoAuthorizationError
import json
import requests
from shakenfist_utilities import api as sf_api, logs


from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist import exceptions
from shakenfist.instance import Instance
from shakenfist.namespace import Namespace, get_api_token
from shakenfist import network
from shakenfist.upload import Upload
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def caller_is_admin(func):
    # Ensure only users in the 'system' namespace can call this method
    def wrapper(*args, **kwargs):
        if get_jwt_identity()[0] != 'system':
            return sf_api.error(401, 'unauthorized')

        return func(*args, **kwargs)
    return wrapper


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
            ns_name, key_name = jwt_data['sub']
        except TypeError:
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
        namespace, keyname = get_jwt_identity()

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
        try:
            inst = Instance.from_db_by_ref(
                kwargs.get('instance_ref'), get_jwt_identity()[0])
        except exceptions.MultipleObjects as e:
            return sf_api.error(400, str(e), suppress_traceback=True)

        if not inst:
            LOG.with_fields({'instance': kwargs.get('instance_ref')}).info(
                'Instance not found, missing or deleted')
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

        if placement.get('node') != config.NODE_NAME:
            url = 'http://%s:%d%s' % (placement['node'], config.API_PORT,
                                      flask.request.environ['PATH_INFO'])
            api_token = get_api_token(
                'http://%s:%d' % (placement['node'], config.API_PORT),
                namespace=get_jwt_identity()[0])
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=json.dumps(sf_api.flask_get_post_body()),
                headers={
                    'Authorization': api_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.info('Proxied %s %s returns: %d, %s' % (
                     flask.request.environ['REQUEST_METHOD'], url,
                     r.status_code, r.text))
            resp = flask.Response(r.text,
                                  mimetype='application/json')
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
        if get_jwt_identity()[0] not in [i.namespace, 'system']:
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
        try:
            n = network.Network.from_db_by_ref(
                kwargs.get('network_ref'), get_jwt_identity()[0])
        except exceptions.MultipleObjects as e:
            return sf_api.error(400, str(e), suppress_traceback=True)

        if not n:
            LOG.with_fields({'network': kwargs.get('network_ref')}).info(
                'Network not found, missing or deleted')
            return sf_api.error(404, 'network not found')

        kwargs['network_from_db'] = n
        return func(*args, **kwargs)
    return wrapper


def redirect_to_network_node(func):
    # Redirect method to the network node
    def wrapper(*args, **kwargs):
        if not config.NODE_IS_NETWORK_NODE:
            admin_token = get_api_token(
                'http://%s:%d' % (config.NETWORK_NODE_IP, config.API_PORT),
                namespace='system')
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                'http://%s:%d%s'
                % (config.NETWORK_NODE_IP, config.API_PORT,
                   flask.request.environ['PATH_INFO']),
                data=flask.request.data,
                headers={
                    'Authorization': admin_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.info('Returning proxied request: %d, %s'
                     % (r.status_code, r.text))
            resp = flask.Response(r.text, mimetype='application/json')
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

        if get_jwt_identity()[0] not in [kwargs['network_from_db'].namespace, 'system']:
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
            url = 'http://%s:%d%s' % (u.node, config.API_PORT,
                                      flask.request.environ['PATH_INFO'])
            api_token = get_api_token(
                'http://%s:%d' % (u.node, config.API_PORT),
                namespace=get_jwt_identity()[0])
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=flask.request.get_data(cache=False, as_text=False,
                                            parse_form_data=False),
                headers={
                    'Authorization': api_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.info('Proxied %s %s returns: %d, %s' % (
                     flask.request.environ['REQUEST_METHOD'], url,
                     r.status_code, r.text))
            resp = flask.Response(r.text,  mimetype='application/json')
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def redirect_to_eventlog_node(func):
    # Redirect method to the event node
    def wrapper(*args, **kwargs):
        if not config.NODE_IS_EVENTLOG_NODE:
            admin_token = get_api_token(
                'http://%s:%d' % (config.EVENTLOG_NODE_IP, config.API_PORT),
                namespace='system')
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                'http://%s:%d%s'
                % (config.EVENTLOG_NODE_IP, config.API_PORT,
                   flask.request.environ['PATH_INFO']),
                data=flask.request.data,
                headers={
                    'Authorization': admin_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.info('Returning proxied request: %d, %s'
                     % (r.status_code, r.text))
            resp = flask.Response(r.text, mimetype='application/json')
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper
