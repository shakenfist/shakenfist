# Documentation state:
#   - Has metadata calls:
#   - OpenAPI complete:
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:

from flasgger import swag_from
from shakenfist_utilities import api as sf_api, logs

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


interface_get_example = """..."""


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
        etcd.enqueue('networknode', DefloatNetworkInterfaceTask(n.uuid, interface_uuid))


class InterfaceMetadatasEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'interfaces', 'Fetch metadata for an interface.',
        [('interface_uuid', 'body', 'uuid', 'The interface to add a key to.', True)],
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
            ('interface_uuid', 'body', 'uuid', 'The interface to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
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
        ni.add_metadata_key(key, value)


class InterfaceMetadataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'interfaces', 'Update a metadata key for an interface.',
        [
            ('interface_uuid', 'body', 'uuid', 'The interface to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
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
        ni.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'interfaces', 'Delete a metadata key for an interface.',
        [
            ('interface_uuid', 'body', 'uuid', 'The interface to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
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
        ni.remove_metadata_key(key)
