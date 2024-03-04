# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: no
#   - API reference docs exist:
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:

from flasgger import swag_from
from shakenfist_utilities import api as sf_api, logs

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist.tasks import (
    DefloatNetworkInterfaceTask,
    FloatNetworkInterfaceTask)


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


interface_get_example = """{
    "floating": null,
    "instance_uuid": "d512e9f5-98d6-4c36-8520-33b6fc6de15f",
    "ipv4": "10.0.0.6",
    "macaddr": "02:00:00:73:18:66",
    "metadata": {},
    "model": "virtio",
    "network_uuid": "6aaaf243-0406-41a1-aa13-5d79a0b8672d",
    "order": 0,
    "state": "created",
    "uuid": "b1981e81-b37a-4176-ba37-b61bc7208012",
    "version": 3
}"""


class InterfaceEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'interfaces', 'Fetch details for an interface.',
        [('interface_uuid', 'body', 'uuid', 'The interface to fetch details for.', True)],
        [(200, 'Interface details.', interface_get_example),
         (404, 'Interface not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.redirect_to_network_node
    @api_base.log_token_use
    def get(self, interface_uuid=None):
        ni, _, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err
        return ni.external_view()


class InterfaceFloatEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'interfaces', 'Float (make publicly available via a floating IP) an interface.',
        [('interface_uuid', 'body', 'uuid', 'The interface to float.', True)],
        [(200, 'Interface float requested.', None),
         (404, 'Interface not found.', None),
         (507, 'Network congested and unable to allocate address.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def post(self, interface_uuid=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err

        try:
            api_util.assign_floating_ip(ni)
        except exceptions.CongestedNetwork as e:
            return sf_api.error(507, str(e), suppress_traceback=True)

        ni.add_event(EVENT_TYPE_AUDIT, 'float request from REST API')
        etcd.enqueue('networknode', FloatNetworkInterfaceTask(n.uuid, interface_uuid))


class InterfaceDefloatEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'interfaces', 'Defloat an interface.',
        [('interface_uuid', 'body', 'uuid', 'The interface to defloat.', True)],
        [(200, 'Interface defloat requested.', None),
         (404, 'Interface not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def post(self, interface_uuid=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err

        # Address is freed as part of the job, so code is "unbalanced" compared
        # to above for reasons.
        ni.add_event(EVENT_TYPE_AUDIT, 'defloat request from REST API')
        etcd.enqueue('networknode', DefloatNetworkInterfaceTask(n.uuid, interface_uuid))


class InterfaceMetadatasEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'interfaces', 'Fetch metadata for an interface.',
        [('interface_uuid', 'query', 'uuid', 'The interface to add a key to.', True)],
        [(200, 'Interface metadata, if any.', None),
         (404, 'Interface not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, interface_uuid=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err
        return ni.metadata

    @swag_from(api_base.swagger_helper(
        'interfaces', 'Add metadata for an interface.',
        [
            ('interface_uuid', 'query', 'uuid', 'The interface to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Interface not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def post(self, interface_uuid=None, key=None, value=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        ni.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'post'})
        ni.add_metadata_key(key, value)


class InterfaceMetadataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'interfaces', 'Update a metadata key for an interface.',
        [
            ('interface_uuid', 'query', 'uuid', 'The interface to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Interface not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def put(self, interface_uuid=None, key=None, value=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        ni.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'put'})
        ni.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'interfaces', 'Delete a metadata key for an interface.',
        [
            ('interface_uuid', 'query', 'uuid', 'The interface to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Interface not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.log_token_use
    def delete(self, interface_uuid=None, key=None, value=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err
        if not key:
            return sf_api.error(400, 'no key specified')
        ni.add_event(
            EVENT_TYPE_AUDIT, 'delete metadata key request from REST API',
            extra={'key': key})
        ni.remove_metadata_key(key)
