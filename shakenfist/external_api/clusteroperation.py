# Copyright 2019 Michael Still and contributors
#
# Documentation state:
#   - Has metadata calls: deliberately not implemented
#   - OpenAPI complete: yes
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:
import flask
from flasgger import swag_from
from shakenfist_utilities import api as sf_api
from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.constants import NoSuchObject
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.schema.object_types import ObjectType
from shakenfist.util.access_tokens import request_namespace


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


clusteroperation_get_example = """{
}"""


clusteroperation_chain_example = """[
    {
        "operation_type": "net_op",
        "uuid": "22b757ae-41d1-456b-84a4-edca694ceb6d",
        "state": "complete",
        "tasks": ["network_deploy"]
    }
]"""


clusteroperations_for_target_example = """[
    {
        "operation_type": "net_op",
        "uuid": "22b757ae-41d1-456b-84a4-edca694ceb6d",
        "state": "complete",
        "tasks": ["network_deploy"]
    }
]"""


def _namespace_for_target(target_object_type, target_uuid):
    """Resolve the namespace owning a cluster-operation target.

    Returns the namespace string, or None if the target object cannot be
    found or its class doesn't expose a namespace attribute. Cluster /
    node-scoped types (e.g. ``node``, ``blob``) have no namespace; the
    caller treats those as admin-only.
    """
    try:
        cls = get_object_class(str(target_object_type))
    except NoSuchObject:
        return None

    try:
        obj = cls.from_db(target_uuid)
    except Exception:
        return None
    if obj is None:
        return None

    return getattr(obj, 'namespace', None)


def _hydrate_op(op_uuid):
    """Load a cluster operation by uuid, returning the dbo instance or None."""
    record = mariadb.get_cluster_operation(op_uuid)
    if not record:
        return None
    operation_type = record.get('operation_type')
    if operation_type not in OPERATION_NAMES_TO_CLASSES:
        return None
    try:
        return get_object_class(operation_type).from_db(op_uuid)
    except NoSuchObject:
        return None


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


class ClusterOperationChainEndpoint(api_base.Resource):
    """Return the transitive ``depends_on`` closure for a cluster operation.

    Starts from ``op_uuid`` and walks every ``depends_on`` ancestor via
    BFS. The returned list contains one summary dict per visited op,
    ordered newest-first by ``created_at`` to match other listing
    endpoints. Non-admin callers see HTTP 403 if any chain member
    touches a foreign namespace (i.e. a namespace other than the
    caller's, and not the cluster ``system`` namespace).
    """

    @swag_from(api_base.swagger_helper(
        'clusteroperations',
        'Get the transitive depends_on closure for a cluster operation.',
        [('op_uuid', 'query', 'uuid', 'The UUID of the operation.', True)],
        [(200, 'A list of cluster operation summary dicts, newest-first.',
          clusteroperation_chain_example),
         (400, 'Malformed depends_on entry on a chain member.', None),
         (403, 'Chain crosses into a namespace the caller cannot see.',
          None),
         (404, 'Operation not found.', None)]))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, op_uuid=None):
        root = _hydrate_op(op_uuid)
        if not root:
            return sf_api.error(404, 'operation not found')

        caller_namespace = request_namespace()
        is_admin = (caller_namespace == 'system')

        visited: dict[str, object] = {}
        order: list[str] = []
        queue: list[str] = [op_uuid]
        visited[op_uuid] = root
        order.append(op_uuid)

        while queue:
            current_uuid = queue.pop(0)
            current_op = visited[current_uuid]

            # Namespace check: every visited op must be in the caller's
            # namespace (or the caller must be admin). We resolve the
            # op's namespace via its cluster_operation_targets row.
            if not is_admin:
                target = mariadb.get_cluster_operation_target(current_uuid)
                if target is not None:
                    target_namespace = _namespace_for_target(
                        target.target_object_type, target.target_uuid)
                    if (target_namespace is not None and
                            target_namespace != caller_namespace):
                        return sf_api.error(
                            403,
                            'cluster operation chain crosses into a '
                            'namespace you cannot see')
                    if target_namespace is None:
                        # Cluster-scoped target (e.g. node, blob) -- only
                        # admins can see ops touching these.
                        return sf_api.error(
                            403,
                            'cluster operation chain includes a '
                            'cluster-scoped target you cannot see')

            # Expand depends_on entries onto the queue.
            try:
                deps = current_op.depends_on
            except AttributeError:
                deps = []
            for dep in deps:
                if not isinstance(dep, dict):
                    return sf_api.error(
                        400, 'malformed depends_on entry')
                dep_uuid = dep.get('op_uuid')
                if not dep_uuid:
                    return sf_api.error(
                        400, 'malformed depends_on entry: missing op_uuid')
                if dep_uuid in visited:
                    continue
                dep_op = _hydrate_op(dep_uuid)
                if dep_op is None:
                    # Dangling depends_on (op was pruned). Skip silently;
                    # the chain is best-effort and a missing ancestor
                    # doesn't invalidate the rest.
                    continue
                visited[dep_uuid] = dep_op
                order.append(dep_uuid)
                queue.append(dep_uuid)

        # Sort the visited ops newest-first by created_at. Fall back to
        # insertion order if created_at is missing.
        def _sort_key(u: str) -> float:
            record = mariadb.get_cluster_operation(u)
            if record and 'created_at' in record:
                return float(record['created_at'])
            return 0.0

        ordered_uuids = sorted(order, key=_sort_key, reverse=True)
        return [visited[u].external_view() for u in ordered_uuids]


