# Documentation state:
#   - Has metadata calls: deliberately not implemented
#   - OpenAPI complete: yes
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:
from flasgger import swag_from
from shakenfist_utilities import api as sf_api
from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


clusteroperation_get_example = """{
}"""


class ClusterOperationEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'clusteroperations', 'Get information for a cluster operation.',
        [
            ('operation_type', 'query', 'uuid', 'The UUID of the operation.', True),
            ('operation_uuid', 'query', 'uuid', 'The UUID of the operation.', True)
        ],
        [(200, 'Information about a single cluster operation.', clusteroperation_get_example),
         (404, 'Operation not found.', None)]))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, operation_type=None, operation_uuid=None):
        if operation_type not in OPERATION_NAMES_TO_CLASSES:
            return sf_api.error(404, 'operation type not found')
        op = get_object_class(operation_type).from_db(operation_uuid)
        if not op:
            return sf_api.error(404, 'operation not found')
        return op.external_view()
