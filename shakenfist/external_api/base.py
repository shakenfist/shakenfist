import flask
from flask_jwt_extended import get_jwt_identity
import json
import requests
from shakenfist_utilities import api as sf_api, logs


from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import exceptions
from shakenfist.etcd import ThreadLocalReadOnlyCache
from shakenfist.instance import Instance
from shakenfist import network
from shakenfist.upload import Upload
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def arg_is_instance_ref(func):
    # Method uses the instance from the db
    def wrapper(*args, **kwargs):
        with ThreadLocalReadOnlyCache():
            try:
                inst = Instance.from_db_by_ref(kwargs.get('instance_ref'),
                                               get_jwt_identity()[0])
            except exceptions.MultipleObjects as e:
                return sf_api.error(400, str(e))

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
            api_token = util_general.get_api_token(
                'http://%s:%d' % (placement['node'], config.API_PORT),
                namespace=get_jwt_identity()[0])
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=json.dumps(sf_api.flask_get_post_body()),
                headers={'Authorization': api_token,
                         'User-Agent': util_general.get_user_agent()})

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
            return sf_api.error(406, 'instance %s is not ready (%s)' % (i.uuid, i.state.value))

        return func(*args, **kwargs)
    return wrapper


def arg_is_network_ref(func):
    # Method uses the network from the db
    def wrapper(*args, **kwargs):
        with ThreadLocalReadOnlyCache():
            try:
                n = network.Network.from_db_by_ref(kwargs.get('network_ref'),
                                                   get_jwt_identity()[0])
            except exceptions.MultipleObjects as e:
                return sf_api.error(400, str(e))

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
            admin_token = util_general.get_api_token(
                'http://%s:%d' % (config.NETWORK_NODE_IP, config.API_PORT),
                namespace='system')
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                'http://%s:%d%s'
                % (config.NETWORK_NODE_IP, config.API_PORT,
                   flask.request.environ['PATH_INFO']),
                data=flask.request.data,
                headers={'Authorization': admin_token,
                         'User-Agent': util_general.get_user_agent()})

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


def requires_namespace_exist(func):
    def wrapper(*args, **kwargs):
        if kwargs.get('namespace'):
            if not db.get_namespace(kwargs['namespace']):
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
            api_token = util_general.get_api_token(
                'http://%s:%d' % (u.node, config.API_PORT),
                namespace=get_jwt_identity()[0])
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=flask.request.get_data(cache=False, as_text=False,
                                            parse_form_data=False),
                headers={'Authorization': api_token,
                         'User-Agent': util_general.get_user_agent()})

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
            admin_token = util_general.get_api_token(
                'http://%s:%d' % (config.EVENTLOG_NODE_IP, config.API_PORT),
                namespace='system')
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                'http://%s:%d%s'
                % (config.EVENTLOG_NODE_IP, config.API_PORT,
                   flask.request.environ['PATH_INFO']),
                data=flask.request.data,
                headers={'Authorization': admin_token,
                         'User-Agent': util_general.get_user_agent()})

            LOG.info('Returning proxied request: %d, %s'
                     % (r.status_code, r.text))
            resp = flask.Response(r.text, mimetype='application/json')
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper
