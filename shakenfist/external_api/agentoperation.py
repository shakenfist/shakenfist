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

from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.util.access_tokens import request_namespace


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

        if request_namespace() not in [kwargs['operation_from_db'].namespace, 'system']:
            log.info('Agent operation not found, ownership test in decorator')
            return sf_api.error(404, 'agent operation not found')

        return func(*args, **kwargs)
    return wrapper


agentoperation_get_example = """{
    "attempts": 0,
    "commands": [
        {
            "block-for-result": true,
            "command": "execute",
            "commandline": "cat /tmp/README.md"
        }
    ],
    "deadline": 1787428090.5,
    "instance_uuid": "a771fb13-aaad-4cb6-a86b-7ee51e7bacc6",
    "last_progress": null,
    "metadata": {},
    "namespace": "vdi",
    "progress_timeout": 0.0,
    "results": {
        "0": {
            "command-line": "cat /tmp/README.md",
            "result": true,
            "return-code": 0,
            "stderr": "",
            "stdout": "..."
        }
    },
    "state": "complete",
    "uuid": "5a00d6f3-19b6-42bc-b1df-ddc4e5a299e9",
    "version": 3,
    "references_to": {},
    "references_from": {
        "agent_output": [
            {
                "source_object_type": "agentoperation",
                "source_uuid": "5a00d6f3-19b6-42bc-b1df-ddc4e5a299e9",
                "relationship": "agent_output",
                "relationship_value": "stdout",
                "target_object_type": "blob",
                "target_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "created": 1683995934.357137,
                "last_active": 1684054381.217045
            }
        ]
    }
}"""


class AgentOperationEndpoint(api_base.Resource):
    # Derivation would give 'agent' from the leading word, which
    # reads as though it were about the in-guest agent itself.
    scope_family = 'agentoperation'

    @swag_from(api_base.swagger_helper(
        'agentoperations', 'Get information for an agent operation.',
        [('operation_uuid', 'path', 'uuid', 'The UUID of an agent operation.', True)],
        [(200, 'Information about a single agent operation.', agentoperation_get_example),
         (404, 'Agent operation not found.', None)]))
    @arg_is_operation_uuid
    @requires_operation_ownership
    @api_base.log_token_use
    def get(self, operation_uuid=None, operation_from_db=None):
        return operation_from_db.external_view()

    @swag_from(api_base.swagger_helper(
        'agentoperations', 'Delete an agent operation.',
        [('operation_uuid', 'path', 'uuid', 'The UUID of an agent operation.', True)],
        [(200, 'Information about a single agentoperation.', None),
         (404, 'Agent operation not found.', None)]))
    @arg_is_operation_uuid
    @requires_operation_ownership
    @api_base.log_token_use
    def delete(self, operation_uuid=None, operation_from_db=None):
        operation_from_db.add_event(
            EVENT_TYPE_AUDIT, 'deletion request from REST API')
        operation_from_db.delete()
        return operation_from_db.external_view()


agentoperation_instance_example = """[
    {
        "attempts": 0,
        "commands": [
            {
                "blob_uuid": "09306f15-b1b3-4850-afb4-f4179559fa7f",
                "command": "put-blob",
                "path": "/tmp/README.md"
            },
            {
                "command": "chmod",
                "mode": 33188,
                "path": "/tmp/README.md"
            }
        ],
        "deadline": 1787428090.5,
        "instance_uuid": "a771fb13-aaad-4cb6-a86b-7ee51e7bacc6",
        "last_progress": null,
        "metadata": {},
        "namespace": "vdi",
        "progress_timeout": 30.0,
        "results": {
            "0": {
                "path": "/tmp/README.md"
            },
            "1": {
                "path": "/tmp/README.md"
            }
        },
        "state": "complete",
        "uuid": "343049d7-da2a-46f2-bb5c-edb783ec1fb9",
        "version": 3
    },
    {
        "attempts": 0,
        "commands": [
            {
                "block-for-result": true,
                "command": "execute",
                "commandline": "cat /tmp/README.md"
            }
        ],
        "deadline": 1787428090.5,
        "instance_uuid": "a771fb13-aaad-4cb6-a86b-7ee51e7bacc6",
        "last_progress": null,
        "metadata": {},
        "namespace": "vdi",
        "progress_timeout": 0.0,
        "results": {
            "0": {
                "command-line": "cat /tmp/README.md",
                "result": true,
                "return-code": 0,
                "stderr": "",
                "stdout": "...content of file..."
            }
        },
        "state": "complete",
        "uuid": "5a00d6f3-19b6-42bc-b1df-ddc4e5a299e9",
        "version": 3
    }
]"""


class InstanceAgentOperationsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'List agent operations for an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True),
         ('all', 'body', 'boolean',
          'Include operations which have already completed, rather than '
          'only those still in flight.', False)],
        [(200, 'Information about a agentoperations for an instance.',
          agentoperation_instance_example),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None, all=False):
        out = []
        ops = instance_from_db.agent_operations

        key = 'queue'
        if all:
            key = 'all'

        for agentop_uuid in ops.get(key, []):
            aop = AgentOperation.from_db(agentop_uuid)
            if aop:
                out.append(aop.external_view())
        return out
