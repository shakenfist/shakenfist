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
from shakenfist_utilities import api as sf_api  # noreorder
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


# Maximum number of ops returned by the chain endpoint. A malformed or
# maliciously crafted ``depends_on`` graph could otherwise force the
# API process to expand a very large chain (one DB round trip per
# node). The cap is also the maximum result size; callers needing
# deeper history must walk the graph themselves via repeated calls.
MAX_CHAIN_NODES = 256


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
    """Load a cluster operation by uuid, returning the dbo instance or None.

    The from_db lookup suppresses the "non-existent object" audit event
    because the op may have been hard-deleted between the
    ``get_cluster_operation`` row read above and the load below
    (the cluster cleaner runs concurrently with this endpoint).
    """
    record = mariadb.get_cluster_operation(op_uuid)
    if not record:
        return None
    operation_type = record.get('operation_type')
    if operation_type not in OPERATION_NAMES_TO_CLASSES:
        return None
    try:
        return get_object_class(operation_type).from_db(
            op_uuid, suppress_failure_audit=True)
    except NoSuchObject:
        return None


class ClusterOperationEndpoint(api_base.Resource):
    # Derivation would give 'cluster', which sounds like cluster
    # administration rather than operation history.
    scope_family = 'clusteroperation'

    @swag_from(api_base.swagger_helper(
        'clusteroperations', 'Get information for a cluster operation.',
        [
            ('operation_type', 'path', 'uuid', 'The UUID of the operation.', True),
            ('operation_uuid', 'path', 'uuid', 'The UUID of the operation.', True)
        ],
        [(200, 'Information about a single cluster operation.', clusteroperation_get_example),
         (403, 'Operation belongs to a namespace the caller cannot see.', None),
         (404, 'Operation not found.', None)]))
    @api_base.log_token_use
    def get(self, operation_type=None, operation_uuid=None):
        if operation_type not in OPERATION_NAMES_TO_CLASSES:
            return sf_api.error(404, 'operation type not found')
        op = get_object_class(operation_type).from_db(operation_uuid)
        if not op:
            return sf_api.error(404, 'operation not found')

        # Namespace gate: without this, any authenticated caller who
        # can guess (or enumerate) an op uuid gets back its
        # operation_type / state / tasks. The leak is small (no
        # payload) but it lets a tenant probe cross-namespace
        # activity and confirm op uuids surfaced in error messages.
        # Mirrors the gate in ``ClusterOperationChainEndpoint.get``
        # below; we fail closed (403) for non-admins when the op has
        # no recorded target, since cluster-scoped ancestors should
        # not be exposed without proof of ownership.
        caller_namespace = request_namespace()
        if caller_namespace != 'system':
            target = mariadb.get_cluster_operation_target(operation_uuid)
            if target is None:
                return sf_api.error(
                    403,
                    'cluster operation has no recorded target; '
                    'namespace cannot be verified')
            target_namespace = _namespace_for_target(
                target.target_object_type, target.target_uuid)
            if (target_namespace is not None and
                    target_namespace != caller_namespace):
                return sf_api.error(
                    403,
                    'cluster operation belongs to a foreign namespace')

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

    scope_family = 'clusteroperation'

    @swag_from(api_base.swagger_helper(
        'clusteroperations',
        'Get the transitive depends_on closure for a cluster operation.',
        [('op_uuid', 'path', 'uuid', 'The UUID of the operation.', True)],
        [(200, 'A list of cluster operation summary dicts, newest-first.',
          clusteroperation_chain_example),
         (400, 'Malformed depends_on entry, or chain exceeds the '
          'configured maximum size.', None),
         (403, 'Chain crosses into a namespace the caller cannot see, '
          'or includes an op without a recorded target.', None),
         (404, 'Operation not found.', None)]))
    @api_base.log_token_use
    def get(self, op_uuid=None):
        root = _hydrate_op(op_uuid)
        if not root:
            return sf_api.error(404, 'operation not found')

        caller_namespace = request_namespace()
        is_admin = (caller_namespace == 'system')

        # created_at is cached here during the BFS so the post-walk
        # sort does not re-query the database once per visited node.
        visited: dict[str, object] = {}
        created_at: dict[str, float] = {}
        order: list[str] = []
        queue: list[str] = [op_uuid]
        visited[op_uuid] = root
        order.append(op_uuid)

        while queue:
            if len(visited) > MAX_CHAIN_NODES:
                return sf_api.error(
                    400,
                    'cluster operation chain exceeds maximum size of '
                    f'{MAX_CHAIN_NODES} nodes')

            current_uuid = queue.pop(0)
            current_op = visited[current_uuid]

            # Namespace check: every visited op must be in the caller's
            # namespace (or the caller must be admin). We resolve the
            # op's namespace via its cluster_operation_targets row.
            #
            # If no target row exists (older ops written before the
            # target table existed, or ops created by a code path that
            # bypassed ``enqueue_cluster_operation``) we *fail closed*
            # for non-admins: without target information we cannot
            # prove the op belongs to the caller's namespace, and
            # exposing the chain regardless would leak cluster-scoped
            # ancestor uuids.
            if not is_admin:
                target = mariadb.get_cluster_operation_target(current_uuid)
                if target is None:
                    return sf_api.error(
                        403,
                        'cluster operation chain includes an op with no '
                        'recorded target; namespace cannot be verified')
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

            # Cache created_at for the post-walk sort.
            record = mariadb.get_cluster_operation(current_uuid)
            if record and 'created_at' in record:
                created_at[current_uuid] = float(record['created_at'])
            else:
                created_at[current_uuid] = 0.0

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

        # Sort the visited ops newest-first by created_at (cached
        # during the BFS, no further DB round trips here).
        ordered_uuids = sorted(
            order, key=lambda u: created_at.get(u, 0.0), reverse=True)
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

    scope_family = 'clusteroperation'

    @swag_from(api_base.swagger_helper(
        'clusteroperations',
        'List cluster operations targeting a specific object.',
        [
            ('target_object_type', 'body', 'string',
             'The ObjectType of the target object (e.g. "network").', True),
            ('target_uuid', 'body', 'uuid',
             'The UUID of the target object.', True),
        ],
        [(200, 'A list of cluster operation summary dicts, newest-first.',
          clusteroperations_for_target_example),
         (400, 'Invalid target_object_type or missing target_uuid.', None),
         (403, 'Target object is in a namespace you cannot see.', None),
         (404, 'Target object not found.', None)]))
    @api_base.log_token_use
    def get(self, target_object_type=None, target_uuid=None):
        # These parameters arrive as keyword arguments when the SF client
        # sends them in a JSON request body: the log_request decorator
        # (external_api/base.py) parses the body and injects each key as a
        # kwarg before the handler runs, which is how the rest of the API
        # receives request parameters. A bare ``def get(self)`` raised a
        # TypeError ("unexpected keyword argument") on every such call. We
        # also fall back to the query string so a raw ``?target_...=`` GET
        # keeps working.
        if target_object_type is None:
            target_object_type = flask.request.args.get('target_object_type')
        if target_uuid is None:
            target_uuid = flask.request.args.get('target_uuid')

        if not target_object_type:
            return sf_api.error(
                400, 'target_object_type parameter is required')
        if not target_uuid:
            return sf_api.error(
                400, 'target_uuid parameter is required')

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
