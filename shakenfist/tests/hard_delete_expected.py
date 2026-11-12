# Copyright 2026 Michael Still and contributors

"""Each batchable type's delete set, written down a second time.

mariadb.HARD_DELETE_BATCH_FUNCTIONS declares which public delete
functions a type's hard delete runs. This module is the independent copy
of the same knowledge, read from each class's own hard_delete() before it
moved onto the batch, and it exists so that the declaration is checked
against something other than itself.

Two sets of tests read it. test_hard_delete_batch_parity compares it with
the declaration and with the calls each class's real hard_delete() makes.
hard_delete_cases uses it to decide which rows a batch must remove, so
that a table missing from the declaration leaves a row behind in a test
rather than quietly removing itself from what the test seeds (#4880).

A change to what a type's hard delete removes has to be made here as well
as in mariadb.py.
"""

from shakenfist.schema.object_types import ObjectType


# What every type's hard_delete() runs last, from
# DatabaseBackedObject.hard_delete().
BASE_DELETES = (
    'delete_state',
    'delete_object_metadata',
    'delete_object_events',
)

# BaseClusterOperation.hard_delete(), which every cluster operation
# inherits unchanged.
CLUSTER_OPERATION_DELETES = (
    'delete_cluster_operation_target',
    'delete_cluster_operation_error',
    'delete_cluster_operation',
)

# For each batchable type, the deletes its hard delete runs before
# BASE_DELETES, in order, as read from each class before it moved onto the
# batch.
EXPECTED_DELETES = {
    ObjectType.ARTIFACT_FETCH_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.IMGCACHE_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_IFACE_IP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_IFACE_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_IP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_MACADDR_IP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_AOP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_BLOB_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_INST_NET_IFACE_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_INST_NETDESC_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_INST_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_INST_SNAP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_NET_OP: CLUSTER_OPERATION_DELETES,
    # AgentOperation derives from BaseOperation, not BaseClusterOperation,
    # so it has none of the cluster operation deletes.
    ObjectType.AGENTOPERATION: (
        'delete_agent_operation_attributes',
        'delete_agent_operation',
        'remove_all_references_from',
    ),
    # Artifact's own deletes, then the targets delete from
    # DatabaseBackedObjectWithOperations.
    ObjectType.ARTIFACT: (
        'delete_all_artifact_indexes',
        'delete_artifact_attributes',
        'delete_artifact',
        'delete_cluster_operation_targets_for_object',
    ),
    # Network's own deletes, then the targets delete from
    # DatabaseBackedObjectWithOperations.
    ObjectType.NETWORK: (
        'delete_network_attributes',
        'delete_network',
        'delete_cluster_operation_targets_for_object',
    ),
    # The references-held backstop from issue 4558, then Blob's own rows.
    ObjectType.BLOB: (
        'remove_all_references_from',
        'delete_blob_hashes',
        'delete_blob_transfers_for_blob',
        'delete_blob_attributes',
        'delete_blob',
    ),
    ObjectType.DHCP: (
        'delete_dnsmasq',
    ),
    ObjectType.INTERFACE: (
        'delete_network_interface_attributes',
        'delete_network_interface',
    ),
    # An IPAM which is not in memory only.
    ObjectType.IPAM: (
        'delete_reservations_for_ipam',
        'delete_ipam',
    ),
    ObjectType.NAMESPACE_KEY: (
        'delete_namespace_key_attributes',
        'delete_namespace_key',
    ),
    ObjectType.TRUSTED_ISSUER: (
        'delete_trusted_issuer_attributes',
        'delete_trusted_issuer',
    ),
    ObjectType.MAPPING_RULE: (
        'delete_mapping_rule_attributes',
        'delete_mapping_rule',
    ),
    ObjectType.UPLOAD: (
        'delete_upload',
    ),
}
