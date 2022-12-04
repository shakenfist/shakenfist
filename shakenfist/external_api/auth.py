import base64
import bcrypt
import datetime
import flask
from flask_jwt_extended import create_access_token, get_jwt_identity
from shakenfist_utilities import api as sf_api, logs

from shakenfist import artifact
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    active_states_filter)
from shakenfist.config import config
from shakenfist import db
from shakenfist.daemons import daemon
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist import instance
from shakenfist.namespace import Namespace, Namespaces, namespace_is_trusted
from shakenfist import network


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def _create_token(ns, keyname, nonce):
    token = create_access_token(
        identity=[ns.uuid, keyname],
        additional_claims={
            'iss': config.ZONE,
            'nonce': nonce
        },
        expires_delta=datetime.timedelta(minutes=config.API_TOKEN_DURATION))
    ns.add_event(
        'Token created from key',
        extra={
            'keyname': keyname,
            'nonce': nonce,
            'token': token
        })
    return {
        'access_token': token,
        'token_type': 'Bearer',
        'expires_in': config.API_TOKEN_DURATION * 60
    }


class AuthEndpoint(sf_api.Resource):
    @api_base.requires_namespace_exist
    def post(self, namespace=None, key=None):
        if not namespace:
            return sf_api.error(400, 'missing namespace in request')
        if not key:
            return sf_api.error(400, 'missing key in request')
        if not isinstance(key, str):
            # Must be a string to encode()
            return sf_api.error(400, 'key is not a string')

        ns = Namespace.from_db(namespace)
        if not ns:
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found during auth request')
            return sf_api.error(404, 'namespace not found during auth request')

        keys = ns.keys.get('nonced_keys', {})
        for keyname in keys:
            possible_key = base64.b64decode(keys[keyname]['key'])
            if bcrypt.checkpw(key.encode('utf-8'), possible_key):
                return _create_token(ns, keyname, keys[keyname]['nonce'])

        ns.add_event('Attempt to use incorrect namespace key')
        return sf_api.error(401, 'unauthorized')


class AuthNamespacesEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')

        ns = Namespace.new(namespace)

        # Log this special case of a token being used
        auth_header = flask.request.headers.get('Authorization', 'Bearer none')
        token = auth_header.split(' ')[1]
        invoking_namespace, keyname = get_jwt_identity()
        parent_ns = Namespace.from_db(invoking_namespace)
        if parent_ns:
            parent_ns.add_event(
                'Token used to create namespace %s' % namespace,
                extra={
                    'token': token,
                    'keyname': keyname,
                    'method': flask.request.environ['REQUEST_METHOD'],
                    'path': flask.request.environ['PATH_INFO'],
                    'remote-address': flask.request.remote_addr,
                    'created-namespace': namespace
                })
        ns.add_event(
            'Token used to create namespace',
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

        # Initialise metadata
        db.persist_metadata('namespace', namespace, {})

        return namespace

    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.log_token_use
    def get(self):
        retval = []
        for ns in Namespaces(filters=[active_states_filter]):
            retval.append(ns.external_view())
        return retval


class AuthNamespaceEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.log_token_use
    def delete(self, namespace):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')
        if namespace == 'system':
            return sf_api.error(403, 'you cannot delete the system namespace')

        # Namespace must exist
        ns = Namespace.from_db(namespace)
        if not ns:
            return sf_api.error(404, 'namespace not found')

        # The namespace must be empty
        instances = []
        deleted_instances = []
        for i in instance.instances_in_namespace(namespace):
            if i.state.value in [dbo.STATE_DELETED, dbo.STATE_ERROR]:
                deleted_instances.append(i.uuid)
            else:
                LOG.withFields({'instance': i.uuid,
                                'state': i.state}).info('Blocks namespace delete')
                instances.append(i.uuid)
        if len(instances) > 0:
            return sf_api.error(400, 'you cannot delete a namespace with instances')

        networks = []
        for n in network.networks_in_namespace(namespace):
            if not n.is_dead():
                LOG.withFields({'network': n.uuid,
                                'state': n.state}).info('Blocks namespace delete')
                networks.append(n.uuid)
        if len(networks) > 0:
            return sf_api.error(400, 'you cannot delete a namespace with networks')

        for a in artifact.artifacts_in_namespace(namespace):
            a.delete()

        ns.state = dbo.STATE_DELETED
        db.delete_metadata('namespace', namespace)


def _namespace_keys_putpost(namespace=None, key_name=None, key=None):
    if not namespace:
        return sf_api.error(400, 'no namespace specified')
    if not key_name:
        return sf_api.error(400, 'no key name specified')
    if not key:
        return sf_api.error(400, 'no key specified')
    if key_name == 'service_key':
        return sf_api.error(403, 'illegal key name')

    ns = Namespace.from_db(namespace)
    if not ns:
        return sf_api.error(404, 'namespace does not exist')

    ns.add_key(key_name, key)
    return key_name


class AuthNamespaceKeysEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def get(self, namespace=None):
        out = []
        ns = Namespace.from_db(namespace)
        for keyname in ns.get('nonced_keys', {}):
            out.append(keyname)
        return out

    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def post(self, namespace=None, key_name=None, key=None):
        return _namespace_keys_putpost(namespace, key_name, key)


class AuthNamespaceKeyEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def put(self, namespace=None, key_name=None, key=None):
        ns = Namespace.from_db(namespace)
        if key_name not in ns.keys:
            return sf_api.error(404, 'key does not exist')

        return _namespace_keys_putpost(namespace, key_name, key)

    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def delete(self, namespace, key_name):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')
        if not key_name:
            return sf_api.error(400, 'no key name specified')

        ns = Namespace.from_db(namespace)
        if key_name in ns.keys.get('nonced_keys', {}):
            ns.remove_key(key_name)
        else:
            return sf_api.error(404, 'key name not found in namespace')


class AuthMetadatasEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def get(self, namespace=None):
        md = db.get_metadata('namespace', namespace)
        if not md:
            return {}
        return md

    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def post(self, namespace=None, key=None, value=None):
        return api_util.metadata_putpost('namespace', namespace, key, value)


class AuthMetadataEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def put(self, namespace=None, key=None, value=None):
        return api_util.metadata_putpost('namespace', namespace, key, value)

    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def delete(self, namespace=None, key=None, value=None):
        if not key:
            return sf_api.error(400, 'no key specified')

        md = db.get_metadata('namespace', namespace)
        if md is None or key not in md:
            return sf_api.error(404, 'key not found')
        del md[key]
        db.persist_metadata('namespace', namespace, md)


class AuthNamespaceTrustsEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def post(self, namespace=None, external_namespace=None):
        if not namespace:
            return sf_api.error(400, 'missing namespace in request')
        if not external_namespace:
            return sf_api.error(400, 'no external namespace specified')

        if not namespace_is_trusted(namespace, get_jwt_identity()[0]):
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found, trust test failed')
            return sf_api.error(404, 'namespace not found')

        ns = Namespace.from_db(namespace)
        ns.add_trust(external_namespace)
        return ns.external_view()


class AuthNamespaceTrustEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.requires_namespace_exist
    @api_base.log_token_use
    def delete(self, namespace=None, external_namespace=None):
        if not namespace:
            return sf_api.error(400, 'missing namespace in request')
        if not external_namespace:
            return sf_api.error(400, 'no external namespace specified')

        if not namespace_is_trusted(namespace, get_jwt_identity()[0]):
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found, trust test failed')
            return sf_api.error(404, 'namespace not found')

        ns = Namespace.from_db(namespace)
        ns.remove_trust(external_namespace)
        return ns.external_view()