class ClusterOperationsEndpoint(api_base.Resource):
    """List cluster operations targeting a specific object.

    Query parameters:
        ``target_object_type``: a valid ``ObjectType`` value (e.g.
            ``'network'``, ``'instance'``, ``'artifact'``).
        ``target_uuid``: the UUID of the target object.

    The handler validates the caller's access to the target object via
    its namespace before issuing the query (Approach (b) from the
    Phase 7 plan): once the caller is confirmed authorised to see ops
    on this specific target, the ops list follows without additional
    namespace filtering at the SQL layer.
    """

    @swag_from(api_base.swagger_helper(
        'clusteroperations',
        'List cluster operations targeting a specific object.',
        [
            ('target_object_type', 'query', 'string',
             'The ObjectType of the target object (e.g. "network").', True),
            ('target_uuid', 'query', 'uuid',
             'The UUID of the target object.', True),
        ],
        [(200, 'A list of cluster operation summary dicts, newest-first.',
          clusteroperations_for_target_example),
         (400, 'Invalid target_object_type or missing target_uuid.', None),
         (403, 'Target object is in a namespace you cannot see.', None),
         (404, 'Target object not found.', None)]))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self):
        # webargs is not in use here -- flask_restful surfaces these
        # query string parameters via flask.request.args.
        target_object_type = flask.request.args.get('target_object_type')
        target_uuid = flask.request.args.get('target_uuid')

        if not target_object_type:
            return sf_api.error(
                400, 'target_object_type query parameter is required')
        if not target_uuid:
            return sf_api.error(
                400, 'target_uuid query parameter is required')

        try:
            object_type_enum = ObjectType(target_object_type)
        except ValueError:
            return sf_api.error(
                400, f'invalid target_object_type: {target_object_type!r}')

        # Resolve the target object to validate access. If the type has
        # no Python class registered (e.g. ``api-requests``, ``unknown``),
        # treat it as not found.
        try:
            cls = get_object_class(target_object_type)
        except NoSuchObject:
            return sf_api.error(
                400, f'unsupported target_object_type: {target_object_type!r}')

        try:
            target_obj = cls.from_db(target_uuid)
        except Exception:
            target_obj = None
        if target_obj is None:
            return sf_api.error(404, 'target object not found')

        caller_namespace = request_namespace()
        is_admin = (caller_namespace == 'system')

        target_namespace = getattr(target_obj, 'namespace', None)
        if not is_admin:
            if target_namespace is None:
                # Cluster-scoped target -- only admins may query.
                return sf_api.error(
                    403,
                    'target object is in a namespace you cannot see')
            if target_namespace != caller_namespace:
                return sf_api.error(
                    403,
                    'target object is in a namespace you cannot see')

        records = mariadb.list_cluster_operations_for_target(
            object_type_enum, target_uuid)

        retval = []
        for record in records:
            op = _hydrate_op(record.get('uuid'))
            if op is None:
                continue
            retval.append(op.external_view())
        return retval
