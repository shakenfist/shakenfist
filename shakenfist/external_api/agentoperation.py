# Documentation state:
#   - Has metadata calls: deliberately not implemented
#   - OpenAPI complete: yes
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:

from flask_jwt_extended import get_jwt_identity
from flasgger import swag_from
from shakenfist_utilities import api as sf_api, logs


from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.agentoperation import AgentOperation


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def arg_is_operation_uuid(func):
    def wrapper(*args, **kwargs):
        if 'operation_uuid' in kwargs:
            kwargs['operation_from_db'] = AgentOperation.from_db(kwargs['operation_uuid'])

        if not kwargs.get('operation_from_db'):
            return sf_api.error(404, 'agent operation not found')

        return func(*args, **kwargs)
    return wrapper


def requires_operation_ownership(func):
    # Requires that @arg_is_operation_uuid has already run
    def wrapper(*args, **kwargs):
        log = LOG.with_fields({'agentoperation': kwargs['operation_uuid']})

        if not kwargs.get('operation_from_db'):
            log.info('Operation not found, kwarg missing')
            return sf_api.error(404, 'agent operation not found')

        if get_jwt_identity()[0] not in [kwargs['operation_from_db'].namespace, 'system']:
            log.info('Agent operation not found, ownership test in decorator')
            return sf_api.error(404, 'agent operation not found')

        return func(*args, **kwargs)
    return wrapper


agentoperation_get_example = """{
    ...
}"""


agentoperation_delete_example = """{
    ...
}"""


class AgentOperationEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'agentoperations', 'Get information for an agent operation.',
        [('operation_uuid', 'query', 'uuid', 'The UUID of an agent operation.', True)],
        [(200, 'Information about a single agent operation.', agentoperation_get_example),
         (404, 'Agent operation not found.', None)]))
    @api_base.verify_token
    @arg_is_operation_uuid
    @requires_operation_ownership
    @api_base.log_token_use
    def get(self, operation_uuid=None, operation_from_db=None):
        return operation_from_db.external_view()

    @swag_from(api_base.swagger_helper(
        'agentoperations', 'Delete an agent operation.',
        [('operation_uuid', 'query', 'uuid', 'The UUID of an agent operation.', True)],
        [(200, 'Information about a single agentoperation.', agentoperation_delete_example),
         (404, 'Agent operation not found.', None)]))
    @api_base.verify_token
    @arg_is_operation_uuid
    @requires_operation_ownership
    @api_base.log_token_use
    def delete(self, operation_uuid=None, operation_from_db=None):
        operation_from_db.delete()
        return operation_from_db.external_view()


class InstanceAgentOperationEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        out = []
        for agentop_uuid in instance_from_db.agent_operations:
            aop = AgentOperation.from_db(agentop_uuid)
            if aop:
                out.append(aop.external_view())
        return out
