import base64
import bcrypt
from flask_jwt_extended import create_access_token
from flask_jwt_extended import jwt_required
from shakenfist_utilities import api as sf_api, logs

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import db
from shakenfist.daemons import daemon
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist import instance
from shakenfist import network


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


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

        ns = db.get_namespace(namespace)
        if not ns:
            LOG.with_fields({'namespace': namespace}).info(
                'Namespace not found during auth request')
            return sf_api.error(404, 'namespace not found during auth request')
        service_key = ns.get('service_key')
        if service_key and key == service_key:
            return {
                'access_token': create_access_token(identity=[namespace, '_service_key'])
            }

        for key_name in ns.get('keys', {}):
            possible_key = base64.b64decode(ns['keys'][key_name])
            if bcrypt.checkpw(key.encode('utf-8'), possible_key):
                return {
                    'access_token': create_access_token(identity=[namespace, key_name])
                }

        LOG.with_fields({'namespace': namespace}).info(
            'Key not found during auth request')
        return sf_api.error(401, 'unauthorized')


class AuthNamespacesEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')

        with db.get_lock('namespace', None, 'all', op='Namespace update'):
            rec = db.get_namespace(namespace)
            if not rec:
                rec = {
                    'name': namespace,
                    'keys': {}
                }

            # Allow shortcut of creating key at same time as the namespace
            if key_name:
                if not key:
                    return sf_api.error(400, 'no key specified')
                if not isinstance(key, str):
                    # Must be a string to encode()
                    return sf_api.error(400, 'key is not a string')
                if key_name == 'service_key':
                    return sf_api.error(403, 'illegal key name')

                encoded = str(base64.b64encode(bcrypt.hashpw(
                    key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
                rec['keys'][key_name] = encoded

            # Initialise metadata
            db.persist_metadata('namespace', namespace, {})
            db.persist_namespace(namespace, rec)

        return namespace

    @jwt_required()
    @sf_api.caller_is_admin
    def get(self):
        out = []
        for rec in db.list_namespaces():
            out.append(rec['name'])
        return out


class AuthNamespaceEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    def delete(self, namespace):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')
        if namespace == 'system':
            return sf_api.error(403, 'you cannot delete the system namespace')

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

        db.delete_namespace(namespace)
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

    with db.get_lock('namespace', None, 'all', op='Namespace key update'):
        rec = db.get_namespace(namespace)
        if not rec:
            return sf_api.error(404, 'namespace does not exist')

        encoded = str(base64.b64encode(bcrypt.hashpw(
            key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
        rec['keys'][key_name] = encoded

        db.persist_namespace(namespace, rec)

    return key_name


class AuthNamespaceKeysEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    def get(self, namespace=None):
        out = []
        rec = db.get_namespace(namespace)
        for keyname in rec['keys']:
            out.append(keyname)
        return out

    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    def post(self, namespace=None, key_name=None, key=None):
        return _namespace_keys_putpost(namespace, key_name, key)


class AuthNamespaceKeyEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    def put(self, namespace=None, key_name=None, key=None):
        rec = db.get_namespace(namespace)
        if key_name not in rec['keys']:
            return sf_api.error(404, 'key does not exist')

        return _namespace_keys_putpost(namespace, key_name, key)

    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    def delete(self, namespace, key_name):
        if not namespace:
            return sf_api.error(400, 'no namespace specified')
        if not key_name:
            return sf_api.error(400, 'no key name specified')

        with db.get_lock('namespace', None, namespace, op='Namespace key delete'):
            ns = db.get_namespace(namespace)
            if ns.get('keys') and key_name in ns['keys']:
                del ns['keys'][key_name]
            else:
                return sf_api.error(404, 'key name not found in namespace')
            db.persist_namespace(namespace, ns)


class AuthMetadatasEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    def get(self, namespace=None):
        md = db.get_metadata('namespace', namespace)
        if not md:
            return {}
        return md

    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    def post(self, namespace=None, key=None, value=None):
        return api_util.metadata_putpost('namespace', namespace, key, value)


class AuthMetadataEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    def put(self, namespace=None, key=None, value=None):
        return api_util.metadata_putpost('namespace', namespace, key, value)

    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.requires_namespace_exist
    def delete(self, namespace=None, key=None, value=None):
        if not key:
            return sf_api.error(400, 'no key specified')

        with db.get_lock('metadata', 'namespace', namespace, op='Metadata delete'):
            md = db.get_metadata('namespace', namespace)
            if md is None or key not in md:
                return sf_api.error(404, 'key not found')
            del md[key]
            db.persist_metadata('namespace', namespace, md)
