import base64
import bcrypt
from flask_jwt_extended import create_access_token
from flask_jwt_extended import jwt_required

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import db
from shakenfist.daemons import daemon
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist import instance
from shakenfist import logutil
from shakenfist import network


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


class AuthEndpoint(api_base.Resource):
    @api_base.requires_namespace_exist
    def post(self, namespace=None, key=None):
        if not namespace:
            return api_base.error(400, 'missing namespace in request')
        if not key:
            return api_base.error(400, 'missing key in request')
        if not isinstance(key, str):
            # Must be a string to encode()
            return api_base.error(400, 'key is not a string')

        ns = db.get_namespace(namespace)
        if not ns:
            return api_base.error(401, 'unauthorized')
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

        return api_base.error(401, 'unauthorized')


class AuthNamespacesEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        if not namespace:
            return api_base.error(400, 'no namespace specified')

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
                    return api_base.error(400, 'no key specified')
                if not isinstance(key, str):
                    # Must be a string to encode()
                    return api_base.error(400, 'key is not a string')
                if key_name == 'service_key':
                    return api_base.error(403, 'illegal key name')

                encoded = str(base64.b64encode(bcrypt.hashpw(
                    key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
                rec['keys'][key_name] = encoded

            # Initialise metadata
            db.persist_metadata('namespace', namespace, {})
            db.persist_namespace(namespace, rec)

        return namespace

    @jwt_required()
    @api_base.caller_is_admin
    def get(self):
        out = []
        for rec in db.list_namespaces():
            out.append(rec['name'])
        return out


class AuthNamespaceEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    def delete(self, namespace):
        if not namespace:
            return api_base.error(400, 'no namespace specified')
        if namespace == 'system':
            return api_base.error(403, 'you cannot delete the system namespace')

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
            return api_base.error(400, 'you cannot delete a namespace with instances')

        networks = []
        for n in network.networks_in_namespace(namespace):
            if not n.is_dead():
                LOG.withFields({'network': n.uuid,
                                'state': n.state}).info('Blocks namespace delete')
                networks.append(n.uuid)
        if len(networks) > 0:
            return api_base.error(400, 'you cannot delete a namespace with networks')

        db.delete_namespace(namespace)
        db.delete_metadata('namespace', namespace)


def _namespace_keys_putpost(namespace=None, key_name=None, key=None):
    if not namespace:
        return api_base.error(400, 'no namespace specified')
    if not key_name:
        return api_base.error(400, 'no key name specified')
    if not key:
        return api_base.error(400, 'no key specified')
    if key_name == 'service_key':
        return api_base.error(403, 'illegal key name')

    with db.get_lock('namespace', None, 'all', op='Namespace key update'):
        rec = db.get_namespace(namespace)
        if not rec:
            return api_base.error(404, 'namespace does not exist')

        encoded = str(base64.b64encode(bcrypt.hashpw(
            key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
        rec['keys'][key_name] = encoded

        db.persist_namespace(namespace, rec)

    return key_name


class AuthNamespaceKeysEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    @api_base.requires_namespace_exist
    def get(self, namespace=None):
        out = []
        rec = db.get_namespace(namespace)
        for keyname in rec['keys']:
            out.append(keyname)
        return out

    @jwt_required()
    @api_base.caller_is_admin
    @api_base.requires_namespace_exist
    def post(self, namespace=None, key_name=None, key=None):
        return _namespace_keys_putpost(namespace, key_name, key)


class AuthNamespaceKeyEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    @api_base.requires_namespace_exist
    def put(self, namespace=None, key_name=None, key=None):
        rec = db.get_namespace(namespace)
        if key_name not in rec['keys']:
            return api_base.error(404, 'key does not exist')

        return _namespace_keys_putpost(namespace, key_name, key)

    @jwt_required()
    @api_base.caller_is_admin
    @api_base.requires_namespace_exist
    def delete(self, namespace, key_name):
        if not namespace:
            return api_base.error(400, 'no namespace specified')
        if not key_name:
            return api_base.error(400, 'no key name specified')

        with db.get_lock('namespace', None, namespace, op='Namespace key delete'):
            ns = db.get_namespace(namespace)
            if ns.get('keys') and key_name in ns['keys']:
                del ns['keys'][key_name]
            else:
                return api_base.error(404, 'key name not found in namespace')
            db.persist_namespace(namespace, ns)


class AuthMetadatasEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    @api_base.requires_namespace_exist
    def get(self, namespace=None):
        md = db.get_metadata('namespace', namespace)
        if not md:
            return {}
        return md

    @jwt_required()
    @api_base.caller_is_admin
    @api_base.requires_namespace_exist
    def post(self, namespace=None, key=None, value=None):
        return api_util.metadata_putpost('namespace', namespace, key, value)


class AuthMetadataEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    @api_base.requires_namespace_exist
    def put(self, namespace=None, key=None, value=None):
        return api_util.metadata_putpost('namespace', namespace, key, value)

    @jwt_required()
    @api_base.caller_is_admin
    @api_base.requires_namespace_exist
    def delete(self, namespace=None, key=None, value=None):
        if not key:
            return api_base.error(400, 'no key specified')

        with db.get_lock('metadata', 'namespace', namespace, op='Metadata delete'):
            md = db.get_metadata('namespace', namespace)
            if md is None or key not in md:
                return api_base.error(404, 'key not found')
            del md[key]
            db.persist_metadata('namespace', namespace, md)
