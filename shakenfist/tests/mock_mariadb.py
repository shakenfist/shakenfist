#
# MockMariaDB
#
# Test fixture that patches Shaken Fist's MariaDB layer with in-memory
# Python dictionaries. This class was previously named MockEtcd, because
# it originally backed the (now-removed) shakenfist.etcd module too.
#
import json
import os
import time
from collections import defaultdict
from itertools import count
from typing import List, Optional
from unittest import mock
from uuid import uuid4

from shakenfist import mariadb
from shakenfist.config import config
from shakenfist.constants import get_object_class
from shakenfist.instance import Instance
from shakenfist.namespace import Namespace
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.node import Node
from shakenfist.schema.agentoperation_attributes import AgentOperationAttributesData
from shakenfist.schema.agentoperation_data import AgentOperationData
from shakenfist.schema.artifact_attributes import ArtifactAttributesData
from shakenfist.schema.instance_attributes import InstanceAttributesData
from shakenfist.schema.instance_data import InstanceData
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.artifact_data import ArtifactData
from shakenfist.schema.artifact_index import ArtifactIndexData
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.ipam_data import IPAMData
from shakenfist.schema.network_attributes import NetworkAttributesData
from shakenfist.schema.network_data import NetworkData
from shakenfist.schema.namespace_attributes import NamespaceAttributesData
from shakenfist.schema.namespace_data import NamespaceData
from shakenfist.schema.namespace_key_attributes import NamespaceKeyAttributesData
from shakenfist.schema.namespace_key_data import NamespaceKeyData
from shakenfist.schema.network_interface_attributes import NetworkInterfaceAttributesData
from shakenfist.schema.network_interface_data import NetworkInterfaceData
from shakenfist.schema.node_attributes import NodeAttributesData
from shakenfist.schema.node_data import NodeData
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.object_state import State
from shakenfist.schema.object_reference import ObjectReference
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType


class _NamespaceAttributesStore(dict):
    """The namespace attributes store, with a legacy `keys` view.

    Namespace keys used to live in the namespace_attributes.keys JSON
    column. Phase 2 of the auth federation plan moved them into the
    namespace_keys tables, and production code now neither reads nor
    writes that column -- it is vestigial until a later schema version
    drops it.

    The behaviour preservation tests written before that cutover
    (shakenfist/tests/test_namespace_keys.py) assert against this store
    using the old shape. They are the phase's bit compatibility proof
    and must pass unmodified, so reads through this mapping render the
    key tables back into the legacy shape. The assertions stay
    meaningful -- they still describe the key material which was
    actually stored, just read out of the table which now stores it.

    Only __getitem__ is overridden. The mocked mariadb accessors use
    get() and __setitem__, so the production read path still sees the
    empty column it now writes.
    """

    def __init__(self, mock_mariadb):
        super().__init__()
        self._mock_mariadb = mock_mariadb

    def __getitem__(self, name):
        data = super().__getitem__(name)
        data.keys = self._mock_mariadb.legacy_namespace_keys_view(name)
        return data


class MockMariaDB():
    """Mock the MariaDB store with simple dictionaries

    test_obj:   TestCase object
    nodes:      List of node tuples (name, ip, list_of_node_jobs)
    node_count: Number of default nodes. Set node_count or nodes.
    """

    def __init__(self, test_obj, nodes=None, node_count=0):
        self.test_obj = test_obj
        self.mariadb_states = {}  # Mock MariaDB state storage
        self.ipam_reservations = {}  # Mock MariaDB IPAM reservations storage
        self.dnsmasq_objects = {}  # Mock MariaDB DnsMasq object storage
        self.namespace_objects = {}  # Mock MariaDB namespace storage
        # Mock MariaDB namespace attributes. See
        # _NamespaceAttributesStore for why this is not a plain dict.
        self.namespace_attributes = _NamespaceAttributesStore(self)
        self.namespace_key_objects = {}  # Mock MariaDB namespace key storage
        self.namespace_key_attributes = {}  # Mock MariaDB namespace key attrs
        self.trusted_issuers = {}  # Mock MariaDB trusted issuer storage
        self.trusted_issuer_attributes = {}  # ... and their attributes
        self.mapping_rules = {}  # Mock MariaDB mapping rule storage
        self.mapping_rule_attributes = {}  # ... and their attributes
        # Federated exchange abuse resistance. Keyed the same way the
        # real tables are: (token_id, rule_uuid) -> expires_at, and
        # (source, window_start) -> attempts.
        self.federation_replay = {}
        self.federation_rate_limits = {}
        self.node_objects = {}  # Mock MariaDB node storage
        self.node_attributes = {}  # Mock MariaDB node attributes
        self.object_references = {}  # Mock MariaDB object references
        self.artifact_objects = {}  # Mock MariaDB artifact storage
        self.artifact_attributes = {}  # Mock MariaDB artifact attributes
        self.artifact_indexes = {}  # Mock MariaDB artifact indexes
        self.network_interface_objects = {}  # Mock MariaDB network interface storage
        self.network_interface_attributes = {}  # Mock MariaDB network interface attributes
        self.network_objects = {}  # Mock MariaDB network storage
        self.network_attributes = {}  # Mock MariaDB network attributes
        self.ipam_objects = {}  # Mock MariaDB IPAM storage
        self.agent_operation_objects = {}  # Mock MariaDB agent operation storage
        self.agent_operation_attributes = {}  # Mock MariaDB agent operation attributes
        self.instance_objects = {}  # Mock MariaDB instance storage
        self.instance_attributes = {}  # Mock MariaDB instance attributes
        self.object_metadata = {}  # Mock MariaDB object metadata storage
        self.cluster_operation_targets = {}  # Mock MariaDB cluster op targets
        self._cot_sequence = count(1)  # AUTO_INCREMENT mock
        self.node_metrics_store = {}  # Mock MariaDB node metrics
        # Mock scheduler_node_capacity rows, keyed by node uuid. Empty
        # by default, which is the phase 3 fail-open case (P7): a node
        # with no capacity row admits unguarded. Tests which want the
        # guard to bite seed a row with set_node_capacity().
        self.node_capacity = {}
        # Mock namespace_claims rows, keyed by claim uuid, as the real
        # table is. Empty by default, which is the unclaimed namespace:
        # the claim stage is skipped entirely and nothing about it
        # appears in the reply. Tests which want the claim stage seed a
        # row with set_namespace_claim().
        #
        # Keyed by uuid rather than by namespace because a namespace can
        # genuinely hold more than one claim: the "one active claim per
        # namespace" rule is enforced by a probe outside the transaction,
        # so two concurrent creates can both commit. Anything which needs
        # "the namespace's claim" asks _claim_for_namespace(), which
        # resolves it the way admission does.
        self.namespace_claims = {}
        # Make every claim create or grow refuse with this reason. The
        # mock does not model the cluster_capacity singleton, so a
        # caller-side test that needs a refusal asks for one; see
        # refuse_namespace_claims().
        self.namespace_claim_refusal = ''
        self.cluster_operations_store = {}  # Mock MariaDB cluster op headers
        self.work_queue_store = []  # Mock MariaDB work_queue rows (list to keep order)
        self._work_queue_next_id = count(1)  # AUTO_INCREMENT mock
        self.obj_counter = count(1)

        # Define ShakenFist Nodes
        if nodes is not None:
            self.nodes = nodes.copy()
        else:
            # Set default nodes
            assert node_count > 0, 'Must define at least one node'
            self.nodes = [('node1_net', '10.0.0.1', [])]
            for i in range(2, node_count+1):
                self.nodes.append(('node%i' % i, '10.0.0.%i' %
                                   i, ['hypervisor']))

        self.node_names = [n[0] for n in self.nodes]

        # Optional trace logging
        self.emit_tracing = os.environ.get('MOCK_MARIADB_TRACE', '0') == '1'

    def setup(self):
        # Mock MariaDB functions for state storage
        self.mariadb_get_state = mock.patch(
            'shakenfist.mariadb.get_state',
            side_effect=self._mariadb_get_state)
        self.mariadb_get_state.start()
        self.test_obj.addCleanup(self.mariadb_get_state.stop)

        self.mariadb_set_state = mock.patch(
            'shakenfist.mariadb.set_state',
            side_effect=self._mariadb_set_state)
        self.mariadb_set_state.start()
        self.test_obj.addCleanup(self.mariadb_set_state.stop)

        self.mariadb_delete_state = mock.patch(
            'shakenfist.mariadb.delete_state',
            side_effect=self._mariadb_delete_state)
        self.mariadb_delete_state.start()
        self.test_obj.addCleanup(self.mariadb_delete_state.stop)

        self.mariadb_get_objects_by_state = mock.patch(
            'shakenfist.mariadb.get_objects_by_state',
            side_effect=self._mariadb_get_objects_by_state)
        self.mariadb_get_objects_by_state.start()
        self.test_obj.addCleanup(self.mariadb_get_objects_by_state.stop)

        # Mock MariaDB functions for IPAM reservations
        self.mariadb_reserve_address = mock.patch(
            'shakenfist.mariadb.reserve_address',
            side_effect=self._mariadb_reserve_address)
        self.mariadb_reserve_address.start()
        self.test_obj.addCleanup(self.mariadb_reserve_address.stop)

        self.mariadb_release_address = mock.patch(
            'shakenfist.mariadb.release_address',
            side_effect=self._mariadb_release_address)
        self.mariadb_release_address.start()
        self.test_obj.addCleanup(self.mariadb_release_address.stop)

        self.mariadb_get_reservation = mock.patch(
            'shakenfist.mariadb.get_reservation',
            side_effect=self._mariadb_get_reservation)
        self.mariadb_get_reservation.start()
        self.test_obj.addCleanup(self.mariadb_get_reservation.stop)

        self.mariadb_get_reservations_for_ipam = mock.patch(
            'shakenfist.mariadb.get_reservations_for_ipam',
            side_effect=self._mariadb_get_reservations_for_ipam)
        self.mariadb_get_reservations_for_ipam.start()
        self.test_obj.addCleanup(self.mariadb_get_reservations_for_ipam.stop)

        self.mariadb_delete_reservation = mock.patch(
            'shakenfist.mariadb.delete_reservation',
            side_effect=self._mariadb_delete_reservation)
        self.mariadb_delete_reservation.start()
        self.test_obj.addCleanup(self.mariadb_delete_reservation.stop)

        self.mariadb_delete_reservations_for_ipam = mock.patch(
            'shakenfist.mariadb.delete_reservations_for_ipam',
            side_effect=self._mariadb_delete_reservations_for_ipam)
        self.mariadb_delete_reservations_for_ipam.start()
        self.test_obj.addCleanup(self.mariadb_delete_reservations_for_ipam.stop)

        self.mariadb_release_haloed_addresses = mock.patch(
            'shakenfist.mariadb.release_haloed_addresses',
            side_effect=self._mariadb_release_haloed_addresses)
        self.mariadb_release_haloed_addresses.start()
        self.test_obj.addCleanup(self.mariadb_release_haloed_addresses.stop)

        self.mariadb_get_addresses_in_use = mock.patch(
            'shakenfist.mariadb.get_addresses_in_use',
            side_effect=self._mariadb_get_addresses_in_use)
        self.mariadb_get_addresses_in_use.start()
        self.test_obj.addCleanup(self.mariadb_get_addresses_in_use.stop)

        # Mock MariaDB DnsMasq operations
        self.mariadb_create_dnsmasq = mock.patch(
            'shakenfist.mariadb.create_dnsmasq',
            side_effect=self._mariadb_create_dnsmasq)
        self.mariadb_create_dnsmasq.start()
        self.test_obj.addCleanup(self.mariadb_create_dnsmasq.stop)

        self.mariadb_get_dnsmasq = mock.patch(
            'shakenfist.mariadb.get_dnsmasq',
            side_effect=self._mariadb_get_dnsmasq)
        self.mariadb_get_dnsmasq.start()
        self.test_obj.addCleanup(self.mariadb_get_dnsmasq.stop)

        self.mariadb_get_dnsmasqs = mock.patch(
            'shakenfist.mariadb.get_dnsmasqs',
            side_effect=self._mariadb_get_dnsmasqs)
        self.mariadb_get_dnsmasqs.start()
        self.test_obj.addCleanup(self.mariadb_get_dnsmasqs.stop)

        self.mariadb_delete_dnsmasq = mock.patch(
            'shakenfist.mariadb.delete_dnsmasq',
            side_effect=self._mariadb_delete_dnsmasq)
        self.mariadb_delete_dnsmasq.start()
        self.test_obj.addCleanup(self.mariadb_delete_dnsmasq.stop)

        self.mariadb_update_dnsmasq = mock.patch(
            'shakenfist.mariadb.update_dnsmasq',
            side_effect=self._mariadb_update_dnsmasq)
        self.mariadb_update_dnsmasq.start()
        self.test_obj.addCleanup(self.mariadb_update_dnsmasq.stop)

        # Mock MariaDB ObjectReference operations
        self.mariadb_get_references_to = mock.patch(
            'shakenfist.mariadb.get_references_to',
            side_effect=self._mariadb_get_references_to)
        self.mariadb_get_references_to.start()
        self.test_obj.addCleanup(self.mariadb_get_references_to.stop)

        self.mariadb_get_references_from = mock.patch(
            'shakenfist.mariadb.get_references_from',
            side_effect=self._mariadb_get_references_from)
        self.mariadb_get_references_from.start()
        self.test_obj.addCleanup(self.mariadb_get_references_from.stop)

        self.mariadb_record_relationship = mock.patch(
            'shakenfist.mariadb.record_relationship',
            side_effect=self._mariadb_record_relationship)
        self.mariadb_record_relationship.start()
        self.test_obj.addCleanup(self.mariadb_record_relationship.stop)

        self.mariadb_remove_relationship = mock.patch(
            'shakenfist.mariadb.remove_relationship',
            side_effect=self._mariadb_remove_relationship)
        self.mariadb_remove_relationship.start()
        self.test_obj.addCleanup(self.mariadb_remove_relationship.stop)

        self.mariadb_remove_all_references_from = mock.patch(
            'shakenfist.mariadb.remove_all_references_from',
            side_effect=self._mariadb_remove_all_references_from)
        self.mariadb_remove_all_references_from.start()
        self.test_obj.addCleanup(self.mariadb_remove_all_references_from.stop)

        # Mock MariaDB Node operations
        self.mariadb_create_node = mock.patch(
            'shakenfist.mariadb.create_node',
            side_effect=self._mariadb_create_node)
        self.mariadb_create_node.start()
        self.test_obj.addCleanup(
            self.mariadb_create_node.stop)

        self.mariadb_get_node = mock.patch(
            'shakenfist.mariadb.get_node',
            side_effect=self._mariadb_get_node)
        self.mariadb_get_node.start()
        self.test_obj.addCleanup(
            self.mariadb_get_node.stop)

        self.mariadb_get_node_by_fqdn = mock.patch(
            'shakenfist.mariadb.get_node_by_fqdn',
            side_effect=self._mariadb_get_node_by_fqdn)
        self.mariadb_get_node_by_fqdn.start()
        self.test_obj.addCleanup(
            self.mariadb_get_node_by_fqdn.stop)

        self.mariadb_get_all_node_uuids = mock.patch(
            'shakenfist.mariadb.get_all_node_uuids',
            side_effect=self._mariadb_get_all_node_uuids)
        self.mariadb_get_all_node_uuids.start()
        self.test_obj.addCleanup(
            self.mariadb_get_all_node_uuids.stop)

        self.mariadb_update_node = mock.patch(
            'shakenfist.mariadb.update_node',
            side_effect=self._mariadb_update_node)
        self.mariadb_update_node.start()
        self.test_obj.addCleanup(
            self.mariadb_update_node.stop)

        self.mariadb_delete_node = mock.patch(
            'shakenfist.mariadb.delete_node',
            side_effect=self._mariadb_delete_node)
        self.mariadb_delete_node.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_node.stop)

        self.mariadb_create_node_attributes = mock.patch(
            'shakenfist.mariadb.create_node_attributes',
            side_effect=(
                self._mariadb_create_node_attributes))
        self.mariadb_create_node_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_create_node_attributes.stop)

        self.mariadb_get_node_attributes = mock.patch(
            'shakenfist.mariadb.get_node_attributes',
            side_effect=(
                self._mariadb_get_node_attributes))
        self.mariadb_get_node_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_get_node_attributes.stop)

        self.mariadb_update_node_attributes = mock.patch(
            'shakenfist.mariadb.update_node_attributes',
            side_effect=(
                self._mariadb_update_node_attributes))
        self.mariadb_update_node_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_update_node_attributes.stop)

        self.mariadb_delete_node_attributes = mock.patch(
            'shakenfist.mariadb.delete_node_attributes',
            side_effect=(
                self._mariadb_delete_node_attributes))
        self.mariadb_delete_node_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_node_attributes.stop)

        # Mock MariaDB Namespace operations
        self.mariadb_create_namespace = mock.patch(
            'shakenfist.mariadb.create_namespace',
            side_effect=self._mariadb_create_namespace)
        self.mariadb_create_namespace.start()
        self.test_obj.addCleanup(self.mariadb_create_namespace.stop)

        self.mariadb_get_namespace = mock.patch(
            'shakenfist.mariadb.get_namespace',
            side_effect=self._mariadb_get_namespace)
        self.mariadb_get_namespace.start()
        self.test_obj.addCleanup(self.mariadb_get_namespace.stop)

        self.mariadb_get_all_namespace_names = mock.patch(
            'shakenfist.mariadb.get_all_namespace_names',
            side_effect=self._mariadb_get_all_namespace_names)
        self.mariadb_get_all_namespace_names.start()
        self.test_obj.addCleanup(self.mariadb_get_all_namespace_names.stop)

        self.mariadb_delete_namespace = mock.patch(
            'shakenfist.mariadb.delete_namespace',
            side_effect=self._mariadb_delete_namespace)
        self.mariadb_delete_namespace.start()
        self.test_obj.addCleanup(self.mariadb_delete_namespace.stop)

        self.mariadb_create_namespace_attributes = mock.patch(
            'shakenfist.mariadb.create_namespace_attributes',
            side_effect=self._mariadb_create_namespace_attributes)
        self.mariadb_create_namespace_attributes.start()
        self.test_obj.addCleanup(self.mariadb_create_namespace_attributes.stop)

        self.mariadb_get_namespace_attributes = mock.patch(
            'shakenfist.mariadb.get_namespace_attributes',
            side_effect=self._mariadb_get_namespace_attributes)
        self.mariadb_get_namespace_attributes.start()
        self.test_obj.addCleanup(self.mariadb_get_namespace_attributes.stop)

        self.mariadb_update_namespace_attributes = mock.patch(
            'shakenfist.mariadb.update_namespace_attributes',
            side_effect=self._mariadb_update_namespace_attributes)
        self.mariadb_update_namespace_attributes.start()
        self.test_obj.addCleanup(self.mariadb_update_namespace_attributes.stop)

        self.mariadb_delete_namespace_attributes = mock.patch(
            'shakenfist.mariadb.delete_namespace_attributes',
            side_effect=self._mariadb_delete_namespace_attributes)
        self.mariadb_delete_namespace_attributes.start()
        self.test_obj.addCleanup(self.mariadb_delete_namespace_attributes.stop)

        # MariaDB namespace key operations
        self.mariadb_create_namespace_key = mock.patch(
            'shakenfist.mariadb.create_namespace_key',
            side_effect=self._mariadb_create_namespace_key)
        self.mariadb_create_namespace_key.start()
        self.test_obj.addCleanup(self.mariadb_create_namespace_key.stop)

        for name in ('create_trusted_issuer', 'get_trusted_issuer',
                     'get_trusted_issuer_by_name',
                     'get_all_trusted_issuers', 'delete_trusted_issuer',
                     'create_trusted_issuer_attributes',
                     'get_trusted_issuer_attributes',
                     'update_trusted_issuer_attributes',
                     'delete_trusted_issuer_attributes',
                     'create_mapping_rule', 'get_mapping_rule',
                     'get_mapping_rule_by_name',
                     'get_mapping_rules_in_namespace',
                     'get_all_mapping_rules', 'delete_mapping_rule',
                     'create_mapping_rule_attributes',
                     'get_mapping_rule_attributes',
                     'update_mapping_rule_attributes',
                     'delete_mapping_rule_attributes',
                     'record_federated_exchange',
                     'count_federated_attempt',
                     'reap_federation_replay',
                     'reap_federation_rate_limits'):
            patcher = mock.patch(
                f'shakenfist.mariadb.{name}',
                side_effect=getattr(self, f'_mariadb_{name}'))
            patcher.start()
            self.test_obj.addCleanup(patcher.stop)

        self.mariadb_get_namespace_key = mock.patch(
            'shakenfist.mariadb.get_namespace_key',
            side_effect=self._mariadb_get_namespace_key)
        self.mariadb_get_namespace_key.start()
        self.test_obj.addCleanup(self.mariadb_get_namespace_key.stop)

        self.mariadb_get_namespace_key_by_name = mock.patch(
            'shakenfist.mariadb.get_namespace_key_by_name',
            side_effect=self._mariadb_get_namespace_key_by_name)
        self.mariadb_get_namespace_key_by_name.start()
        self.test_obj.addCleanup(self.mariadb_get_namespace_key_by_name.stop)

        self.mariadb_find_namespace_keys = mock.patch(
            'shakenfist.mariadb.find_namespace_keys',
            side_effect=self._mariadb_find_namespace_keys)
        self.mariadb_find_namespace_keys.start()
        self.test_obj.addCleanup(self.mariadb_find_namespace_keys.stop)

        self.mariadb_delete_namespace_key = mock.patch(
            'shakenfist.mariadb.delete_namespace_key',
            side_effect=self._mariadb_delete_namespace_key)
        self.mariadb_delete_namespace_key.start()
        self.test_obj.addCleanup(self.mariadb_delete_namespace_key.stop)

        self.mariadb_delete_expired_namespace_keys = mock.patch(
            'shakenfist.mariadb.delete_expired_namespace_keys',
            side_effect=self._mariadb_delete_expired_namespace_keys)
        self.mariadb_delete_expired_namespace_keys.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_expired_namespace_keys.stop)

        # MariaDB namespace key attributes operations
        self.mariadb_create_namespace_key_attributes = mock.patch(
            'shakenfist.mariadb.create_namespace_key_attributes',
            side_effect=self._mariadb_create_namespace_key_attributes)
        self.mariadb_create_namespace_key_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_create_namespace_key_attributes.stop)

        self.mariadb_get_namespace_key_attributes = mock.patch(
            'shakenfist.mariadb.get_namespace_key_attributes',
            side_effect=self._mariadb_get_namespace_key_attributes)
        self.mariadb_get_namespace_key_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_get_namespace_key_attributes.stop)

        self.mariadb_update_namespace_key_attributes = mock.patch(
            'shakenfist.mariadb.update_namespace_key_attributes',
            side_effect=self._mariadb_update_namespace_key_attributes)
        self.mariadb_update_namespace_key_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_update_namespace_key_attributes.stop)

        self.mariadb_delete_namespace_key_attributes = mock.patch(
            'shakenfist.mariadb.delete_namespace_key_attributes',
            side_effect=self._mariadb_delete_namespace_key_attributes)
        self.mariadb_delete_namespace_key_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_namespace_key_attributes.stop)

        # MariaDB artifact operations
        self.mariadb_create_artifact = mock.patch(
            'shakenfist.mariadb.create_artifact',
            side_effect=self._mariadb_create_artifact)
        self.mariadb_create_artifact.start()
        self.test_obj.addCleanup(self.mariadb_create_artifact.stop)

        self.mariadb_get_artifact = mock.patch(
            'shakenfist.mariadb.get_artifact',
            side_effect=self._mariadb_get_artifact)
        self.mariadb_get_artifact.start()
        self.test_obj.addCleanup(self.mariadb_get_artifact.stop)

        self.mariadb_get_all_artifacts = mock.patch(
            'shakenfist.mariadb.get_all_artifacts',
            side_effect=self._mariadb_get_all_artifacts)
        self.mariadb_get_all_artifacts.start()
        self.test_obj.addCleanup(self.mariadb_get_all_artifacts.stop)

        self.mariadb_find_artifacts = mock.patch(
            'shakenfist.mariadb.find_artifacts',
            side_effect=self._mariadb_find_artifacts)
        self.mariadb_find_artifacts.start()
        self.test_obj.addCleanup(self.mariadb_find_artifacts.stop)

        self.mariadb_update_artifact = mock.patch(
            'shakenfist.mariadb.update_artifact',
            side_effect=self._mariadb_update_artifact)
        self.mariadb_update_artifact.start()
        self.test_obj.addCleanup(self.mariadb_update_artifact.stop)

        self.mariadb_delete_artifact = mock.patch(
            'shakenfist.mariadb.delete_artifact',
            side_effect=self._mariadb_delete_artifact)
        self.mariadb_delete_artifact.start()
        self.test_obj.addCleanup(self.mariadb_delete_artifact.stop)

        # MariaDB artifact attributes operations
        self.mariadb_create_artifact_attributes = mock.patch(
            'shakenfist.mariadb.create_artifact_attributes',
            side_effect=self._mariadb_create_artifact_attributes)
        self.mariadb_create_artifact_attributes.start()
        self.test_obj.addCleanup(self.mariadb_create_artifact_attributes.stop)

        self.mariadb_get_artifact_attributes = mock.patch(
            'shakenfist.mariadb.get_artifact_attributes',
            side_effect=self._mariadb_get_artifact_attributes)
        self.mariadb_get_artifact_attributes.start()
        self.test_obj.addCleanup(self.mariadb_get_artifact_attributes.stop)

        self.mariadb_update_artifact_attributes = mock.patch(
            'shakenfist.mariadb.update_artifact_attributes',
            side_effect=self._mariadb_update_artifact_attributes)
        self.mariadb_update_artifact_attributes.start()
        self.test_obj.addCleanup(self.mariadb_update_artifact_attributes.stop)

        self.mariadb_delete_artifact_attributes = mock.patch(
            'shakenfist.mariadb.delete_artifact_attributes',
            side_effect=self._mariadb_delete_artifact_attributes)
        self.mariadb_delete_artifact_attributes.start()
        self.test_obj.addCleanup(self.mariadb_delete_artifact_attributes.stop)

        # MariaDB artifact index operations
        self.mariadb_create_artifact_index = mock.patch(
            'shakenfist.mariadb.create_artifact_index',
            side_effect=self._mariadb_create_artifact_index)
        self.mariadb_create_artifact_index.start()
        self.test_obj.addCleanup(self.mariadb_create_artifact_index.stop)

        self.mariadb_get_artifact_index = mock.patch(
            'shakenfist.mariadb.get_artifact_index',
            side_effect=self._mariadb_get_artifact_index)
        self.mariadb_get_artifact_index.start()
        self.test_obj.addCleanup(self.mariadb_get_artifact_index.stop)

        self.mariadb_get_all_artifact_indexes = mock.patch(
            'shakenfist.mariadb.get_all_artifact_indexes',
            side_effect=self._mariadb_get_all_artifact_indexes)
        self.mariadb_get_all_artifact_indexes.start()
        self.test_obj.addCleanup(self.mariadb_get_all_artifact_indexes.stop)

        self.mariadb_delete_artifact_index = mock.patch(
            'shakenfist.mariadb.delete_artifact_index',
            side_effect=self._mariadb_delete_artifact_index)
        self.mariadb_delete_artifact_index.start()
        self.test_obj.addCleanup(self.mariadb_delete_artifact_index.stop)

        self.mariadb_delete_all_artifact_indexes = mock.patch(
            'shakenfist.mariadb.delete_all_artifact_indexes',
            side_effect=self._mariadb_delete_all_artifact_indexes)
        self.mariadb_delete_all_artifact_indexes.start()
        self.test_obj.addCleanup(self.mariadb_delete_all_artifact_indexes.stop)

        # MariaDB network interface operations
        self.mariadb_create_network_interface = mock.patch(
            'shakenfist.mariadb.create_network_interface',
            side_effect=self._mariadb_create_network_interface)
        self.mariadb_create_network_interface.start()
        self.test_obj.addCleanup(self.mariadb_create_network_interface.stop)

        self.mariadb_get_network_interface = mock.patch(
            'shakenfist.mariadb.get_network_interface',
            side_effect=self._mariadb_get_network_interface)
        self.mariadb_get_network_interface.start()
        self.test_obj.addCleanup(self.mariadb_get_network_interface.stop)

        self.mariadb_delete_network_interface = mock.patch(
            'shakenfist.mariadb.delete_network_interface',
            side_effect=self._mariadb_delete_network_interface)
        self.mariadb_delete_network_interface.start()
        self.test_obj.addCleanup(self.mariadb_delete_network_interface.stop)

        self.mariadb_update_network_interface = mock.patch(
            'shakenfist.mariadb.update_network_interface',
            side_effect=self._mariadb_update_network_interface)
        self.mariadb_update_network_interface.start()
        self.test_obj.addCleanup(self.mariadb_update_network_interface.stop)

        self.mariadb_find_network_interfaces = mock.patch(
            'shakenfist.mariadb.find_network_interfaces',
            side_effect=self._mariadb_find_network_interfaces)
        self.mariadb_find_network_interfaces.start()
        self.test_obj.addCleanup(self.mariadb_find_network_interfaces.stop)

        # MariaDB network interface attributes operations
        self.mariadb_create_network_interface_attributes = mock.patch(
            'shakenfist.mariadb.create_network_interface_attributes',
            side_effect=self._mariadb_create_network_interface_attributes)
        self.mariadb_create_network_interface_attributes.start()
        self.test_obj.addCleanup(self.mariadb_create_network_interface_attributes.stop)

        self.mariadb_get_network_interface_attributes = mock.patch(
            'shakenfist.mariadb.get_network_interface_attributes',
            side_effect=self._mariadb_get_network_interface_attributes)
        self.mariadb_get_network_interface_attributes.start()
        self.test_obj.addCleanup(self.mariadb_get_network_interface_attributes.stop)

        self.mariadb_update_network_interface_attributes = mock.patch(
            'shakenfist.mariadb.update_network_interface_attributes',
            side_effect=self._mariadb_update_network_interface_attributes)
        self.mariadb_update_network_interface_attributes.start()
        self.test_obj.addCleanup(self.mariadb_update_network_interface_attributes.stop)

        self.mariadb_delete_network_interface_attributes = mock.patch(
            'shakenfist.mariadb.delete_network_interface_attributes',
            side_effect=self._mariadb_delete_network_interface_attributes)
        self.mariadb_delete_network_interface_attributes.start()
        self.test_obj.addCleanup(self.mariadb_delete_network_interface_attributes.stop)

        # MariaDB network operations
        self.mariadb_create_network = mock.patch(
            'shakenfist.mariadb.create_network',
            side_effect=self._mariadb_create_network)
        self.mariadb_create_network.start()
        self.test_obj.addCleanup(self.mariadb_create_network.stop)

        self.mariadb_get_network = mock.patch(
            'shakenfist.mariadb.get_network',
            side_effect=self._mariadb_get_network)
        self.mariadb_get_network.start()
        self.test_obj.addCleanup(self.mariadb_get_network.stop)

        self.mariadb_get_all_networks = mock.patch(
            'shakenfist.mariadb.get_all_networks',
            side_effect=self._mariadb_get_all_networks)
        self.mariadb_get_all_networks.start()
        self.test_obj.addCleanup(self.mariadb_get_all_networks.stop)

        self.mariadb_find_networks = mock.patch(
            'shakenfist.mariadb.find_networks',
            side_effect=self._mariadb_find_networks)
        self.mariadb_find_networks.start()
        self.test_obj.addCleanup(self.mariadb_find_networks.stop)

        self.mariadb_find_network_vxids = mock.patch(
            'shakenfist.mariadb.find_network_vxids',
            side_effect=self._mariadb_find_network_vxids)
        self.mariadb_find_network_vxids.start()
        self.test_obj.addCleanup(self.mariadb_find_network_vxids.stop)

        self.mariadb_delete_network = mock.patch(
            'shakenfist.mariadb.delete_network',
            side_effect=self._mariadb_delete_network)
        self.mariadb_delete_network.start()
        self.test_obj.addCleanup(self.mariadb_delete_network.stop)

        # MariaDB network attributes operations
        self.mariadb_create_network_attributes = mock.patch(
            'shakenfist.mariadb.create_network_attributes',
            side_effect=self._mariadb_create_network_attributes)
        self.mariadb_create_network_attributes.start()
        self.test_obj.addCleanup(self.mariadb_create_network_attributes.stop)

        self.mariadb_get_network_attributes = mock.patch(
            'shakenfist.mariadb.get_network_attributes',
            side_effect=self._mariadb_get_network_attributes)
        self.mariadb_get_network_attributes.start()
        self.test_obj.addCleanup(self.mariadb_get_network_attributes.stop)

        self.mariadb_update_network_attributes = mock.patch(
            'shakenfist.mariadb.update_network_attributes',
            side_effect=self._mariadb_update_network_attributes)
        self.mariadb_update_network_attributes.start()
        self.test_obj.addCleanup(self.mariadb_update_network_attributes.stop)

        self.mariadb_delete_network_attributes = mock.patch(
            'shakenfist.mariadb.delete_network_attributes',
            side_effect=self._mariadb_delete_network_attributes)
        self.mariadb_delete_network_attributes.start()
        self.test_obj.addCleanup(self.mariadb_delete_network_attributes.stop)

        # MariaDB IPAM operations
        self.mariadb_create_ipam = mock.patch(
            'shakenfist.mariadb.create_ipam',
            side_effect=self._mariadb_create_ipam)
        self.mariadb_create_ipam.start()
        self.test_obj.addCleanup(self.mariadb_create_ipam.stop)

        self.mariadb_get_ipam = mock.patch(
            'shakenfist.mariadb.get_ipam',
            side_effect=self._mariadb_get_ipam)
        self.mariadb_get_ipam.start()
        self.test_obj.addCleanup(self.mariadb_get_ipam.stop)

        self.mariadb_delete_ipam = mock.patch(
            'shakenfist.mariadb.delete_ipam',
            side_effect=self._mariadb_delete_ipam)
        self.mariadb_delete_ipam.start()
        self.test_obj.addCleanup(self.mariadb_delete_ipam.stop)

        self.mariadb_update_ipam = mock.patch(
            'shakenfist.mariadb.update_ipam',
            side_effect=self._mariadb_update_ipam)
        self.mariadb_update_ipam.start()
        self.test_obj.addCleanup(self.mariadb_update_ipam.stop)

        # MariaDB agent operation operations
        self.mariadb_create_agent_operation = mock.patch(
            'shakenfist.mariadb.create_agent_operation',
            side_effect=self._mariadb_create_agent_operation)
        self.mariadb_create_agent_operation.start()
        self.test_obj.addCleanup(self.mariadb_create_agent_operation.stop)

        self.mariadb_get_agent_operation = mock.patch(
            'shakenfist.mariadb.get_agent_operation',
            side_effect=self._mariadb_get_agent_operation)
        self.mariadb_get_agent_operation.start()
        self.test_obj.addCleanup(self.mariadb_get_agent_operation.stop)

        self.mariadb_delete_agent_operation = mock.patch(
            'shakenfist.mariadb.delete_agent_operation',
            side_effect=self._mariadb_delete_agent_operation)
        self.mariadb_delete_agent_operation.start()
        self.test_obj.addCleanup(self.mariadb_delete_agent_operation.stop)

        # MariaDB agent operation attributes operations
        self.mariadb_create_agent_operation_attributes = mock.patch(
            'shakenfist.mariadb.create_agent_operation_attributes',
            side_effect=self._mariadb_create_agent_operation_attributes)
        self.mariadb_create_agent_operation_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_create_agent_operation_attributes.stop)

        self.mariadb_get_agent_operation_attributes = mock.patch(
            'shakenfist.mariadb.get_agent_operation_attributes',
            side_effect=self._mariadb_get_agent_operation_attributes)
        self.mariadb_get_agent_operation_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_get_agent_operation_attributes.stop)

        self.mariadb_update_agent_operation_attributes = mock.patch(
            'shakenfist.mariadb.update_agent_operation_attributes',
            side_effect=self._mariadb_update_agent_operation_attributes)
        self.mariadb_update_agent_operation_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_update_agent_operation_attributes.stop)

        self.mariadb_delete_agent_operation_attributes = mock.patch(
            'shakenfist.mariadb.delete_agent_operation_attributes',
            side_effect=self._mariadb_delete_agent_operation_attributes)
        self.mariadb_delete_agent_operation_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_agent_operation_attributes.stop)

        # MariaDB instance operations
        self.mariadb_create_instance = mock.patch(
            'shakenfist.mariadb.create_instance',
            side_effect=self._mariadb_create_instance)
        self.mariadb_create_instance.start()
        self.test_obj.addCleanup(self.mariadb_create_instance.stop)

        self.mariadb_get_instance = mock.patch(
            'shakenfist.mariadb.get_instance',
            side_effect=self._mariadb_get_instance)
        self.mariadb_get_instance.start()
        self.test_obj.addCleanup(self.mariadb_get_instance.stop)

        self.mariadb_get_all_instances = mock.patch(
            'shakenfist.mariadb.get_all_instances',
            side_effect=self._mariadb_get_all_instances)
        self.mariadb_get_all_instances.start()
        self.test_obj.addCleanup(self.mariadb_get_all_instances.stop)

        self.mariadb_find_instances = mock.patch(
            'shakenfist.mariadb.find_instances',
            side_effect=self._mariadb_find_instances)
        self.mariadb_find_instances.start()
        self.test_obj.addCleanup(self.mariadb_find_instances.stop)

        self.mariadb_get_all_instance_uuids = mock.patch(
            'shakenfist.mariadb.get_all_instance_uuids',
            side_effect=self._mariadb_get_all_instance_uuids)
        self.mariadb_get_all_instance_uuids.start()
        self.test_obj.addCleanup(
            self.mariadb_get_all_instance_uuids.stop)

        self.mariadb_delete_instance = mock.patch(
            'shakenfist.mariadb.delete_instance',
            side_effect=self._mariadb_delete_instance)
        self.mariadb_delete_instance.start()
        self.test_obj.addCleanup(self.mariadb_delete_instance.stop)

        # MariaDB instance attributes operations
        self.mariadb_create_instance_attributes = mock.patch(
            'shakenfist.mariadb.create_instance_attributes',
            side_effect=self._mariadb_create_instance_attributes)
        self.mariadb_create_instance_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_create_instance_attributes.stop)

        self.mariadb_get_instance_attributes = mock.patch(
            'shakenfist.mariadb.get_instance_attributes',
            side_effect=self._mariadb_get_instance_attributes)
        self.mariadb_get_instance_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_get_instance_attributes.stop)

        self.mariadb_update_instance_attributes = mock.patch(
            'shakenfist.mariadb.update_instance_attributes',
            side_effect=self._mariadb_update_instance_attributes)
        self.mariadb_update_instance_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_update_instance_attributes.stop)

        self.mariadb_delete_instance_attributes = mock.patch(
            'shakenfist.mariadb.delete_instance_attributes',
            side_effect=self._mariadb_delete_instance_attributes)
        self.mariadb_delete_instance_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_instance_attributes.stop)

        # MariaDB atomic placement admission and release
        self.mariadb_admit_instance_placement = mock.patch(
            'shakenfist.mariadb.admit_instance_placement',
            side_effect=self._mariadb_admit_instance_placement)
        self.mariadb_admit_instance_placement.start()
        self.test_obj.addCleanup(
            self.mariadb_admit_instance_placement.stop)

        self.mariadb_release_instance_placement = mock.patch(
            'shakenfist.mariadb.release_instance_placement',
            side_effect=self._mariadb_release_instance_placement)
        self.mariadb_release_instance_placement.start()
        self.test_obj.addCleanup(
            self.mariadb_release_instance_placement.stop)

        self.mariadb_get_scheduler_node_capacity = mock.patch(
            'shakenfist.mariadb.get_scheduler_node_capacity',
            side_effect=self._mariadb_get_scheduler_node_capacity)
        self.mariadb_get_scheduler_node_capacity.start()
        self.test_obj.addCleanup(
            self.mariadb_get_scheduler_node_capacity.stop)

        # MariaDB namespace claim CRUD
        self.mariadb_create_namespace_claim = mock.patch(
            'shakenfist.mariadb.create_namespace_claim',
            side_effect=self._mariadb_create_namespace_claim)
        self.mariadb_create_namespace_claim.start()
        self.test_obj.addCleanup(
            self.mariadb_create_namespace_claim.stop)

        self.mariadb_get_namespace_claim = mock.patch(
            'shakenfist.mariadb.get_namespace_claim',
            side_effect=self._mariadb_get_namespace_claim)
        self.mariadb_get_namespace_claim.start()
        self.test_obj.addCleanup(self.mariadb_get_namespace_claim.stop)

        self.mariadb_get_namespace_claims = mock.patch(
            'shakenfist.mariadb.get_namespace_claims',
            side_effect=self._mariadb_get_namespace_claims)
        self.mariadb_get_namespace_claims.start()
        self.test_obj.addCleanup(self.mariadb_get_namespace_claims.stop)

        self.mariadb_update_namespace_claim = mock.patch(
            'shakenfist.mariadb.update_namespace_claim',
            side_effect=self._mariadb_update_namespace_claim)
        self.mariadb_update_namespace_claim.start()
        self.test_obj.addCleanup(
            self.mariadb_update_namespace_claim.stop)

        self.mariadb_delete_namespace_claim = mock.patch(
            'shakenfist.mariadb.delete_namespace_claim',
            side_effect=self._mariadb_delete_namespace_claim)
        self.mariadb_delete_namespace_claim.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_namespace_claim.stop)

        # Mock MariaDB functions for object metadata
        self.mariadb_get_object_metadata = mock.patch(
            'shakenfist.mariadb.get_object_metadata',
            side_effect=self._mariadb_get_object_metadata)
        self.mariadb_get_object_metadata.start()
        self.test_obj.addCleanup(
            self.mariadb_get_object_metadata.stop)

        self.mariadb_set_metadata = mock.patch(
            'shakenfist.mariadb.set_metadata',
            side_effect=self._mariadb_set_metadata)
        self.mariadb_set_metadata.start()
        self.test_obj.addCleanup(
            self.mariadb_set_metadata.stop)

        self.mariadb_delete_object_metadata = mock.patch(
            'shakenfist.mariadb.delete_object_metadata',
            side_effect=self._mariadb_delete_object_metadata)
        self.mariadb_delete_object_metadata.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_object_metadata.stop)

        # DatabaseBackedObject.hard_delete() purges the object's events.
        # MockMariaDB does not store events (they are suppressed for unit
        # tests), so this only needs to stop the real accessor from
        # reaching for a database connection.
        self.mariadb_delete_object_events = mock.patch(
            'shakenfist.mariadb.delete_object_events',
            side_effect=self._mariadb_delete_object_events)
        self.mariadb_delete_object_events.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_object_events.stop)

        # Mock MariaDB functions for cluster operation targets
        self.mariadb_create_cluster_operation_target = mock.patch(
            'shakenfist.mariadb.create_cluster_operation_target',
            side_effect=self._mariadb_create_cluster_operation_target)
        self.mariadb_create_cluster_operation_target.start()
        self.test_obj.addCleanup(
            self.mariadb_create_cluster_operation_target.stop)

        self.mariadb_get_cluster_operation_target = mock.patch(
            'shakenfist.mariadb.get_cluster_operation_target',
            side_effect=self._mariadb_get_cluster_operation_target)
        self.mariadb_get_cluster_operation_target.start()
        self.test_obj.addCleanup(
            self.mariadb_get_cluster_operation_target.stop)

        self.mariadb_get_cluster_operation_targets_for_object = mock.patch(
            'shakenfist.mariadb.get_cluster_operation_targets_for_object',
            side_effect=self._mariadb_get_cluster_operation_targets_for_object)
        self.mariadb_get_cluster_operation_targets_for_object.start()
        self.test_obj.addCleanup(
            self.mariadb_get_cluster_operation_targets_for_object.stop)

        self.mariadb_get_latest_cluster_operation_target = mock.patch(
            'shakenfist.mariadb.get_latest_cluster_operation_target',
            side_effect=self._mariadb_get_latest_cluster_operation_target)
        self.mariadb_get_latest_cluster_operation_target.start()
        self.test_obj.addCleanup(
            self.mariadb_get_latest_cluster_operation_target.stop)

        self.mariadb_delete_cluster_operation_target = mock.patch(
            'shakenfist.mariadb.delete_cluster_operation_target',
            side_effect=self._mariadb_delete_cluster_operation_target)
        self.mariadb_delete_cluster_operation_target.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_cluster_operation_target.stop)

        self.mariadb_delete_cluster_operation_targets_for_object = mock.patch(
            'shakenfist.mariadb.delete_cluster_operation_targets_for_object',
            side_effect=self._mariadb_delete_cluster_operation_targets_for_object)
        self.mariadb_delete_cluster_operation_targets_for_object.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_cluster_operation_targets_for_object.stop)

        self.mariadb_delete_stale_cluster_operation_targets = mock.patch(
            'shakenfist.mariadb.delete_stale_cluster_operation_targets',
            side_effect=self._mariadb_delete_stale_cluster_operation_targets)
        self.mariadb_delete_stale_cluster_operation_targets.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_stale_cluster_operation_targets.stop)

        # Mock MariaDB node metrics operations
        self.mariadb_upsert_node_metrics = mock.patch(
            'shakenfist.mariadb.upsert_node_metrics',
            side_effect=self._mariadb_upsert_node_metrics)
        self.mariadb_upsert_node_metrics.start()
        self.test_obj.addCleanup(
            self.mariadb_upsert_node_metrics.stop)

        self.mariadb_get_node_metrics = mock.patch(
            'shakenfist.mariadb.get_node_metrics',
            side_effect=self._mariadb_get_node_metrics)
        self.mariadb_get_node_metrics.start()
        self.test_obj.addCleanup(
            self.mariadb_get_node_metrics.stop)

        self.mariadb_get_all_node_metrics = mock.patch(
            'shakenfist.mariadb.get_all_node_metrics',
            side_effect=self._mariadb_get_all_node_metrics)
        self.mariadb_get_all_node_metrics.start()
        self.test_obj.addCleanup(
            self.mariadb_get_all_node_metrics.stop)

        self.mariadb_delete_node_metrics = mock.patch(
            'shakenfist.mariadb.delete_node_metrics',
            side_effect=self._mariadb_delete_node_metrics)
        self.mariadb_delete_node_metrics.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_node_metrics.stop)

        # Mock MariaDB cluster_operations operations
        self.mariadb_create_cluster_operation = mock.patch(
            'shakenfist.mariadb.create_cluster_operation',
            side_effect=self._mariadb_create_cluster_operation)
        self.mariadb_create_cluster_operation.start()
        self.test_obj.addCleanup(
            self.mariadb_create_cluster_operation.stop)

        self.mariadb_get_cluster_operation = mock.patch(
            'shakenfist.mariadb.get_cluster_operation',
            side_effect=self._mariadb_get_cluster_operation)
        self.mariadb_get_cluster_operation.start()
        self.test_obj.addCleanup(
            self.mariadb_get_cluster_operation.stop)

        self.mariadb_get_cluster_operations_by_node = mock.patch(
            'shakenfist.mariadb.get_cluster_operations_by_node',
            side_effect=self._mariadb_get_cluster_operations_by_node)
        self.mariadb_get_cluster_operations_by_node.start()
        self.test_obj.addCleanup(
            self.mariadb_get_cluster_operations_by_node.stop)

        self.mariadb_delete_cluster_operation = mock.patch(
            'shakenfist.mariadb.delete_cluster_operation',
            side_effect=self._mariadb_delete_cluster_operation)
        self.mariadb_delete_cluster_operation.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_cluster_operation.stop)

        self.mariadb_create_and_enqueue_cluster_operation = (
            mock.patch(
                'shakenfist.mariadb'
                '.create_and_enqueue_cluster_operation',
                side_effect=(
                    self
                    ._mariadb_create_and_enqueue_cluster_operation)
            )
        )
        self.mariadb_create_and_enqueue_cluster_operation.start()
        self.test_obj.addCleanup(
            self.mariadb_create_and_enqueue_cluster_operation.stop)

        # Mock MariaDB work queue public API
        self.mariadb_enqueue_work_item = mock.patch(
            'shakenfist.mariadb.enqueue_work_item',
            side_effect=self._mariadb_enqueue_work_item)
        self.mariadb_enqueue_work_item.start()
        self.test_obj.addCleanup(
            self.mariadb_enqueue_work_item.stop)

        self.mariadb_dequeue_work_items = mock.patch(
            'shakenfist.mariadb.dequeue_work_items',
            side_effect=self._mariadb_dequeue_work_items)
        self.mariadb_dequeue_work_items.start()
        self.test_obj.addCleanup(
            self.mariadb_dequeue_work_items.stop)

        self.mariadb_resolve_work_item = mock.patch(
            'shakenfist.mariadb.resolve_work_item',
            side_effect=self._mariadb_resolve_work_item)
        self.mariadb_resolve_work_item.start()
        self.test_obj.addCleanup(
            self.mariadb_resolve_work_item.stop)

        self.mariadb_get_work_queue_length = mock.patch(
            'shakenfist.mariadb.get_work_queue_length',
            side_effect=self._mariadb_get_work_queue_length)
        self.mariadb_get_work_queue_length.start()
        self.test_obj.addCleanup(
            self.mariadb_get_work_queue_length.stop)

        self.mariadb_restart_work_queue = mock.patch(
            'shakenfist.mariadb.restart_work_queue',
            side_effect=self._mariadb_restart_work_queue)
        self.mariadb_restart_work_queue.start()
        self.test_obj.addCleanup(
            self.mariadb_restart_work_queue.stop)

        self.mariadb_list_stuck_work_queue_rows = mock.patch(
            'shakenfist.mariadb.list_stuck_work_queue_rows',
            side_effect=self._mariadb_list_stuck_work_queue_rows)
        self.mariadb_list_stuck_work_queue_rows.start()
        self.test_obj.addCleanup(
            self.mariadb_list_stuck_work_queue_rows.stop)

        self.mariadb_clear_work_queue_claim = mock.patch(
            'shakenfist.mariadb.clear_work_queue_claim',
            side_effect=self._mariadb_clear_work_queue_claim)
        self.mariadb_clear_work_queue_claim.start()
        self.test_obj.addCleanup(
            self.mariadb_clear_work_queue_claim.stop)

        self.mariadb_delete_work_queue_row = mock.patch(
            'shakenfist.mariadb.delete_work_queue_row',
            side_effect=self._mariadb_delete_work_queue_row)
        self.mariadb_delete_work_queue_row.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_work_queue_row.stop)

        self.mariadb_claim_coalescible_siblings = mock.patch(
            'shakenfist.mariadb.claim_coalescible_siblings',
            side_effect=self._mariadb_claim_coalescible_siblings)
        self.mariadb_claim_coalescible_siblings.start()
        self.test_obj.addCleanup(
            self.mariadb_claim_coalescible_siblings.stop)

        self.mariadb_find_existing_coalescible_op = mock.patch(
            'shakenfist.mariadb.find_existing_coalescible_op',
            side_effect=self._mariadb_find_existing_coalescible_op)
        self.mariadb_find_existing_coalescible_op.start()
        self.test_obj.addCleanup(
            self.mariadb_find_existing_coalescible_op.stop)

        # Mock cluster lock operations (used by locks.ClusterLock)
        self.db_acquire_lock = mock.patch(
            'shakenfist.mariadb.acquire_cluster_lock',
            return_value=True)
        self.db_acquire_lock.start()
        self.test_obj.addCleanup(self.db_acquire_lock.stop)

        self.db_release_lock = mock.patch(
            'shakenfist.mariadb.release_cluster_lock',
            return_value=True)
        self.db_release_lock.start()
        self.test_obj.addCleanup(self.db_release_lock.stop)

        self.db_get_lock_holder = mock.patch(
            'shakenfist.mariadb.get_cluster_lock_holder',
            return_value={'holder': None})
        self.db_get_lock_holder.start()
        self.test_obj.addCleanup(self.db_get_lock_holder.stop)

        self.db_clear_stale_locks = mock.patch(
            'shakenfist.mariadb.clear_stale_cluster_locks')
        self.db_clear_stale_locks.start()
        self.test_obj.addCleanup(self.db_clear_stale_locks.stop)

        self.db_get_existing_locks = mock.patch(
            'shakenfist.mariadb.get_all_cluster_locks',
            return_value={})
        self.db_get_existing_locks.start()
        self.test_obj.addCleanup(self.db_get_existing_locks.stop)

        # Setup basic DB data
        self.node_uuids = {}
        for n in self.nodes:
            node_obj = Node.new(n[0], n[1])
            self.node_uuids[n[0]] = str(node_obj.uuid)

    def next_uuid(self):
        """Generate predictable UUIDs that are unique during the testcase"""
        # NOTE(mikal): there are version and variant fields in uuid4's that
        # pydantic enforces.
        #               version    variant
        #                     *    *
        return '12345678-1234-4321-8234-%012i' % next(self.obj_counter)

    def _trace(self, m):
        if self.emit_tracing:
            print(m)

    #
    # MariaDB mock operations
    #

    def _mariadb_get_state(self, object_type: ObjectType,
                           object_uuid: str) -> Optional[State]:
        """Mock implementation of mariadb.get_state()"""
        # Key by object_type and object_uuid to avoid collisions between
        # different object types sharing the same UUID (e.g., ipam/network)
        key = f'{object_type}/{object_uuid}'
        if key in self.mariadb_states:
            data = self.mariadb_states[key]
            self._trace(f'MockMariaDB.get_state({key}): {data}')
            return State(
                value=data['state_value'],
                update_time=data['update_time'],
                message=data['message']
            )
        self._trace(f'MockMariaDB.get_state({key}): None')
        return None

    def _mariadb_set_state(self, object_type: ObjectType, object_uuid: str,
                           state: State) -> bool:
        """Mock implementation of mariadb.set_state()"""
        key = f'{object_type}/{object_uuid}'
        self.mariadb_states[key] = {
            'object_type': object_type,
            'object_uuid': object_uuid,
            'state_value': state.value,
            'update_time': state.update_time,
            'message': state.message
        }
        self._trace(
            f'MockMariaDB.set_state({key}): {state.value}')
        return True

    def _mariadb_delete_state(self, object_type: ObjectType,
                              object_uuid: str) -> bool:
        """Mock implementation of mariadb.delete_state()"""
        key = f'{object_type}/{object_uuid}'
        if key in self.mariadb_states:
            del self.mariadb_states[key]
            self._trace(f'MockMariaDB.delete_state({key}): deleted')
        else:
            self._trace(f'MockMariaDB.delete_state({key}): not found')
        return True

    def _mariadb_get_objects_by_state(self, object_type: ObjectType,
                                      state_values: list[str],
                                      updated_before: Optional[float] = None
                                      ) -> list[str]:
        """Mock implementation of mariadb.get_objects_by_state()"""
        result = []
        for key, data in self.mariadb_states.items():
            if (data['object_type'] == object_type and
                    data['state_value'] in state_values):
                if (updated_before and
                        data.get('update_time', 0) >= updated_before):
                    continue
                result.append(data['object_uuid'])
        self._trace(
            f'MockMariaDB.get_objects_by_state({object_type}, '
            f'{state_values}): {result}')
        return result

    def get_cluster_operation_metadata(
            self, op_uuid: str) -> Optional[dict]:
        """Return the metadata dict of a stored cluster operation.

        Tests previously asserted on `/sf/{operation_type}/{uuid}` etcd
        paths; after phase 5, headers live in MariaDB via
        cluster_operations_store. The stored row has the metadata dict
        plus `operation_type` and `created_at` overlaid; this helper
        returns just the metadata-shaped view tests compare against.
        """
        row = self.cluster_operations_store.get(str(op_uuid))
        if row is None:
            return None
        return {
            k: v for k, v in row.items()
            if k not in ('operation_type', 'created_at')
        }

    def get_work_queue_payload(self, queue_name: str) -> Optional[dict]:
        """Return the payload of the most recent work_queue row for a queue.

        Tests previously asserted on `/sf/queue/{queue_name}/{job_name}`
        etcd paths; the new MariaDB-backed queue stores rows in
        work_queue_store and uses an autoincrement id, so per-test queue
        assertions look up the row by queue_name instead.
        """
        match = None
        for row in self.work_queue_store:
            if row['queue_name'] == queue_name:
                match = row
        if match is None:
            return None
        return match['payload']

    def get_mariadb_state(self, object_type: ObjectType,
                          object_uuid: str) -> Optional[dict]:
        """Get state from the mock MariaDB store for test assertions.

        Returns a dict with 'value' and 'update_time' keys, matching the format
        previously used in etcd, or None if no state exists.
        """
        key = f'{object_type}/{object_uuid}'
        if key in self.mariadb_states:
            data = self.mariadb_states[key]
            return {
                'value': data['state_value'],
                'update_time': data['update_time']
            }
        return None

    def get_mariadb_instance_attributes(
            self, object_uuid: str) -> Optional[InstanceAttributesData]:
        """Get instance attributes from the mock MariaDB store for test assertions."""
        key = str(object_uuid)
        return self.instance_attributes.get(key)

    #
    # MariaDB IPAM mock operations
    #

    def _ipam_key(self, ipam_uuid: str, address: str) -> str:
        """Generate a unique key for an IPAM reservation.

        The address can be either a string or an IPv4Address object.
        """
        return f'{ipam_uuid}/{address}'

    def _mariadb_reserve_address(self, reservation: IPAMReservation) -> bool:
        """Mock implementation of mariadb.reserve_address()"""
        key = self._ipam_key(reservation.ipam_uuid, str(reservation.address))
        if key in self.ipam_reservations:
            self._trace(f'MockMariaDB.reserve_address({key}): already exists')
            return False
        self.ipam_reservations[key] = reservation
        self._trace(f'MockMariaDB.reserve_address({key}): success')
        return True

    def _mariadb_release_address(self, ipam_uuid: str, address: str,
                                 halo_reservation: IPAMReservation) -> bool:
        """Mock implementation of mariadb.release_address()"""
        key = self._ipam_key(ipam_uuid, address)
        if key not in self.ipam_reservations:
            self._trace(f'MockMariaDB.release_address({key}): not found')
            return False
        self.ipam_reservations[key] = halo_reservation
        self._trace(f'MockMariaDB.release_address({key}): updated to halo')
        return True

    def _mariadb_get_reservation(self, ipam_uuid: str,
                                 address: str) -> Optional[IPAMReservation]:
        """Mock implementation of mariadb.get_reservation()"""
        key = self._ipam_key(ipam_uuid, address)
        reservation = self.ipam_reservations.get(key)
        self._trace(f'MockMariaDB.get_reservation({key}): {reservation}')
        return reservation

    def _mariadb_get_reservations_for_ipam(
            self, ipam_uuid: str) -> list[IPAMReservation]:
        """Mock implementation of mariadb.get_reservations_for_ipam()"""
        result = []
        prefix = f'{ipam_uuid}/'
        for key, reservation in self.ipam_reservations.items():
            if key.startswith(prefix):
                result.append(reservation)
        self._trace(
            f'MockMariaDB.get_reservations_for_ipam({ipam_uuid}): '
            f'{len(result)} reservations')
        return result

    def _mariadb_delete_reservation(self, ipam_uuid: str, address: str) -> bool:
        """Mock implementation of mariadb.delete_reservation()"""
        key = self._ipam_key(ipam_uuid, address)
        if key in self.ipam_reservations:
            del self.ipam_reservations[key]
            self._trace(f'MockMariaDB.delete_reservation({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_reservation({key}): not found')
        return False

    def _mariadb_delete_reservations_for_ipam(self, ipam_uuid: str) -> int:
        """Mock implementation of mariadb.delete_reservations_for_ipam()"""
        prefix = f'{ipam_uuid}/'
        to_delete = [k for k in self.ipam_reservations if k.startswith(prefix)]
        for key in to_delete:
            del self.ipam_reservations[key]
        self._trace(
            f'MockMariaDB.delete_reservations_for_ipam({ipam_uuid}): '
            f'deleted {len(to_delete)}')
        return len(to_delete)

    def _mariadb_release_haloed_addresses(self, ipam_uuid: str,
                                          older_than: float) -> int:
        """Mock implementation of mariadb.release_haloed_addresses()"""
        from shakenfist.schema.ipam_reservation import ReservationType

        prefix = f'{ipam_uuid}/'
        to_delete = []
        for key, reservation in self.ipam_reservations.items():
            if (key.startswith(prefix) and
                    reservation.reservation_type == ReservationType.DELETION_HALO
                    and reservation.reserved_at < older_than):
                to_delete.append(key)
        for key in to_delete:
            del self.ipam_reservations[key]
        self._trace(
            f'MockMariaDB.release_haloed_addresses({ipam_uuid}, '
            f'{older_than}): deleted {len(to_delete)}')
        return len(to_delete)

    def _mariadb_get_addresses_in_use(self, ipam_uuid: str) -> set[str]:
        """Mock implementation of mariadb.get_addresses_in_use()"""
        result = set()
        prefix = f'{ipam_uuid}/'
        for key in self.ipam_reservations:
            if key.startswith(prefix):
                # Extract address from key (format: ipam_uuid/address)
                address = key[len(prefix):]
                result.add(address)
        self._trace(
            f'MockMariaDB.get_addresses_in_use({ipam_uuid}): {len(result)} '
            f'addresses')
        return result

    def _mariadb_create_dnsmasq(self, data: DnsMasqData) -> bool:
        """Mock implementation of mariadb.create_dnsmasq()"""
        key = str(data.uuid)
        if key in self.dnsmasq_objects:
            self._trace(f'MockMariaDB.create_dnsmasq({key}): already exists')
            return False
        self.dnsmasq_objects[key] = data
        self._trace(f'MockMariaDB.create_dnsmasq({key}): created')
        return True

    def _mariadb_get_dnsmasq(self, dnsmasq_uuid) -> Optional[DnsMasqData]:
        """Mock implementation of mariadb.get_dnsmasq()"""
        key = str(dnsmasq_uuid)
        data = self.dnsmasq_objects.get(key)
        self._trace(f'MockMariaDB.get_dnsmasq({key}): {data}')
        return data

    def _mariadb_get_dnsmasqs(
            self, namespace: Optional[str] = None,
            owner_uuid=None) -> list[DnsMasqData]:
        """Mock implementation of mariadb.get_dnsmasqs()"""
        result = []
        for data in self.dnsmasq_objects.values():
            if namespace and data.namespace != namespace:
                continue
            if owner_uuid and str(data.owner_uuid) != str(owner_uuid):
                continue
            result.append(data)
        self._trace(
            f'MockMariaDB.get_dnsmasqs(namespace={namespace}, '
            f'owner_uuid={owner_uuid}): {len(result)}')
        return result

    def _mariadb_delete_dnsmasq(self, dnsmasq_uuid) -> bool:
        """Mock implementation of mariadb.delete_dnsmasq()"""
        key = str(dnsmasq_uuid)
        if key in self.dnsmasq_objects:
            del self.dnsmasq_objects[key]
            self._trace(f'MockMariaDB.delete_dnsmasq({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_dnsmasq({key}): not found')
        return False

    def _mariadb_update_dnsmasq(self, data: DnsMasqData) -> bool:
        """Mock implementation of mariadb.update_dnsmasq()"""
        key = str(data.uuid)
        if key in self.dnsmasq_objects:
            self.dnsmasq_objects[key] = data
            self._trace(f'MockMariaDB.update_dnsmasq({key}): updated')
            return True
        self._trace(f'MockMariaDB.update_dnsmasq({key}): not found')
        return False

    #
    # MariaDB ObjectReference mock operations
    #

    def _mariadb_get_references_to(
            self, object_type: ObjectType, object_uuid: str,
            relationship: Optional[RelationshipType] = None) -> list:
        """Mock implementation of mariadb.get_references_to()"""
        refs = [
            r for r in self.object_references.values()
            if r.target_object_type == object_type
            and r.target_uuid == str(object_uuid)
            and (relationship is None or r.relationship == relationship)]
        self._trace(
            f'MockMariaDB.get_references_to({object_type}, {object_uuid}, '
            f'{relationship}): {len(refs)} refs')
        return refs

    def _mariadb_get_references_from(
            self, object_type: ObjectType, object_uuid: str,
            relationship: Optional[RelationshipType] = None) -> list:
        """Mock implementation of mariadb.get_references_from()"""
        refs = [
            r for r in self.object_references.values()
            if r.source_object_type == object_type
            and r.source_uuid == str(object_uuid)
            and (relationship is None or r.relationship == relationship)]
        self._trace(
            f'MockMariaDB.get_references_from({object_type}, {object_uuid}, '
            f'{relationship}): {len(refs)} refs')
        return refs

    def _mariadb_record_relationship(
            self, source_type: ObjectType, source_uuid,
            relationship: RelationshipType,
            relationship_value: Optional[str],
            target_type: ObjectType, target_uuid) -> bool:
        """Mock implementation of mariadb.record_relationship()

        Idempotent, like the real implementation. The key deliberately
        excludes relationship_value, matching the real table's primary
        key: an upsert against an existing row refreshes last_active
        but does not change the stored relationship_value.
        """
        key = (source_type, str(source_uuid), relationship,
               target_type, str(target_uuid))
        if key not in self.object_references:
            self.object_references[key] = ObjectReference(
                source_object_type=source_type,
                source_uuid=str(source_uuid),
                relationship=relationship,
                relationship_value=relationship_value,
                target_object_type=target_type,
                target_uuid=str(target_uuid),
                created=time.time(),
                last_active=time.time())
        else:
            self.object_references[key].last_active = time.time()
        self._trace(
            f'MockMariaDB.record_relationship({key}): recorded')
        return True

    def _mariadb_remove_relationship(
            self, source_type: ObjectType, source_uuid,
            relationship: RelationshipType,
            relationship_value: Optional[str],
            target_type: ObjectType, target_uuid) -> bool:
        """Mock implementation of mariadb.remove_relationship()

        Like the real DELETE, the row is only removed if its stored
        relationship_value matches the argument, and the return value
        is True whether or not a row was removed (False only means a
        database error).
        """
        key = (source_type, str(source_uuid), relationship,
               target_type, str(target_uuid))
        existing = self.object_references.get(key)
        removed = False
        if (existing is not None and
                existing.relationship_value == relationship_value):
            del self.object_references[key]
            removed = True
        self._trace(
            f'MockMariaDB.remove_relationship({key}): removed={removed}')
        return True

    def _mariadb_remove_all_references_from(
            self, object_type: ObjectType, object_uuid: str,
            relationship: Optional[RelationshipType] = None) -> int:
        """Mock implementation of mariadb.remove_all_references_from()"""
        doomed = [
            key for key, r in self.object_references.items()
            if r.source_object_type == object_type
            and r.source_uuid == str(object_uuid)
            and (relationship is None or r.relationship == relationship)]
        for key in doomed:
            del self.object_references[key]
        self._trace(
            f'MockMariaDB.remove_all_references_from({object_type}, '
            f'{object_uuid}, {relationship}): {len(doomed)}')
        return len(doomed)

    #
    # MariaDB Node mock operations
    #

    def _mariadb_create_node(self, node_uuid, fqdn,
                             ip, version) -> bool:
        """Mock implementation of mariadb.create_node()"""
        import uuid as uuid_mod
        key = str(node_uuid)
        if key in self.node_objects:
            self._trace(
                f'MockMariaDB.create_node({key}): exists')
            return False
        if isinstance(node_uuid, str):
            node_uuid = uuid_mod.UUID(node_uuid)
        data = NodeData(
            uuid=node_uuid, fqdn=fqdn,
            ip=ip, version=version)
        self.node_objects[key] = data
        self._trace(
            f'MockMariaDB.create_node({key}): created')
        return True

    def _mariadb_get_node(self, node_uuid
                          ) -> Optional[NodeData]:
        """Mock implementation of mariadb.get_node()"""
        key = str(node_uuid)
        data = self.node_objects.get(key)
        self._trace(
            f'MockMariaDB.get_node({key}): {data}')
        return data

    def _mariadb_get_node_by_fqdn(
            self, fqdn) -> Optional[NodeData]:
        """Mock implementation of
        mariadb.get_node_by_fqdn()"""
        for data in self.node_objects.values():
            if data.fqdn == fqdn:
                self._trace(
                    f'MockMariaDB.get_node_by_fqdn'
                    f'({fqdn}): {data}')
                return data
        self._trace(
            f'MockMariaDB.get_node_by_fqdn'
            f'({fqdn}): None')
        return None

    def _mariadb_get_all_node_uuids(self) -> list[str]:
        """Mock implementation of
        mariadb.get_all_node_uuids()"""
        result = list(self.node_objects.keys())
        self._trace(
            f'MockMariaDB.get_all_node_uuids(): '
            f'{result}')
        return result

    def _mariadb_update_node(self, data: NodeData
                             ) -> bool:
        """Mock implementation of
        mariadb.update_node()"""
        key = str(data.uuid)
        if key in self.node_objects:
            self.node_objects[key] = data
            self._trace(
                f'MockMariaDB.update_node({key}): '
                f'updated')
            return True
        self._trace(
            f'MockMariaDB.update_node({key}): '
            f'not found')
        return False

    def _mariadb_delete_node(self, node_uuid) -> bool:
        """Mock implementation of
        mariadb.delete_node()"""
        key = str(node_uuid)
        if key in self.node_objects:
            del self.node_objects[key]
            self._trace(
                f'MockMariaDB.delete_node({key}): '
                f'deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_node({key}): '
            f'not found')
        return False

    def _mariadb_create_node_attributes(
            self, data: NodeAttributesData) -> bool:
        """Mock implementation of
        mariadb.create_node_attributes()"""
        key = str(data.uuid)
        if key in self.node_attributes:
            self._trace(
                f'MockMariaDB.create_node_attributes'
                f'({key}): exists')
            return False
        self.node_attributes[key] = data
        self._trace(
            f'MockMariaDB.create_node_attributes'
            f'({key}): created')
        return True

    def _mariadb_get_node_attributes(
            self, node_uuid
    ) -> Optional[NodeAttributesData]:
        """Mock implementation of
        mariadb.get_node_attributes()"""
        key = str(node_uuid)
        data = self.node_attributes.get(key)
        self._trace(
            f'MockMariaDB.get_node_attributes'
            f'({key}): {data}')
        return data

    def _mariadb_update_node_attributes(
            self, data: NodeAttributesData,
            fields: Optional[List[str]] = None) -> bool:
        """Mock implementation of
        mariadb.update_node_attributes()

        Like the real implementation, a fields mask limits the write
        to the named model fields; None or empty replaces every field.
        The masked path copies onto the stored object so writes to
        other fields by concurrent callers are preserved, mirroring
        the per-column SQL UPDATE.
        """
        key = str(data.uuid)
        if key in self.node_attributes:
            if fields:
                stored = self.node_attributes[key]
                for field in fields:
                    setattr(stored, field, getattr(data, field))
            else:
                self.node_attributes[key] = data
            self._trace(
                f'MockMariaDB.update_node_attributes'
                f'({key}): updated (fields={fields})')
            return True
        self._trace(
            f'MockMariaDB.update_node_attributes'
            f'({key}): not found')
        return False

    def _mariadb_delete_node_attributes(
            self, node_uuid) -> bool:
        """Mock implementation of
        mariadb.delete_node_attributes()"""
        key = str(node_uuid)
        if key in self.node_attributes:
            del self.node_attributes[key]
            self._trace(
                f'MockMariaDB.delete_node_attributes'
                f'({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_node_attributes'
            f'({key}): not found')
        return False

    #
    # MariaDB Namespace mock operations
    #

    def _mariadb_create_namespace(self, name, version) -> bool:
        """Mock implementation of mariadb.create_namespace()"""
        if name in self.namespace_objects:
            self._trace(f'MockMariaDB.create_namespace({name}): exists')
            return False
        data = NamespaceData(name=name, version=version)
        self.namespace_objects[name] = data
        self._trace(f'MockMariaDB.create_namespace({name}): created')
        return True

    def _mariadb_get_namespace(self, name) -> Optional[NamespaceData]:
        """Mock implementation of mariadb.get_namespace()"""
        data = self.namespace_objects.get(name)
        self._trace(f'MockMariaDB.get_namespace({name}): {data}')
        return data

    def _mariadb_get_all_namespace_names(self) -> list[str]:
        """Mock implementation of mariadb.get_all_namespace_names()"""
        result = sorted(self.namespace_objects.keys())
        self._trace(f'MockMariaDB.get_all_namespace_names(): {result}')
        return result

    def _mariadb_delete_namespace(self, name) -> bool:
        """Mock implementation of mariadb.delete_namespace()"""
        if name in self.namespace_objects:
            del self.namespace_objects[name]
            self._trace(f'MockMariaDB.delete_namespace({name}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_namespace({name}): not found')
        return False

    def _mariadb_create_namespace_attributes(self, data: NamespaceAttributesData) -> bool:
        """Mock implementation of mariadb.create_namespace_attributes()"""
        if data.name in self.namespace_attributes:
            self._trace(f'MockMariaDB.create_namespace_attributes({data.name}): exists')
            return False
        self.namespace_attributes[data.name] = data
        self._trace(f'MockMariaDB.create_namespace_attributes({data.name}): created')
        return True

    def _mariadb_get_namespace_attributes(self, name) -> Optional[NamespaceAttributesData]:
        """Mock implementation of mariadb.get_namespace_attributes()"""
        data = self.namespace_attributes.get(name)
        self._trace(f'MockMariaDB.get_namespace_attributes({name}): {data}')
        return data

    def _mariadb_update_namespace_attributes(
            self, data: NamespaceAttributesData,
            fields: Optional[List[str]] = None) -> bool:
        """Mock implementation of mariadb.update_namespace_attributes()

        Like the real implementation, a fields mask limits the write
        to the named model fields; None or empty replaces every field.
        """
        if data.name in self.namespace_attributes:
            if fields:
                stored = self.namespace_attributes[data.name]
                for field in fields:
                    setattr(stored, field, getattr(data, field))
            else:
                self.namespace_attributes[data.name] = data
            self._trace(
                f'MockMariaDB.update_namespace_attributes({data.name}): '
                f'updated (fields={fields})')
            return True
        self._trace(f'MockMariaDB.update_namespace_attributes({data.name}): not found')
        return False

    def _mariadb_delete_namespace_attributes(self, name) -> bool:
        """Mock implementation of mariadb.delete_namespace_attributes()"""
        if name in self.namespace_attributes:
            del self.namespace_attributes[name]
            self._trace(f'MockMariaDB.delete_namespace_attributes({name}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_namespace_attributes({name}): not found')
        return False

    # MariaDB NamespaceKey mock implementations

    def legacy_namespace_keys_view(self, namespace):
        """Render one namespace's keys in the pre-phase-2 JSON shape.

        See _NamespaceAttributesStore. Expired keys are included, as
        they always were in storage -- the expiry filter was and is
        applied at read time by the accessors, not here.
        """
        nonced_keys = {}
        for data in self.namespace_key_objects.values():
            if data.namespace != namespace:
                continue
            attrs = self.namespace_key_attributes.get(str(data.uuid))
            if not attrs:
                continue
            entry = {'key': attrs.key, 'nonce': attrs.nonce}
            if attrs.expiry is not None:
                entry['expiry'] = attrs.expiry
            nonced_keys[data.name] = entry
        return {'nonced_keys': nonced_keys}

    def _mariadb_create_namespace_key(self, data: NamespaceKeyData) -> bool:
        """Mock implementation of mariadb.create_namespace_key()

        The real table has a UNIQUE index on (namespace, name) as well
        as the uuid primary key, so both collisions fail here too.
        """
        key = str(data.uuid)
        if key in self.namespace_key_objects:
            self._trace(
                f'MockMariaDB.create_namespace_key({key}): already exists')
            return False
        for existing in self.namespace_key_objects.values():
            if (existing.namespace == data.namespace
                    and existing.name == data.name):
                self._trace(
                    f'MockMariaDB.create_namespace_key({key}): '
                    f'duplicate {data.namespace}:{data.name}')
                return False
        self.namespace_key_objects[key] = data
        self._trace(f'MockMariaDB.create_namespace_key({key}): created')
        return True

    def _mariadb_get_namespace_key(
            self, key_uuid) -> Optional[NamespaceKeyData]:
        """Mock implementation of mariadb.get_namespace_key()"""
        key = str(key_uuid)
        data = self.namespace_key_objects.get(key)
        self._trace(f'MockMariaDB.get_namespace_key({key}): {data}')
        return data

    def _mariadb_get_namespace_key_by_name(self, namespace, name):
        """Mock implementation of mariadb.get_namespace_key_by_name()

        Returns the joined (static, attributes) pair, or None. Expired
        keys are returned -- expiry is check-at-use in the caller.
        """
        for data in self.namespace_key_objects.values():
            if data.namespace == namespace and data.name == name:
                attrs = self.namespace_key_attributes.get(str(data.uuid))
                if not attrs:
                    continue
                self._trace(
                    f'MockMariaDB.get_namespace_key_by_name'
                    f'({namespace}, {name}): {data.uuid}')
                return (data, attrs)
        self._trace(
            f'MockMariaDB.get_namespace_key_by_name'
            f'({namespace}, {name}): not found')
        return None

    def _mariadb_find_namespace_keys(
            self, namespace, include_expired=False, now=None):
        """Mock implementation of mariadb.find_namespace_keys()

        Returns joined (static, attributes) pairs for one namespace,
        applying the same expiry filter the real accessor pushes into
        SQL.
        """
        if now is None:
            now = time.time()

        results = []
        for data in self.namespace_key_objects.values():
            if data.namespace != namespace:
                continue
            attrs = self.namespace_key_attributes.get(str(data.uuid))
            if not attrs:
                continue
            if not include_expired:
                if attrs.expiry is not None and attrs.expiry <= now:
                    continue
            results.append((data, attrs))

        self._trace(
            f'MockMariaDB.find_namespace_keys({namespace}, '
            f'include_expired={include_expired}): {len(results)} results')
        return results

    def _mariadb_delete_namespace_key(self, key_uuid) -> bool:
        """Mock implementation of mariadb.delete_namespace_key()"""
        key = str(key_uuid)
        if key in self.namespace_key_objects:
            del self.namespace_key_objects[key]
            self._trace(f'MockMariaDB.delete_namespace_key({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_namespace_key({key}): not found')
        return False

    def _mariadb_delete_expired_namespace_keys(self, older_than) -> int:
        """Mock implementation of mariadb.delete_expired_namespace_keys()"""
        doomed = [
            key for key, data in self.namespace_key_objects.items()
            if (self.namespace_key_attributes.get(key) is not None
                and self.namespace_key_attributes[key].expiry is not None
                and self.namespace_key_attributes[key].expiry < older_than)
        ]
        for key in doomed:
            del self.namespace_key_objects[key]
            del self.namespace_key_attributes[key]
        self._trace(
            f'MockMariaDB.delete_expired_namespace_keys({older_than}): '
            f'{len(doomed)} deleted')
        return len(doomed)

    def _mariadb_create_namespace_key_attributes(
            self, data: NamespaceKeyAttributesData) -> bool:
        """Mock implementation of mariadb.create_namespace_key_attributes()"""
        key = str(data.uuid)
        if key in self.namespace_key_attributes:
            self._trace(
                f'MockMariaDB.create_namespace_key_attributes({key}): '
                f'already exists')
            return False
        self.namespace_key_attributes[key] = data
        self._trace(
            f'MockMariaDB.create_namespace_key_attributes({key}): created')
        return True

    def _mariadb_get_namespace_key_attributes(
            self, key_uuid) -> Optional[NamespaceKeyAttributesData]:
        """Mock implementation of mariadb.get_namespace_key_attributes()"""
        key = str(key_uuid)
        data = self.namespace_key_attributes.get(key)
        self._trace(
            f'MockMariaDB.get_namespace_key_attributes({key}): {data}')
        return data

    def _mariadb_update_namespace_key_attributes(
            self, data: NamespaceKeyAttributesData) -> bool:
        """Mock implementation of mariadb.update_namespace_key_attributes()"""
        key = str(data.uuid)
        if key in self.namespace_key_attributes:
            self.namespace_key_attributes[key] = data
            self._trace(
                f'MockMariaDB.update_namespace_key_attributes({key}): updated')
            return True
        self._trace(
            f'MockMariaDB.update_namespace_key_attributes({key}): not found')
        return False

    def _mariadb_delete_namespace_key_attributes(self, key_uuid) -> bool:
        """Mock implementation of mariadb.delete_namespace_key_attributes()"""
        key = str(key_uuid)
        if key in self.namespace_key_attributes:
            del self.namespace_key_attributes[key]
            self._trace(
                f'MockMariaDB.delete_namespace_key_attributes({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_namespace_key_attributes({key}): not found')
        return False

    # MariaDB Artifact mock implementations

    def _mariadb_create_artifact(self, artifact_uuid, artifact_type,
                                 source_url, name, namespace, version) -> bool:
        """Mock implementation of mariadb.create_artifact()"""
        key = str(artifact_uuid)
        if key in self.artifact_objects:
            return False
        self.artifact_objects[key] = ArtifactData(
            uuid=artifact_uuid, artifact_type=artifact_type,
            source_url=source_url, name=name,
            namespace=namespace, version=version)
        self._trace(f'MockMariaDB.create_artifact({key})')
        return True

    def _mariadb_get_artifact(self, artifact_uuid) -> Optional[ArtifactData]:
        """Mock implementation of mariadb.get_artifact()"""
        key = str(artifact_uuid)
        data = self.artifact_objects.get(key)
        self._trace(f'MockMariaDB.get_artifact({key}): {data}')
        return data

    def _mariadb_get_all_artifacts(self) -> list[ArtifactData]:
        """Mock implementation of mariadb.get_all_artifacts()"""
        self._trace(
            f'MockMariaDB.get_all_artifacts(): '
            f'{len(self.artifact_objects)} results')
        return list(self.artifact_objects.values())

    def _mariadb_find_artifacts(
            self, criteria: ObjectFilterCriteria) -> list[ArtifactData]:
        """Mock implementation of mariadb.find_artifacts().

        Honours criteria.states by cross-referencing mariadb_states,
        matching the real SQL JOIN against object_states. Namespace
        and name filters are applied in Python.
        """
        results = list(self.artifact_objects.values())
        if criteria.states:
            matching = {
                d['object_uuid']
                for d in self.mariadb_states.values()
                if (d['object_type'] == ObjectType.ARTIFACT
                    and d['state_value'] in criteria.states)
            }
            results = [d for d in results if str(d.uuid) in matching]
        if criteria.namespace is not None:
            results = [d for d in results if d.namespace == criteria.namespace]
        if criteria.name is not None:
            results = [d for d in results if d.name == criteria.name]
        self._trace(
            f'MockMariaDB.find_artifacts(criteria={criteria!r}): '
            f'{len(results)} results')
        return results

    def _mariadb_update_artifact(self, data: ArtifactData) -> bool:
        """Mock implementation of mariadb.update_artifact()"""
        key = str(data.uuid)
        if key in self.artifact_objects:
            self.artifact_objects[key] = data
            self._trace(f'MockMariaDB.update_artifact({key}): updated')
            return True
        self._trace(f'MockMariaDB.update_artifact({key}): not found')
        return False

    def _mariadb_delete_artifact(self, artifact_uuid) -> bool:
        """Mock implementation of mariadb.delete_artifact()"""
        key = str(artifact_uuid)
        if key in self.artifact_objects:
            del self.artifact_objects[key]
            self._trace(f'MockMariaDB.delete_artifact({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_artifact({key}): not found')
        return False

    def _mariadb_create_artifact_attributes(
            self, data: ArtifactAttributesData) -> bool:
        """Mock implementation of mariadb.create_artifact_attributes()"""
        key = str(data.uuid)
        if key in self.artifact_attributes:
            self._trace(
                f'MockMariaDB.create_artifact_attributes({key}): exists')
            return False
        self.artifact_attributes[key] = data
        self._trace(
            f'MockMariaDB.create_artifact_attributes({key}): created')
        return True

    def _mariadb_get_artifact_attributes(
            self, artifact_uuid) -> Optional[ArtifactAttributesData]:
        """Mock implementation of mariadb.get_artifact_attributes()"""
        key = str(artifact_uuid)
        data = self.artifact_attributes.get(key)
        self._trace(
            f'MockMariaDB.get_artifact_attributes({key}): {data}')
        return data

    def _mariadb_update_artifact_attributes(
            self, data: ArtifactAttributesData,
            fields: Optional[List[str]] = None) -> bool:
        """Mock implementation of mariadb.update_artifact_attributes()

        A fields mask limits the write to the named model fields,
        mirroring the per-column SQL UPDATE.
        """
        key = str(data.uuid)
        if key in self.artifact_attributes:
            if fields:
                stored = self.artifact_attributes[key]
                for field in fields:
                    setattr(stored, field, getattr(data, field))
            else:
                self.artifact_attributes[key] = data
            self._trace(
                f'MockMariaDB.update_artifact_attributes({key}): '
                f'updated (fields={fields})')
            return True
        self._trace(
            f'MockMariaDB.update_artifact_attributes({key}): not found')
        return False

    def _mariadb_delete_artifact_attributes(self, artifact_uuid) -> bool:
        """Mock implementation of mariadb.delete_artifact_attributes()"""
        key = str(artifact_uuid)
        if key in self.artifact_attributes:
            del self.artifact_attributes[key]
            self._trace(
                f'MockMariaDB.delete_artifact_attributes({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_artifact_attributes({key}): not found')
        return False

    def _mariadb_create_artifact_index(self, artifact_uuid,
                                       index_number, blob_uuid) -> bool:
        """Mock implementation of mariadb.create_artifact_index()"""
        key = (str(artifact_uuid), index_number)
        if key in self.artifact_indexes:
            self._trace(
                f'MockMariaDB.create_artifact_index({key}): exists')
            return False
        self.artifact_indexes[key] = ArtifactIndexData(
            artifact_uuid=artifact_uuid, index_number=index_number,
            blob_uuid=blob_uuid)
        self._trace(
            f'MockMariaDB.create_artifact_index({key}): created')
        return True

    def _mariadb_get_artifact_index(self, artifact_uuid,
                                    index_number) -> Optional[ArtifactIndexData]:
        """Mock implementation of mariadb.get_artifact_index()"""
        key = (str(artifact_uuid), index_number)
        data = self.artifact_indexes.get(key)
        self._trace(
            f'MockMariaDB.get_artifact_index({key}): {data}')
        return data

    def _mariadb_get_all_artifact_indexes(
            self, artifact_uuid) -> list[ArtifactIndexData]:
        """Mock implementation of mariadb.get_all_artifact_indexes()"""
        prefix = str(artifact_uuid)
        result = [v for k, v in self.artifact_indexes.items()
                  if k[0] == prefix]
        self._trace(
            f'MockMariaDB.get_all_artifact_indexes({prefix}): '
            f'{len(result)} results')
        return result

    def _mariadb_delete_artifact_index(self, artifact_uuid,
                                       index_number) -> bool:
        """Mock implementation of mariadb.delete_artifact_index()"""
        key = (str(artifact_uuid), index_number)
        if key in self.artifact_indexes:
            del self.artifact_indexes[key]
            self._trace(
                f'MockMariaDB.delete_artifact_index({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_artifact_index({key}): not found')
        return False

    def _mariadb_delete_all_artifact_indexes(self, artifact_uuid) -> int:
        """Mock implementation of mariadb.delete_all_artifact_indexes()"""
        prefix = str(artifact_uuid)
        to_delete = [k for k in self.artifact_indexes if k[0] == prefix]
        for k in to_delete:
            del self.artifact_indexes[k]
        self._trace(
            f'MockMariaDB.delete_all_artifact_indexes({prefix}): '
            f'{len(to_delete)} deleted')
        return len(to_delete)

    #
    # MariaDB NetworkInterface mock operations
    #

    def _mariadb_create_network_interface(self, data: NetworkInterfaceData) -> bool:
        """Mock implementation of mariadb.create_network_interface()"""
        key = str(data.uuid)
        if key in self.network_interface_objects:
            self._trace(f'MockMariaDB.create_network_interface({key}): already exists')
            return False
        self.network_interface_objects[key] = data
        self._trace(f'MockMariaDB.create_network_interface({key}): created')
        return True

    def _mariadb_get_network_interface(
            self, ni_uuid) -> Optional[NetworkInterfaceData]:
        """Mock implementation of mariadb.get_network_interface()"""
        key = str(ni_uuid)
        data = self.network_interface_objects.get(key)
        self._trace(f'MockMariaDB.get_network_interface({key}): {data}')
        return data

    def _mariadb_delete_network_interface(self, ni_uuid) -> bool:
        """Mock implementation of mariadb.delete_network_interface()"""
        key = str(ni_uuid)
        if key in self.network_interface_objects:
            del self.network_interface_objects[key]
            self._trace(f'MockMariaDB.delete_network_interface({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_network_interface({key}): not found')
        return False

    def _mariadb_update_network_interface(self, data: NetworkInterfaceData) -> bool:
        """Mock implementation of mariadb.update_network_interface()"""
        key = str(data.uuid)
        if key in self.network_interface_objects:
            self.network_interface_objects[key] = data
            self._trace(f'MockMariaDB.update_network_interface({key}): updated')
            return True
        self._trace(f'MockMariaDB.update_network_interface({key}): not found')
        return False

    def _mariadb_find_network_interfaces(
            self, criteria: ObjectFilterCriteria) -> list[NetworkInterfaceData]:
        """Mock implementation of mariadb.find_network_interfaces().

        Honours criteria.states by cross-referencing mariadb_states,
        matching the real SQL JOIN against object_states. Honours
        criteria.network_uuid and criteria.instance_uuid against the
        corresponding indexed columns on network_interfaces. Namespace
        and name are silently ignored because network_interfaces has
        neither column.
        """
        results = list(self.network_interface_objects.values())
        if criteria.states:
            matching = {
                d['object_uuid']
                for d in self.mariadb_states.values()
                if (d['object_type'] == ObjectType.INTERFACE
                    and d['state_value'] in criteria.states)
            }
            results = [d for d in results if str(d.uuid) in matching]
        if criteria.network_uuid is not None:
            results = [
                d for d in results
                if str(d.network_uuid) == criteria.network_uuid]
        if criteria.instance_uuid is not None:
            results = [
                d for d in results
                if str(d.instance_uuid) == criteria.instance_uuid]
        # criteria.namespace and criteria.name are silently ignored:
        # network_interfaces has neither column (consistent with the
        # real _direct_find_network_interfaces behaviour).
        self._trace(
            f'MockMariaDB.find_network_interfaces(criteria={criteria!r}): '
            f'{len(results)} results')
        return results

    def _mariadb_create_network_interface_attributes(
            self, data: NetworkInterfaceAttributesData) -> bool:
        """Mock implementation of mariadb.create_network_interface_attributes()"""
        key = str(data.uuid)
        if key in self.network_interface_attributes:
            self._trace(
                f'MockMariaDB.create_network_interface_attributes({key}): already exists')
            return False
        self.network_interface_attributes[key] = data
        self._trace(
            f'MockMariaDB.create_network_interface_attributes({key}): created')
        return True

    def _mariadb_get_network_interface_attributes(
            self, ni_uuid) -> Optional[NetworkInterfaceAttributesData]:
        """Mock implementation of mariadb.get_network_interface_attributes()"""
        key = str(ni_uuid)
        data = self.network_interface_attributes.get(key)
        self._trace(
            f'MockMariaDB.get_network_interface_attributes({key}): {data}')
        return data

    def _mariadb_update_network_interface_attributes(
            self, data: NetworkInterfaceAttributesData) -> bool:
        """Mock implementation of mariadb.update_network_interface_attributes()"""
        key = str(data.uuid)
        if key in self.network_interface_attributes:
            self.network_interface_attributes[key] = data
            self._trace(
                f'MockMariaDB.update_network_interface_attributes({key}): updated')
            return True
        self._trace(
            f'MockMariaDB.update_network_interface_attributes({key}): not found')
        return False

    def _mariadb_delete_network_interface_attributes(self, ni_uuid) -> bool:
        """Mock implementation of mariadb.delete_network_interface_attributes()"""
        key = str(ni_uuid)
        if key in self.network_interface_attributes:
            del self.network_interface_attributes[key]
            self._trace(
                f'MockMariaDB.delete_network_interface_attributes({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_network_interface_attributes({key}): not found')
        return False

    def _mariadb_create_network(self, data: NetworkData) -> bool:
        """Mock implementation of mariadb.create_network()"""
        key = str(data.uuid)
        if key in self.network_objects:
            self._trace(f'MockMariaDB.create_network({key}): already exists')
            return False
        self.network_objects[key] = data
        self._trace(f'MockMariaDB.create_network({key}): created')
        return True

    def _mariadb_get_network(self, net_uuid) -> Optional[NetworkData]:
        """Mock implementation of mariadb.get_network()"""
        key = str(net_uuid)
        data = self.network_objects.get(key)
        self._trace(f'MockMariaDB.get_network({key}): {data}')
        return data

    def _mariadb_get_all_networks(self) -> list[NetworkData]:
        """Mock implementation of mariadb.get_all_networks()"""
        self._trace('MockMariaDB.get_all_networks()')
        return list(self.network_objects.values())

    def _mariadb_find_network_vxids(self, vxids: list[int]) -> dict[int, str]:
        """Mock implementation of mariadb.find_network_vxids()"""
        self._trace(f'MockMariaDB.find_network_vxids({vxids})')
        wanted = set(vxids)
        return {
            d.vxid: str(d.uuid) for d in self.network_objects.values()
            if d.vxid in wanted
        }

    def _mariadb_find_networks(
            self, criteria: ObjectFilterCriteria) -> list[NetworkData]:
        """Mock implementation of mariadb.find_networks().

        Honours criteria.states by cross-referencing mariadb_states,
        matching the real SQL JOIN against object_states. Namespace
        and name filters are applied in Python. NetworkData.namespace
        is Optional[str], so a non-None criteria.namespace excludes
        networks whose stored namespace is None (matches SQL NULL
        semantics).
        """
        results = list(self.network_objects.values())
        if criteria.states:
            matching = {
                d['object_uuid']
                for d in self.mariadb_states.values()
                if (d['object_type'] == ObjectType.NETWORK
                    and d['state_value'] in criteria.states)
            }
            results = [d for d in results if str(d.uuid) in matching]
        if criteria.namespace is not None:
            results = [d for d in results if d.namespace == criteria.namespace]
        if criteria.name is not None:
            results = [d for d in results if d.name == criteria.name]
        self._trace(
            f'MockMariaDB.find_networks(criteria={criteria!r}): '
            f'{len(results)} results')
        return results

    def _mariadb_delete_network(self, net_uuid) -> bool:
        """Mock implementation of mariadb.delete_network()"""
        key = str(net_uuid)
        if key in self.network_objects:
            del self.network_objects[key]
            self._trace(f'MockMariaDB.delete_network({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_network({key}): not found')
        return False

    def _mariadb_create_network_attributes(
            self, data: NetworkAttributesData) -> bool:
        """Mock implementation of mariadb.create_network_attributes()"""
        key = str(data.uuid)
        if key in self.network_attributes:
            self._trace(
                f'MockMariaDB.create_network_attributes({key}): '
                f'already exists')
            return False
        self.network_attributes[key] = data
        self._trace(
            f'MockMariaDB.create_network_attributes({key}): created')
        return True

    def _mariadb_get_network_attributes(
            self, net_uuid) -> Optional[NetworkAttributesData]:
        """Mock implementation of mariadb.get_network_attributes()"""
        key = str(net_uuid)
        data = self.network_attributes.get(key)
        self._trace(
            f'MockMariaDB.get_network_attributes({key}): {data}')
        return data

    def _mariadb_update_network_attributes(
            self, data: NetworkAttributesData,
            fields: Optional[List[str]] = None) -> bool:
        """Mock implementation of mariadb.update_network_attributes()

        A fields mask limits the write to the named model fields,
        mirroring the per-column SQL UPDATE.
        """
        key = str(data.uuid)
        if key in self.network_attributes:
            if fields:
                stored = self.network_attributes[key]
                for field in fields:
                    setattr(stored, field, getattr(data, field))
            else:
                self.network_attributes[key] = data
            self._trace(
                f'MockMariaDB.update_network_attributes({key}): '
                f'updated (fields={fields})')
            return True
        self._trace(
            f'MockMariaDB.update_network_attributes({key}): '
            f'not found')
        return False

    def _mariadb_delete_network_attributes(self, net_uuid) -> bool:
        """Mock implementation of mariadb.delete_network_attributes()"""
        key = str(net_uuid)
        if key in self.network_attributes:
            del self.network_attributes[key]
            self._trace(
                f'MockMariaDB.delete_network_attributes({key}): '
                f'deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_network_attributes({key}): '
            f'not found')
        return False

    def _mariadb_create_ipam(self, data: IPAMData) -> bool:
        """Mock implementation of mariadb.create_ipam()"""
        key = str(data.uuid)
        if key in self.ipam_objects:
            self._trace(f'MockMariaDB.create_ipam({key}): already exists')
            return False
        self.ipam_objects[key] = data
        self._trace(f'MockMariaDB.create_ipam({key}): created')
        return True

    def _mariadb_get_ipam(self, ipam_uuid) -> Optional[IPAMData]:
        """Mock implementation of mariadb.get_ipam()"""
        key = str(ipam_uuid)
        data = self.ipam_objects.get(key)
        self._trace(f'MockMariaDB.get_ipam({key}): {data}')
        return data

    def _mariadb_delete_ipam(self, ipam_uuid) -> bool:
        """Mock implementation of mariadb.delete_ipam()"""
        key = str(ipam_uuid)
        if key in self.ipam_objects:
            del self.ipam_objects[key]
            self._trace(f'MockMariaDB.delete_ipam({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_ipam({key}): not found')
        return False

    def _mariadb_update_ipam(self, data: IPAMData) -> bool:
        """Mock implementation of mariadb.update_ipam()"""
        key = str(data.uuid)
        if key in self.ipam_objects:
            self.ipam_objects[key] = data
            self._trace(f'MockMariaDB.update_ipam({key}): updated')
            return True
        self._trace(f'MockMariaDB.update_ipam({key}): not found')
        return False

    def _mariadb_create_agent_operation(
            self, data: AgentOperationData) -> bool:
        """Mock implementation of mariadb.create_agent_operation()"""
        key = str(data.uuid)
        if key in self.agent_operation_objects:
            self._trace(
                f'MockMariaDB.create_agent_operation({key}): '
                f'already exists')
            return False
        self.agent_operation_objects[key] = data
        self._trace(
            f'MockMariaDB.create_agent_operation({key}): created')
        return True

    def _mariadb_get_agent_operation(
            self, aop_uuid) -> Optional[AgentOperationData]:
        """Mock implementation of mariadb.get_agent_operation()"""
        key = str(aop_uuid)
        data = self.agent_operation_objects.get(key)
        self._trace(
            f'MockMariaDB.get_agent_operation({key}): {data}')
        return data

    def _mariadb_delete_agent_operation(self, aop_uuid) -> bool:
        """Mock implementation of mariadb.delete_agent_operation()"""
        key = str(aop_uuid)
        if key in self.agent_operation_objects:
            del self.agent_operation_objects[key]
            self._trace(
                f'MockMariaDB.delete_agent_operation({key}): '
                f'deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_agent_operation({key}): '
            f'not found')
        return False

    def _mariadb_create_agent_operation_attributes(
            self, data: AgentOperationAttributesData) -> bool:
        """Mock implementation of mariadb.create_agent_operation_attributes()"""
        key = str(data.uuid)
        if key in self.agent_operation_attributes:
            self._trace(
                f'MockMariaDB.create_agent_operation_attributes'
                f'({key}): already exists')
            return False
        self.agent_operation_attributes[key] = data
        self._trace(
            f'MockMariaDB.create_agent_operation_attributes'
            f'({key}): created')
        return True

    def _mariadb_get_agent_operation_attributes(
            self, aop_uuid
    ) -> Optional[AgentOperationAttributesData]:
        """Mock implementation of mariadb.get_agent_operation_attributes()"""
        key = str(aop_uuid)
        data = self.agent_operation_attributes.get(key)
        self._trace(
            f'MockMariaDB.get_agent_operation_attributes'
            f'({key}): {data}')
        return data

    def _mariadb_update_agent_operation_attributes(
            self, data: AgentOperationAttributesData,
            fields: Optional[List[str]] = None) -> bool:
        """Mock implementation of mariadb.update_agent_operation_attributes()

        A fields mask limits the write to the named model fields,
        mirroring the per-column SQL UPDATE.
        """
        key = str(data.uuid)
        if key in self.agent_operation_attributes:
            if fields:
                stored = self.agent_operation_attributes[key]
                for field in fields:
                    setattr(stored, field, getattr(data, field))
            else:
                self.agent_operation_attributes[key] = data
            self._trace(
                f'MockMariaDB.update_agent_operation_attributes'
                f'({key}): updated (fields={fields})')
            return True
        self._trace(
            f'MockMariaDB.update_agent_operation_attributes'
            f'({key}): not found')
        return False

    def _mariadb_delete_agent_operation_attributes(
            self, aop_uuid) -> bool:
        """Mock implementation of mariadb.delete_agent_operation_attributes()"""
        key = str(aop_uuid)
        if key in self.agent_operation_attributes:
            del self.agent_operation_attributes[key]
            self._trace(
                f'MockMariaDB.delete_agent_operation_attributes'
                f'({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_agent_operation_attributes'
            f'({key}): not found')
        return False

    # MariaDB Instance mock operations

    def _mariadb_create_instance(self, data: InstanceData) -> bool:
        """Mock implementation of mariadb.create_instance()"""
        key = str(data.uuid)
        if key in self.instance_objects:
            self._trace(
                f'MockMariaDB.create_instance({key}): already exists')
            return False
        self.instance_objects[key] = data
        self._trace(f'MockMariaDB.create_instance({key}): created')
        return True

    def _mariadb_get_instance(
            self, inst_uuid) -> Optional[InstanceData]:
        """Mock implementation of mariadb.get_instance()"""
        key = str(inst_uuid)
        data = self.instance_objects.get(key)
        self._trace(f'MockMariaDB.get_instance({key}): {data}')
        return data

    def _mariadb_get_all_instances(self) -> list[InstanceData]:
        """Mock implementation of mariadb.get_all_instances()"""
        self._trace('MockMariaDB.get_all_instances()')
        return list(self.instance_objects.values())

    def _mariadb_find_instances(
            self, criteria: ObjectFilterCriteria) -> list[InstanceData]:
        """Mock implementation of mariadb.find_instances().

        Honours criteria.states by cross-referencing mariadb_states,
        matching the real SQL JOIN against object_states. Namespace
        and name filters are applied in Python.
        """
        results = list(self.instance_objects.values())
        if criteria.states:
            matching = {
                d['object_uuid']
                for d in self.mariadb_states.values()
                if (d['object_type'] == ObjectType.INSTANCE
                    and d['state_value'] in criteria.states)
            }
            results = [d for d in results if str(d.uuid) in matching]
        if criteria.namespace is not None:
            results = [d for d in results if d.namespace == criteria.namespace]
        if criteria.name is not None:
            results = [d for d in results if d.name == criteria.name]
        self._trace(
            f'MockMariaDB.find_instances(criteria={criteria!r}): '
            f'{len(results)} results')
        return results

    def _mariadb_get_all_instance_uuids(self) -> list[str]:
        """Mock implementation of mariadb.get_all_instance_uuids()"""
        self._trace('MockMariaDB.get_all_instance_uuids()')
        return list(self.instance_objects.keys())

    def _mariadb_delete_instance(self, inst_uuid) -> bool:
        """Mock implementation of mariadb.delete_instance()"""
        key = str(inst_uuid)
        if key in self.instance_objects:
            del self.instance_objects[key]
            self._trace(
                f'MockMariaDB.delete_instance({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_instance({key}): not found')
        return False

    def _mariadb_create_instance_attributes(
            self, data: InstanceAttributesData) -> bool:
        """Mock implementation of mariadb.create_instance_attributes()"""
        key = str(data.uuid)
        if key in self.instance_attributes:
            self._trace(
                f'MockMariaDB.create_instance_attributes({key}): '
                f'already exists')
            return False
        self.instance_attributes[key] = data
        self._trace(
            f'MockMariaDB.create_instance_attributes({key}): created')
        return True

    def _mariadb_get_instance_attributes(
            self, inst_uuid) -> Optional[InstanceAttributesData]:
        """Mock implementation of mariadb.get_instance_attributes()"""
        key = str(inst_uuid)
        data = self.instance_attributes.get(key)
        self._trace(
            f'MockMariaDB.get_instance_attributes({key}): {data}')
        return data

    def _mariadb_update_instance_attributes(
            self, data: InstanceAttributesData,
            fields: Optional[List[str]] = None) -> bool:
        """Mock implementation of mariadb.update_instance_attributes()

        Like the real implementation, a fields mask limits the write to
        the named model fields; None or empty replaces every field. The
        masked path copies onto the stored object so writes to other
        fields by concurrent callers are preserved, mirroring the
        per-column SQL UPDATE.
        """
        key = str(data.uuid)
        if key in self.instance_attributes:
            if fields:
                stored = self.instance_attributes[key]
                for field in fields:
                    setattr(stored, field, getattr(data, field))
            else:
                self.instance_attributes[key] = data
            self._trace(
                f'MockMariaDB.update_instance_attributes({key}): '
                f'updated (fields={fields})')
            return True
        self._trace(
            f'MockMariaDB.update_instance_attributes({key}): '
            f'not found')
        return False

    def _mariadb_delete_instance_attributes(self, inst_uuid) -> bool:
        """Mock implementation of mariadb.delete_instance_attributes()"""
        key = str(inst_uuid)
        if key in self.instance_attributes:
            del self.instance_attributes[key]
            self._trace(
                f'MockMariaDB.delete_instance_attributes({key}): '
                f'deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_instance_attributes({key}): '
            f'not found')
        return False

    #
    # Mock MariaDB atomic placement admission and release
    #

    def set_node_capacity(self, node_uuid, limit_cpus=0, limit_memory_mb=0,
                          limit_disk_gb=0, used_cpus=0, used_memory_mb=0,
                          used_disk_gb=0, expected_demand=0.0,
                          demand_limit=None):
        """Seed a scheduler_node_capacity row for a node.

        No node has one by default, which is the phase 3 fail-open case
        (P7): admission proceeds unguarded and says so. Seeding a row
        makes the guard apply to that node.

        ``demand_limit`` is the mock's stand-in for the real D13 bound
        of ``SCHEDULER_TARGET_LOAD x cpu_schedulable`` (the mock has no
        node_metrics table; fold any measured load into
        ``expected_demand``). ``None``, the default, leaves the demand
        clause out of the guard entirely, as a real deployment with
        ``SCHEDULER_TARGET_LOAD`` at or below zero would.
        """
        self.node_capacity[str(node_uuid)] = {
            'limit_cpus': limit_cpus,
            'limit_memory_mb': limit_memory_mb,
            'limit_disk_gb': limit_disk_gb,
            'used_cpus': used_cpus,
            'used_memory_mb': used_memory_mb,
            'used_disk_gb': used_disk_gb,
            'expected_demand': expected_demand,
            'demand_limit': demand_limit,
        }
        return self.node_capacity[str(node_uuid)]

    def set_namespace_claim(self, namespace, limit_cpus=0, limit_memory_mb=0,
                            limit_disk_gb=0, used_cpus=0, used_memory_mb=0,
                            used_disk_gb=0, claim_uuid=None, state='active',
                            expires_in=3600):
        """Seed an active namespace_claims row for a namespace.

        No namespace has one by default, which is the unclaimed case:
        admission skips the claim stage and charges the cluster's
        unclaimed sums instead (which this mock does not model, because
        nothing caller-side can observe them). Seeding a row makes the
        claim stage apply to every placement in that namespace.

        The row defaults to active and unexpired -- expiry is coverage
        state the admission path resolves before it gets here, via the
        branch select this mock stands in for, so a seeded claim is by
        definition one that select would have returned. ``state`` and
        ``expires_in`` exist for the claim CRUD callers, which do see
        both fields.

        Whether the claim's limits can *refuse* a placement is not a
        property of the row: mariadb.CLAIM_ENFORCEMENT_HARD decides that
        for the whole cluster, and it is False for D16's advisory
        release. With it False, seeding a claim smaller than what is
        placed produces claim_over_limit and the offending dimensions on
        an admitted placement; with it True the same seed produces
        failing_stage='claim'.
        """
        now = time.time()
        claim_uuid = str(claim_uuid) if claim_uuid else str(uuid4())
        self.namespace_claims[claim_uuid] = {
            'uuid': claim_uuid,
            'namespace': str(namespace),
            'limit_cpus': limit_cpus,
            'limit_memory_mb': limit_memory_mb,
            'limit_disk_gb': limit_disk_gb,
            'used_cpus': used_cpus,
            'used_memory_mb': used_memory_mb,
            'used_disk_gb': used_disk_gb,
            'state': state,
            'expires_at': now + expires_in,
            'updated_at': now,
        }
        return self.namespace_claims[claim_uuid]

    def refuse_namespace_claims(self, reason):
        """Make every subsequent claim create or grow refuse.

        The mock deliberately does not model the cluster_capacity
        singleton's arithmetic. Re-implementing D14's mirror guard here
        would be a second implementation of the thing the phase exists
        to get right, which would then be what caller-side tests
        actually assert against; the guard is proven against a real
        server by test_mariadb_capacity_claims_live.py instead.

        What a caller *does* have to handle is a refusal, so this seeds
        one deterministically: pass 'capacity', 'no_cluster_capacity' or
        any other reason the real implementation can return, and pass
        '' to go back to accepting.
        """
        self.namespace_claim_refusal = reason

    def _claim_by_uuid(self, claim_uuid):
        """The seeded claim row with this uuid, or None."""
        return self.namespace_claims.get(str(claim_uuid))

    def _claim_for_namespace(self, namespace):
        """The claim admission would draw a namespace's placement down.

        The real branch select filters on active and unexpired coverage
        and takes the lowest uuid when a namespace somehow holds more
        than one; this resolves it the same way, so a test which seeds
        two claims for one namespace sees the same one admission would.
        """
        candidates = [row for row in self.namespace_claims.values()
                      if row['namespace'] == str(namespace)
                      and row['state'] == 'active'
                      and row['expires_at'] > time.time()]
        if not candidates:
            return None
        return sorted(candidates, key=lambda r: r['uuid'])[0]

    def _claim_row(self, row):
        """One claim row in the shape mariadb's claim reads return."""
        return {
            'uuid': row['uuid'],
            'namespace': row['namespace'],
            'limit_cpus': row['limit_cpus'],
            'limit_memory_mb': row['limit_memory_mb'],
            'limit_disk_gb': row['limit_disk_gb'],
            'used_cpus': row['used_cpus'],
            'used_memory_mb': row['used_memory_mb'],
            'used_disk_gb': row['used_disk_gb'],
            'state': row['state'],
            'expires_at': row['expires_at'],
            'updated_at': row['updated_at'],
        }

    def _mariadb_create_namespace_claim(
            self, claim_uuid, namespace, limit_cpus, limit_memory_mb,
            limit_disk_gb, expires_in_seconds):
        """Mock implementation of mariadb.create_namespace_claim()

        The D3 drawdown migration is not modelled: this mock has no
        cluster singleton to migrate usage out of, so a created claim
        starts at zero usage unless a test seeded one with
        set_namespace_claim() first.
        """
        result = {
            'success': True, 'error': '', 'created': False,
            'refused_reason': '', 'dimensions': [], 'claim': None}
        if self.namespace_claim_refusal:
            result['refused_reason'] = self.namespace_claim_refusal
            return result
        if self._claim_for_namespace(namespace) is not None:
            result['refused_reason'] = 'exists'
            return result

        row = self.set_namespace_claim(
            namespace, limit_cpus=limit_cpus,
            limit_memory_mb=limit_memory_mb, limit_disk_gb=limit_disk_gb,
            claim_uuid=claim_uuid, expires_in=expires_in_seconds)
        result['created'] = True
        result['claim'] = self._claim_row(row)
        return result

    def _mariadb_get_namespace_claim(self, claim_uuid):
        """Mock implementation of mariadb.get_namespace_claim()"""
        row = self._claim_by_uuid(claim_uuid)
        return self._claim_row(row) if row is not None else None

    def _mariadb_get_namespace_claims(self, namespace=''):
        """Mock implementation of mariadb.get_namespace_claims()"""
        return [self._claim_row(row)
                for _, row in sorted(self.namespace_claims.items())
                if not namespace or row['namespace'] == str(namespace)]

    def _mariadb_update_namespace_claim(
            self, claim_uuid, fields, limit_cpus=0, limit_memory_mb=0,
            limit_disk_gb=0, expires_in_seconds=0):
        """Mock implementation of mariadb.update_namespace_claim()

        D8's shrink floor is modelled because it is a refusal a caller
        has to handle and it is a property of the claim row alone. The
        grow guard is not, for the reason refuse_namespace_claims()
        records.
        """
        result = {
            'success': True, 'error': '', 'updated': False,
            'refused_reason': '', 'dimensions': [], 'claim': None}
        row = self._claim_by_uuid(claim_uuid)
        if row is None:
            result['refused_reason'] = 'not_found'
            return result
        if row['state'] != 'active':
            result['refused_reason'] = 'not_active'
            return result

        requested = {'limit_cpus': limit_cpus,
                     'limit_memory_mb': limit_memory_mb,
                     'limit_disk_gb': limit_disk_gb}
        growing = any(requested[f] > row[f]
                      for f in requested if f in fields)
        if growing and self.namespace_claim_refusal:
            result['refused_reason'] = self.namespace_claim_refusal
            return result

        for field, used in (('limit_cpus', 'used_cpus'),
                            ('limit_memory_mb', 'used_memory_mb'),
                            ('limit_disk_gb', 'used_disk_gb')):
            if field in fields and requested[field] < row[used]:
                result['refused_reason'] = 'below_usage'
                return result

        for field in requested:
            if field in fields:
                row[field] = requested[field]
        if 'expires_in_seconds' in fields:
            row['expires_at'] = time.time() + expires_in_seconds
        row['updated_at'] = time.time()

        result['updated'] = True
        result['claim'] = self._claim_row(row)
        return result

    def _mariadb_delete_namespace_claim(self, claim_uuid):
        """Mock implementation of mariadb.delete_namespace_claim()

        Deleting twice is harmless, exactly as it is against a real
        database: the second call finds no row and returns nothing.
        """
        result = {
            'success': True, 'error': '', 'deleted': False,
            'returned_cpus': 0, 'returned_memory_mb': 0,
            'returned_disk_gb': 0, 'clamped': False}
        row = self._claim_by_uuid(claim_uuid)
        if row is None:
            return result

        del self.namespace_claims[row['uuid']]
        result['deleted'] = True
        if row['state'] == 'active':
            result['returned_cpus'] = row['used_cpus']
            result['returned_memory_mb'] = row['used_memory_mb']
            result['returned_disk_gb'] = row['used_disk_gb']
        return result

    def _mariadb_get_scheduler_node_capacity(self):
        """Mock implementation of mariadb.get_scheduler_node_capacity()

        Only nodes seeded with set_node_capacity() have a row, which is
        what the real table looks like too: the reconciler writes a row
        per schedulable hypervisor and declines to guess for the rest.
        ``demand_limit`` is mock-internal configuration, not a column,
        so it is not returned.
        """
        return [
            {k: v for k, v in dict(row, node_uuid=node_uuid).items()
             if k != 'demand_limit'}
            for node_uuid, row in self.node_capacity.items()]

    def _decrement_node_capacity(self, node_uuid, cpus, memory_mb, disk_gb):
        """Floored decrement of one node's counters, as the RPC does (P6).

        Returns True if any dimension had to be clamped at zero, which
        means the ledger and ground truth had already diverged.
        """
        row = self.node_capacity.get(str(node_uuid))
        return self._decrement_capacity_row(row, cpus, memory_mb, disk_gb)

    def _decrement_namespace_claim(self, namespace, cpus, memory_mb, disk_gb):
        """Floored decrement of a namespace's claim, as the RPC does (P6).

        An unclaimed namespace has nothing to decrement here: the real
        implementation credits the cluster's unclaimed sums instead,
        which this mock does not model.
        """
        row = self._claim_for_namespace(namespace)
        return self._decrement_capacity_row(row, cpus, memory_mb, disk_gb)

    def _decrement_capacity_row(self, row, cpus, memory_mb, disk_gb):
        """Floored decrement of one row's used_* counters.

        Returns True if any dimension had to be clamped at zero, which
        means the ledger and ground truth had already diverged.
        """
        if row is None:
            return False
        clamped = False
        for field, amount in (('used_cpus', cpus),
                              ('used_memory_mb', memory_mb),
                              ('used_disk_gb', disk_gb)):
            if row[field] < amount:
                clamped = True
                row[field] = 0
            else:
                row[field] -= amount
        return clamped

    def _mariadb_admit_instance_placement(
            self, instance_uuid, namespace, node_uuid, cpus, memory_mb,
            disk_gb, placement_json, old_node_uuid='', enforce=True,
            enforce_demand=True):
        """Mock implementation of mariadb.admit_instance_placement()

        The counters are a simple in-memory ledger, but the reply shape,
        the fail-open behaviour of a node with no capacity row (P7), the
        move's floored decrement of the old node (P6) and -- most
        importantly for the callers under test -- the atomic combination
        of the placement attribute write with the delete-all-then-insert
        of the INSTANCE_LOCATION reference rows all match the real
        implementation. The D13 demand clause applies only to nodes
        seeded with a ``demand_limit`` (see set_node_capacity()), is
        waived by ``enforce_demand=False`` exactly as the real guard
        waives it for a zero target load, and every admission
        accumulates the placement's demand contribution whether or not
        the clause was enforced -- also like the real UPDATE.

        The claim stage applies to namespaces seeded with
        set_namespace_claim(), and matches the real implementation's
        semantics rather than approximating them: it is skipped by a
        move (which changes node, never namespace, so consumes nothing
        on the namespace side), it is guarded only when
        mariadb.CLAIM_ENFORCEMENT_HARD is True (D6's third flag, read at
        call time so a phase 5 test can patch it), and when it is not
        guarded an over-claim placement is *admitted* and reported --
        ``claim_over_limit`` with only the dimensions actually over in
        ``claim_dimensions``, each carrying the usage the claim held
        *before* this admission (D5's read-back). A denial at any stage
        charges nothing at all, as the real transaction's rollback does,
        so a denied reply can never carry an exceedance.

        The cluster singleton is still not modelled: an unclaimed
        namespace's charge has no caller-observable effect, so there is
        nothing for a caller-side test to assert about it.
        """
        result = {
            'success': True, 'error': '', 'admitted': False,
            'unguarded': False, 'clamped': False, 'failing_stage': '',
            'dimensions': [], 'node_used_cpus': 0,
            'node_used_memory_mb': 0, 'node_used_disk_gb': 0,
            'node_expected_demand': 0.0, 'claim_over_limit': False,
            'claim_dimensions': []}

        attrs = self.instance_attributes.get(str(instance_uuid))
        if attrs is None:
            result['success'] = False
            result['error'] = (
                f'instance {instance_uuid} has no instance_attributes row')
            self._trace(
                f'MockMariaDB.admit_instance_placement({instance_uuid}): '
                f'no attributes row')
            return result

        demand_add = cpus * config.SCHEDULER_DEMAND_PER_VCPU
        is_move = bool(old_node_uuid) and str(old_node_uuid) != str(node_uuid)

        # The claim stage, evaluated before the node stage exactly as the
        # real transaction orders its statements -- so when both stages
        # would refuse, the claim is the stage reported.
        claim = None if is_move else self._claim_for_namespace(namespace)
        if claim is not None and enforce and mariadb.CLAIM_ENFORCEMENT_HARD:
            dimensions = []
            for dimension, requested in (('cpus', cpus),
                                         ('memory_mb', memory_mb),
                                         ('disk_gb', disk_gb)):
                limit = claim['limit_' + dimension]
                used = claim['used_' + dimension]
                dimensions.append({
                    'dimension': dimension,
                    'limit': float(limit),
                    'used': float(used),
                    'requested': float(requested),
                    'exceeded': used + requested > limit})
            if any(d['exceeded'] for d in dimensions):
                result['failing_stage'] = 'claim'
                result['dimensions'] = dimensions
                self._trace(
                    f'MockMariaDB.admit_instance_placement('
                    f'{instance_uuid}, {node_uuid}): denied by claim')
                return result

        row = self.node_capacity.get(str(node_uuid))
        if row is None:
            result['unguarded'] = True
        elif enforce:
            dimensions = []
            for dimension, requested in (('cpus', cpus),
                                         ('memory_mb', memory_mb),
                                         ('disk_gb', disk_gb)):
                limit = row['limit_' + dimension]
                used = row['used_' + dimension]
                dimensions.append({
                    'dimension': dimension,
                    'limit': float(limit),
                    'used': float(used),
                    'requested': float(requested),
                    'exceeded': used + requested > limit})
            if enforce_demand and row['demand_limit'] is not None:
                dimensions.append({
                    'dimension': 'demand',
                    'limit': float(row['demand_limit']),
                    'used': float(row['expected_demand']),
                    'requested': float(demand_add),
                    'exceeded': (row['expected_demand'] + demand_add
                                 > row['demand_limit'])})
            if any(d['exceeded'] for d in dimensions):
                result['failing_stage'] = 'node'
                result['dimensions'] = dimensions
                self._trace(
                    f'MockMariaDB.admit_instance_placement('
                    f'{instance_uuid}, {node_uuid}): denied')
                return result

        # Nothing can refuse from here on, so the counters are charged --
        # the namespace side first, in the real transaction's order.
        if claim is not None:
            for dimension, requested in (('cpus', cpus),
                                         ('memory_mb', memory_mb),
                                         ('disk_gb', disk_gb)):
                limit = claim['limit_' + dimension]
                used = claim['used_' + dimension]
                claim['used_' + dimension] = used + requested
                # used is what the claim held *before* this admission, so
                # the triple reads exactly as a denial's does, and only
                # the dimensions actually over are reported.
                if used + requested > limit:
                    result['claim_dimensions'].append({
                        'dimension': dimension,
                        'limit': float(limit),
                        'used': float(used),
                        'requested': float(requested),
                        'exceeded': True})
            result['claim_over_limit'] = bool(result['claim_dimensions'])

        if row is not None:
            row['used_cpus'] += cpus
            row['used_memory_mb'] += memory_mb
            row['used_disk_gb'] += disk_gb
            row['expected_demand'] += demand_add

        if is_move:
            result['clamped'] = self._decrement_node_capacity(
                old_node_uuid, cpus, memory_mb, disk_gb)

        attrs.placement = json.loads(placement_json)

        self._delete_instance_location_rows(instance_uuid)
        self._mariadb_record_relationship(
            ObjectType.NODE, str(node_uuid),
            RelationshipType.INSTANCE_LOCATION, None,
            ObjectType.INSTANCE, str(instance_uuid))

        if row is not None:
            result['node_used_cpus'] = row['used_cpus']
            result['node_used_memory_mb'] = row['used_memory_mb']
            result['node_used_disk_gb'] = row['used_disk_gb']
            result['node_expected_demand'] = row['expected_demand']

        result['admitted'] = True
        self._trace(
            f'MockMariaDB.admit_instance_placement({instance_uuid}, '
            f'{node_uuid}): admitted')
        return result

    def _mariadb_release_instance_placement(
            self, instance_uuid, namespace, cpus, memory_mb, disk_gb,
            node_uuid=''):
        """Mock implementation of mariadb.release_instance_placement()

        Release is reference-gated in both call forms, as the real
        implementation is: the INSTANCE_LOCATION rows are the only
        record of what is charged, and node_uuid filters them rather
        than replacing them. So a release naming a node the instance
        holds no reference on releases nothing and reports
        released=False, which is what makes a repeat delete of an
        errored instance -- whose never-cleared placement attribute
        keeps naming its old node -- a no-op instead of a second
        decrement.
        """
        result = {
            'success': True, 'error': '', 'released': False, 'clamped': False}

        nodes = [
            r.source_uuid for r in self.object_references.values()
            if r.relationship == RelationshipType.INSTANCE_LOCATION
            and r.target_uuid == str(instance_uuid)]
        if node_uuid:
            nodes = [n for n in nodes if str(n) == str(node_uuid)]

        if not nodes:
            self._trace(
                f'MockMariaDB.release_instance_placement({instance_uuid}): '
                f'nothing to release')
            return result

        # The namespace side is charged once per instance however many
        # nodes hold a reference for it, so it is credited back once too
        # -- the real implementation says the same, in the same order
        # (namespace, then nodes).
        if self._decrement_namespace_claim(
                namespace, cpus, memory_mb, disk_gb):
            result['clamped'] = True

        for node in sorted(set(nodes)):
            if self._decrement_node_capacity(node, cpus, memory_mb, disk_gb):
                result['clamped'] = True

        # As in the real implementation, the rows deleted are exactly
        # the rows credited back: a named release leaves a row on
        # another node alone, charge and all.
        self._delete_instance_location_rows(instance_uuid, source_nodes=nodes)
        result['released'] = True
        self._trace(
            f'MockMariaDB.release_instance_placement({instance_uuid}): '
            f'released from {nodes}')
        return result

    def _delete_instance_location_rows(self, instance_uuid,
                                       source_nodes=None):
        """Remove INSTANCE_LOCATION reference rows for an instance.

        Every row when source_nodes is None (the admission form), or
        only the named sources' rows (the release form).
        """
        doomed = [
            key for key, r in self.object_references.items()
            if r.relationship == RelationshipType.INSTANCE_LOCATION
            and r.target_uuid == str(instance_uuid)
            and (source_nodes is None or
                 str(r.source_uuid) in {str(n) for n in source_nodes})]
        for key in doomed:
            del self.object_references[key]
        return len(doomed)

    #
    # Mock MariaDB object metadata functions
    #

    def _mariadb_get_object_metadata(self, object_type, object_uuid):
        """Mock implementation of mariadb.get_object_metadata()"""
        from shakenfist.schema.object_metadata import ObjectMetadataData

        key = f'{object_type}/{object_uuid}'
        if key in self.object_metadata:
            data = self.object_metadata[key]
            self._trace(
                f'MockMariaDB.get_object_metadata({key}): '
                f'found')
            return ObjectMetadataData(
                object_type=str(object_type),
                object_uuid=object_uuid,
                metadata=data.get('metadata'),
            )
        self._trace(
            f'MockMariaDB.get_object_metadata({key}): '
            f'not found')
        return None

    def _mariadb_set_metadata(self, object_type, object_uuid,
                              metadata_dict):
        """Mock implementation of mariadb.set_metadata()"""
        key = f'{object_type}/{object_uuid}'
        if key not in self.object_metadata:
            self.object_metadata[key] = {}
        self.object_metadata[key]['metadata'] = metadata_dict
        self._trace(
            f'MockMariaDB.set_metadata({key}): stored')
        return True

    def _mariadb_delete_object_metadata(self, object_type,
                                        object_uuid):
        """Mock implementation of mariadb.delete_object_metadata()"""
        key = f'{object_type}/{object_uuid}'
        if key in self.object_metadata:
            del self.object_metadata[key]
            self._trace(
                f'MockMariaDB.delete_object_metadata({key}): '
                f'deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_object_metadata({key}): '
            f'not found')
        return False

    def _mariadb_delete_object_events(self, object_type, object_uuid):
        """Mock implementation of mariadb.delete_object_events()"""
        self._trace(
            f'MockMariaDB.delete_object_events('
            f'{object_type}/{object_uuid}): ignored')
        return 0

    def _mariadb_create_cluster_operation_target(
            self, operation_uuid, operation_type,
            target_object_type, target_uuid, created_at):
        """Mock implementation of mariadb.create_cluster_operation_target()"""
        from shakenfist.schema.cluster_operation_target import (
            ClusterOperationTargetData)

        if operation_uuid in self.cluster_operation_targets:
            # Real code (_direct_create_cluster_operation_target in
            # mariadb.py) swallows IntegrityError on the UNIQUE
            # constraint and returns True. The mock matches that
            # behaviour to preserve idempotency semantics.
            self._trace(
                f'MockMariaDB.create_cluster_operation_target'
                f'({operation_uuid}): duplicate')
            return True

        seq = next(self._cot_sequence)
        self.cluster_operation_targets[operation_uuid] = (
            ClusterOperationTargetData(
                operation_uuid=operation_uuid,
                operation_type=str(operation_type),
                target_object_type=str(target_object_type),
                target_uuid=target_uuid,
                sequence_number=seq,
                created_at=created_at
            ))
        self._trace(
            f'MockMariaDB.create_cluster_operation_target'
            f'({operation_uuid}): created seq={seq}')
        return True

    def _mariadb_get_cluster_operation_target(
            self, operation_uuid):
        """Mock implementation of mariadb.get_cluster_operation_target()"""
        data = self.cluster_operation_targets.get(operation_uuid)
        self._trace(
            f'MockMariaDB.get_cluster_operation_target'
            f'({operation_uuid}): '
            f'{"found" if data else "not found"}')
        return data

    def _mariadb_get_cluster_operation_targets_for_object(
            self, target_object_type, target_uuid):
        """Mock implementation of mariadb.get_cluster_operation_targets_for_object()"""
        results = [
            d for d in self.cluster_operation_targets.values()
            if (d.target_object_type == str(target_object_type)
                and d.target_uuid == target_uuid)
        ]
        results.sort(key=lambda d: d.sequence_number or 0)
        self._trace(
            f'MockMariaDB.get_cluster_operation_targets_for_object'
            f'({target_object_type}/{target_uuid}): '
            f'{len(results)} found')
        return results

    def _mariadb_get_latest_cluster_operation_target(
            self, target_object_type, target_uuid):
        """Mock implementation of mariadb.get_latest_cluster_operation_target()"""
        results = self._mariadb_get_cluster_operation_targets_for_object(
            target_object_type, target_uuid)
        if not results:
            return None
        return results[-1]

    def _mariadb_delete_cluster_operation_target(
            self, operation_uuid):
        """Mock implementation of mariadb.delete_cluster_operation_target()"""
        if operation_uuid in self.cluster_operation_targets:
            del self.cluster_operation_targets[operation_uuid]
            self._trace(
                f'MockMariaDB.delete_cluster_operation_target'
                f'({operation_uuid}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_cluster_operation_target'
            f'({operation_uuid}): not found')
        return False

    def _mariadb_delete_cluster_operation_targets_for_object(
            self, target_object_type, target_uuid):
        """Mock implementation of mariadb.delete_cluster_operation_targets_for_object()"""
        to_delete = [
            k for k, d in self.cluster_operation_targets.items()
            if (d.target_object_type == str(target_object_type)
                and d.target_uuid == target_uuid)
        ]
        for k in to_delete:
            del self.cluster_operation_targets[k]
        self._trace(
            f'MockMariaDB.delete_cluster_operation_targets_for_object'
            f'({target_object_type}/{target_uuid}): '
            f'{len(to_delete)} deleted')
        return True

    def _mariadb_delete_stale_cluster_operation_targets(
            self, max_age):
        """Mock implementation of mariadb.delete_stale_cluster_operation_targets()"""
        active_states = ('queued', 'preflight', 'executing')
        cutoff = time.time() - max_age

        # Operation UUIDs that are still active in any object_states row.
        active_operation_uuids = {
            data['object_uuid']
            for data in self.mariadb_states.values()
            if data['state_value'] in active_states
        }

        to_delete = [
            k for k, d in self.cluster_operation_targets.items()
            if (d.created_at < cutoff
                and d.operation_uuid not in active_operation_uuids)
        ]
        for k in to_delete:
            del self.cluster_operation_targets[k]
        self._trace(
            f'MockMariaDB.delete_stale_cluster_operation_targets'
            f'(max_age={max_age}): {len(to_delete)} deleted')
        return len(to_delete)

    # MariaDB Node Metrics mock operations

    def _mariadb_upsert_node_metrics(
            self, node_uuid, fqdn, timestamp, metrics):
        """Mock implementation of mariadb.upsert_node_metrics()"""
        key = str(node_uuid)
        self.node_metrics_store[key] = {
            'node_uuid': key,
            'fqdn': fqdn,
            'timestamp': timestamp,
            'metrics': metrics
        }
        self._trace(
            f'MockMariaDB.upsert_node_metrics({key}): upserted')
        return True

    def _mariadb_get_node_metrics(self, node_uuid):
        """Mock implementation of mariadb.get_node_metrics()"""
        key = str(node_uuid)
        data = self.node_metrics_store.get(key)
        self._trace(
            f'MockMariaDB.get_node_metrics({key}): {data}')
        return data

    def _mariadb_get_all_node_metrics(self):
        """Mock implementation of mariadb.get_all_node_metrics()"""
        result = list(self.node_metrics_store.values())
        self._trace(
            f'MockMariaDB.get_all_node_metrics(): '
            f'{len(result)} items')
        return result

    def _mariadb_delete_node_metrics(self, node_uuid):
        """Mock implementation of mariadb.delete_node_metrics()"""
        key = str(node_uuid)
        if key in self.node_metrics_store:
            del self.node_metrics_store[key]
            self._trace(
                f'MockMariaDB.delete_node_metrics({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_node_metrics({key}): not found')
        return False

    # MariaDB Cluster Operations mock operations

    def _mariadb_create_cluster_operation(
            self, uuid, operation_type, metadata, created_at):
        """Mock implementation of mariadb.create_cluster_operation()

        Insert-only: refuses to overwrite an existing key and returns
        False on duplicate. Mirrors the real direct-layer contract.
        """
        key = str(uuid)
        if key in self.cluster_operations_store:
            self._trace(
                f'MockMariaDB.create_cluster_operation({key}): '
                f'duplicate')
            return False
        row = dict(metadata)
        row['uuid'] = key
        row['operation_type'] = operation_type
        row['created_at'] = created_at
        self.cluster_operations_store[key] = row
        self._trace(
            f'MockMariaDB.create_cluster_operation({key}): '
            f'created')
        return True

    def _mariadb_get_cluster_operation(self, uuid):
        """Mock implementation of mariadb.get_cluster_operation()"""
        key = str(uuid)
        data = self.cluster_operations_store.get(key)
        self._trace(
            f'MockMariaDB.get_cluster_operation({key}): {data}')
        return dict(data) if data is not None else None

    def _mariadb_get_cluster_operations_by_node(self, node_uuid):
        """Mock implementation of mariadb.get_cluster_operations_by_node()"""
        key = str(node_uuid)
        result = sorted(
            (
                dict(row)
                for row in self.cluster_operations_store.values()
                if str(row.get('node_uuid', '')) == key
            ),
            key=lambda r: r['created_at']
        )
        self._trace(
            f'MockMariaDB.get_cluster_operations_by_node({key}): '
            f'{len(result)} items')
        return result

    def _mariadb_delete_cluster_operation(self, uuid):
        """Mock implementation of mariadb.delete_cluster_operation()"""
        key = str(uuid)
        if key in self.cluster_operations_store:
            del self.cluster_operations_store[key]
            self._trace(
                f'MockMariaDB.delete_cluster_operation({key}): '
                f'deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_cluster_operation({key}): '
            f'not found')
        return False

    def _mariadb_create_and_enqueue_cluster_operation(
            self, op_uuid, operation_type, metadata,
            created_at, queue_name, delay=0.0, targets=None):
        """Mock implementation of the atomic create+enqueue path.

        Mirrors the real function's contract: insert-only on
        cluster_operations (returns (False, error) on duplicate), records
        a work_queue row in work_queue_store, and -- when
        ``targets`` is supplied -- writes one
        cluster_operation_targets row per target in the same call,
        matching the real function which now writes them in the
        same transaction as the operation. Until phase 5 switches
        from_db() to read cluster_operations from MariaDB, this
        mock also writes the legacy etcd path at
        /sf/{operation_type}/{uuid} so existing tests that load
        operations via from_db() continue to work.
        """
        key = str(op_uuid)
        if key in self.cluster_operations_store:
            self._trace(
                f'MockMariaDB.create_and_enqueue_cluster_operation'
                f'({key}): duplicate')
            return False, f'duplicate cluster_operation uuid: {key}'

        row = dict(metadata)
        row['uuid'] = key
        row['operation_type'] = operation_type
        row['created_at'] = created_at
        self.cluster_operations_store[key] = row

        self.work_queue_store.append({
            'id': next(self._work_queue_next_id),
            'queue_name': queue_name,
            'scheduled_at': created_at + delay,
            'claimed_at': None,
            'claimed_by': None,
            'attempts': 0,
            'payload': {
                'operation_type': operation_type,
                'operation_uuid': key,
            },
            'created_at': created_at,
        })

        # The real RPC writes an object_states row in the same
        # transaction. Mirror that here so tests that assert on
        # the initial 'queued' state continue to pass.
        state_key = f'{operation_type}/{key}'
        self.mariadb_states[state_key] = {
            'object_type': operation_type,
            'object_uuid': key,
            'state_value': 'queued',
            'update_time': created_at,
            'message': None,
        }

        # Write the cluster_operation_targets rows the real function
        # now writes in the same transaction. Delegating to the
        # existing target-writer preserves the mock's storage and
        # idempotency semantics.
        for target_object_type, target_uuid in (targets or []):
            self._mariadb_create_cluster_operation_target(
                operation_uuid=key,
                operation_type=operation_type,
                target_object_type=target_object_type,
                target_uuid=target_uuid,
                created_at=created_at,
            )

        self._trace(
            f'MockMariaDB.create_and_enqueue_cluster_operation'
            f'({key}): created and enqueued on {queue_name}')
        return True, ''

    def _mariadb_enqueue_work_item(
            self, queue_name, work_item, delay=0.0):
        """Mock implementation of mariadb.enqueue_work_item()."""
        now = time.time()
        row = {
            'id': next(self._work_queue_next_id),
            'queue_name': queue_name,
            'scheduled_at': now + delay,
            'claimed_at': None,
            'claimed_by': None,
            'attempts': 0,
            'payload': dict(work_item),
            'created_at': now,
        }
        self.work_queue_store.append(row)
        self._trace(
            f'MockMariaDB.enqueue_work_item({queue_name}, '
            f'{work_item}, delay={delay}): id={row["id"]}')

    def _mariadb_dequeue_work_items(self, queue_names, limit=10):
        """Mock implementation of mariadb.dequeue_work_items().

        Returns up to ``limit`` ``(queue_name, job_name, payload)``
        tuples for the highest-priority eligible rows. Priority is
        the caller-supplied order of ``queue_names``: index 0 is
        top priority, matching the SQL ORDER BY
        FIELD(queue_name, ...). Within a single queue_name, ties are
        broken by ``scheduled_at`` ASC (insertion order in practice).
        'Eligible' means ``claimed_at`` is None and ``scheduled_at``
        <= now.
        """
        now = time.time()
        priority = {q: i for i, q in enumerate(queue_names)}
        eligible = [
            r for r in self.work_queue_store
            if r['queue_name'] in priority
            and r['claimed_at'] is None
            and r['scheduled_at'] <= now
        ]
        if not eligible:
            self._trace(
                f'MockMariaDB.dequeue_work_items('
                f'{queue_names}, limit={limit}): empty')
            return []
        eligible.sort(key=lambda r: (
            priority[r['queue_name']], r['scheduled_at']))
        claimed = eligible[:limit]
        result = []
        for row in claimed:
            row['claimed_at'] = now
            row['claimed_by'] = 'mock'
            row['attempts'] = row.get('attempts', 0) + 1
            result.append(
                (row['queue_name'], str(row['id']),
                 dict(row['payload'] or {})))
        self._trace(
            f'MockMariaDB.dequeue_work_items('
            f'{queue_names}, limit={limit}): '
            f'returned {len(result)} items')
        return result

    def _mariadb_resolve_work_item(self, queue_name, job_name):
        """Mock implementation of mariadb.resolve_work_item()."""
        try:
            job_id = int(job_name)
        except (TypeError, ValueError):
            self._trace(
                f'MockMariaDB.resolve_work_item({queue_name}, '
                f'{job_name!r}): non-numeric job name, ignoring')
            return
        before = len(self.work_queue_store)
        self.work_queue_store = [
            r for r in self.work_queue_store
            if not (r['id'] == job_id and r['queue_name'] == queue_name)
        ]
        self._trace(
            f'MockMariaDB.resolve_work_item({queue_name}, '
            f'{job_name}): removed {before - len(self.work_queue_store)}')

    def _mariadb_get_work_queue_length(self, queue_name):
        """Mock implementation of mariadb.get_work_queue_length()."""
        now = time.time()
        processing = 0
        queued = 0
        deferred = 0
        for r in self.work_queue_store:
            if r['queue_name'] != queue_name:
                continue
            if r['claimed_at'] is not None:
                processing += 1
            elif r['scheduled_at'] <= now:
                queued += 1
            else:
                deferred += 1
        self._trace(
            f'MockMariaDB.get_work_queue_length({queue_name}): '
            f'processing={processing} queued={queued} '
            f'deferred={deferred}')
        return processing, queued, deferred

    def _mariadb_restart_work_queue(self, queue_name):
        """Mock implementation of mariadb.restart_work_queue()."""
        cleared = 0
        for r in self.work_queue_store:
            if (r['queue_name'] == queue_name
                    and r['claimed_at'] is not None):
                r['claimed_at'] = None
                r['claimed_by'] = None
                cleared += 1
        self._trace(
            f'MockMariaDB.restart_work_queue({queue_name}): '
            f'cleared {cleared}')

    def _mariadb_list_stuck_work_queue_rows(self, threshold_seconds):
        """Mock implementation of mariadb.list_stuck_work_queue_rows().

        Returns rows whose claim age exceeds threshold_seconds,
        ordered by claimed_at ascending so the caller processes
        the oldest stuck row first (matches the real SQL ORDER BY).
        """
        now = time.time()
        stuck = []
        for r in self.work_queue_store:
            if r['claimed_at'] is None:
                continue
            if now - r['claimed_at'] < threshold_seconds:
                continue
            stuck.append({
                'id': int(r['id']),
                'queue_name': r['queue_name'],
                'claimed_at': float(r['claimed_at']),
                'claimed_by': r['claimed_by'],
                'attempts': int(r.get('attempts', 0)),
                'payload': dict(r['payload'] or {}),
            })
        stuck.sort(key=lambda s: s['claimed_at'])
        self._trace(
            f'MockMariaDB.list_stuck_work_queue_rows'
            f'({threshold_seconds}): {len(stuck)}')
        return stuck

    def _mariadb_clear_work_queue_claim(self, row_id):
        """Mock implementation of mariadb.clear_work_queue_claim()."""
        for r in self.work_queue_store:
            if r['id'] == row_id and r['claimed_at'] is not None:
                r['claimed_at'] = None
                r['claimed_by'] = None
                self._trace(
                    f'MockMariaDB.clear_work_queue_claim'
                    f'({row_id}): cleared')
                return True
        self._trace(
            f'MockMariaDB.clear_work_queue_claim({row_id}): no-op')
        return False

    def _mariadb_delete_work_queue_row(self, row_id):
        """Mock implementation of mariadb.delete_work_queue_row()."""
        before = len(self.work_queue_store)
        self.work_queue_store = [
            r for r in self.work_queue_store if r['id'] != row_id
        ]
        removed = before != len(self.work_queue_store)
        self._trace(
            f'MockMariaDB.delete_work_queue_row({row_id}): '
            f'{"removed" if removed else "not found"}')
        return removed

    def _mariadb_find_existing_coalescible_op(
            self, operation_type, target_column, target_uuid, task_name):
        """Mock implementation of mariadb.find_existing_coalescible_op().

        Mirrors the SQL guards in
        ``mariadb._direct_find_existing_coalescible_op``: returns
        the uuid of the oldest single-task pending op on the same
        target whose task equals ``task_name``, or ``None``. The
        oldest-first order matches the SQL
        ``ORDER BY created_at ASC LIMIT 1``.
        """
        if target_column not in {
                'network_uuid', 'instance_uuid', 'node_uuid'}:
            return None
        candidates: list[tuple[float, str]] = []
        for op_uuid, op_row in self.cluster_operations_store.items():
            if op_row.get('operation_type') != operation_type:
                continue
            if str(op_row.get(target_column) or '') != str(target_uuid):
                continue
            # The mock stores metadata fields flat on the row (see
            # ``_mariadb_create_and_enqueue_cluster_operation``),
            # while the real cluster_operations table has a
            # ``metadata_json`` blob. Read from whichever shape is
            # populated so the mock matches both today's flat layout
            # and any future migration.
            metadata = op_row.get('metadata_json') or op_row
            tasks = metadata.get('tasks') or []
            if len(tasks) != 1 or tasks[0] != task_name:
                continue
            state_key = f'{operation_type}/{op_uuid}'
            state_row = self.mariadb_states.get(state_key)
            if not state_row or state_row.get('state_value') != 'queued':
                continue
            candidates.append(
                (op_row.get('created_at', 0.0), op_uuid))
        if not candidates:
            self._trace(
                f'MockMariaDB.find_existing_coalescible_op('
                f'{operation_type}, {target_column}={target_uuid}, '
                f'{task_name}): no match')
            return None
        candidates.sort()
        chosen_uuid = candidates[0][1]
        self._trace(
            f'MockMariaDB.find_existing_coalescible_op('
            f'{operation_type}, {target_column}={target_uuid}, '
            f'{task_name}): reusing {chosen_uuid}')
        return chosen_uuid

    def _mariadb_claim_coalescible_siblings(
            self, operation_type, target_column, target_uuid,
            task_names, exclude_op_uuid):
        """Mock implementation of mariadb.claim_coalescible_siblings().

        Mirrors the SQL safety guards in
        ``mariadb._direct_claim_coalescible_siblings``: only single-
        task ops in state ``queued`` matching one of ``task_names``
        on the same target are folded; the excluded op is never
        affected. Transitions the matched ops to ``complete`` in
        ``mariadb_states`` and returns their uuids.
        """
        if not task_names or target_column not in {
                'network_uuid', 'instance_uuid', 'node_uuid'}:
            return []

        folded: list[str] = []
        for op_uuid, op_row in list(self.cluster_operations_store.items()):
            if op_uuid == exclude_op_uuid:
                continue
            if op_row.get('operation_type') != operation_type:
                continue
            if str(op_row.get(target_column) or '') != str(target_uuid):
                continue
            # See ``_mariadb_find_existing_coalescible_op`` for the
            # rationale behind the metadata_json-vs-flat fallback.
            metadata = op_row.get('metadata_json') or op_row
            tasks = metadata.get('tasks') or []
            if len(tasks) != 1 or tasks[0] not in task_names:
                continue
            state_key = f'{operation_type}/{op_uuid}'
            state_row = self.mariadb_states.get(state_key)
            if not state_row or state_row.get('state_value') != 'queued':
                continue
            state_row['state_value'] = 'complete'
            state_row['update_time'] = time.time()
            state_row['message'] = 'coalesced into sibling op'
            folded.append(op_uuid)
        self._trace(
            f'MockMariaDB.claim_coalescible_siblings('
            f'{operation_type}, {target_column}={target_uuid}): '
            f'folded {len(folded)} ops')
        return folded

    #
    # DB operations - Utilizing SF DB functionality
    #

    def set_node_metrics_same(self, metrics=None):
        if not metrics:
            metrics = {
                'cpu_max_per_instance': 16,
                'cpu_max': 4,
                'memory_available': 22000,
                'memory_max': 24000,
                'disk_free_instances': 2000*1024*1024*1024,
                'cpu_total_instance_vcpus': 4,
                'cpu_available': 12,
            }

        for n in self.nodes:
            node_uuid = self.node_uuids[n[0]]
            metrics['is_hypervisor'] = 'hypervisor' in n[2]
            metrics['is_database_node'] = 'database' in n[2]
            self.node_metrics_store[node_uuid] = {
                'node_uuid': node_uuid,
                'fqdn': n[0],
                'timestamp': time.time(),
                'metrics': dict(metrics),
            }

    def update_node_metrics(self, fqdn, metrics):
        """Merge per-node metric overrides on top of already set metrics.

        Call set_node_metrics_same() first to establish the baseline
        (including role flags); this helper then makes individual nodes
        heterogeneous.
        """
        node_uuid = self.node_uuids[fqdn]
        self.node_metrics_store[node_uuid]['metrics'].update(metrics)

    #
    # Database backed objects
    #

    def create_namespace(self, namespace, key_name, key):
        ns = Namespace.new(namespace)
        ns.add_key(key_name, key)

    def create_instance(self, name,
                        uuid=None,
                        cpus=1,
                        disk_spec=[{'base': 'cirros', 'size': 21}],
                        memory=1024,
                        namespace='unittest',
                        requested_placement='',
                        ssh_key='ssh-rsa AAAAB3Nabc unit@test',
                        user_data='',
                        video='cirrus',
                        uefi=False,
                        configdrive='openstack-disk',
                        metadata=None,
                        set_state=Instance.STATE_CREATED,
                        place_on_node='',
                        ):

        if not uuid:
            uuid = self.next_uuid()

        inst = Instance.new(name=name,
                            cpus=cpus,
                            memory=memory,
                            namespace=namespace,
                            ssh_key=ssh_key,
                            disk_spec=disk_spec,
                            user_data=user_data,
                            video=video,
                            requested_placement=requested_placement,
                            instance_uuid=uuid,
                            uefi=uefi,
                            configdrive=configdrive,
                            )

        if metadata:
            for k, v in metadata.items():
                inst.add_metadata_key(k, v)

        # We just smash the requested state into the object, we don't attempt
        # to find a valid path to that state.
        inst._state_update(set_state, skip_transition_validation=True)

        if place_on_node:
            # Every production caller places by node uuid -- the scheduler's
            # candidates are uuids, as is config.NODE_UUID -- so an fqdn
            # passed here for readability is resolved rather than stored as
            # a placement no real code path would ever write.
            inst.place_instance(
                self.node_uuids.get(place_on_node, place_on_node))

        return inst

    def create_network(self, name,
                       uuid=None,
                       namespace='unittest',
                       netblock='10.9.8.0/24',
                       provide_dhcp=False,
                       provide_nat=False,
                       provide_dns=False,
                       vxid=None,
                       metadata=None,
                       set_state=Network.STATE_CREATED,
                       ):

        if not uuid:
            uuid = self.next_uuid()

        network = Network.new(name=name,
                              namespace=namespace,
                              netblock=netblock,
                              provide_dhcp=provide_dhcp,
                              provide_nat=provide_nat,
                              provide_dns=provide_dns,
                              network_uuid=uuid,
                              vxid=vxid,
                              )

        if metadata:
            for k, v in metadata.items():
                network.add_metadata_key(k, v)

        state_path = defaultdict(set)
        for initial, allowed in Network.state_targets.items():
            if allowed:
                for a in allowed:
                    state_path[a].add(initial)

        # We just smash the requested state into the object, we don't attempt
        # to find a valid path to that state.
        network._state_update(set_state, skip_transition_validation=True)

        # Ignore cluster operations because we don't do them in unit tests.
        # Use skip_transition_validation since operations may start in
        # various states depending on how they were created.
        last_op = network.last_cluster_operation
        if last_op and last_op.get('op_type'):
            op = get_object_class(last_op.get('op_type')).from_db(
                last_op.get('op_uuid'))
            op._state_update(op.STATE_EXECUTING, skip_transition_validation=True)
            op._state_update(op.STATE_COMPLETE, skip_transition_validation=True)

        return network

    def generate_netdesc(self,
                         network_uuid,
                         address='10.1.2.3',
                         model='virtio',
                         mac_address=None):
        return {
            'network_uuid': network_uuid,
            'address': address,
            'model': model,
            'macaddress': mac_address,
        }

    def create_network_interface(self,
                                 uuid=None,
                                 netdesc=None,
                                 instance_uuid=None,
                                 order=1,
                                 set_state=Network.STATE_CREATED
                                 ):

        # Handle default test data
        if not netdesc:
            raise Exception('Must set netdesc (use generate_netdesc()')

        net_iface = NetworkInterface.new(uuid, netdesc, instance_uuid, order)

        state_path = defaultdict(set)
        for initial, allowed in NetworkInterface.state_targets.items():
            if allowed:
                for a in allowed:
                    state_path[a].add(initial)

        # We just smash the requested state into the object, we don't attempt
        # to find a valid path to that state.
        net_iface._state_update(set_state, skip_transition_validation=True)

        return net_iface

    # ------------------------------------------------------------------
    # TrustedIssuer storage
    # ------------------------------------------------------------------

    def _mariadb_create_trusted_issuer(self, data):
        if any(d.name == data.name
               for d in self.trusted_issuers.values()):
            # The unique index on name.
            return False
        self.trusted_issuers[str(data.uuid)] = data
        return True

    def _mariadb_get_trusted_issuer(self, issuer_uuid):
        return self.trusted_issuers.get(str(issuer_uuid))

    def _mariadb_get_trusted_issuer_by_name(self, name):
        for data in self.trusted_issuers.values():
            if data.name == name:
                return data
        return None

    def _mariadb_get_all_trusted_issuers(self):
        return list(self.trusted_issuers.values())

    def _mariadb_delete_trusted_issuer(self, issuer_uuid):
        return self.trusted_issuers.pop(str(issuer_uuid), None) is not None

    def _mariadb_create_trusted_issuer_attributes(self, data):
        self.trusted_issuer_attributes[str(data.uuid)] = data
        return True

    def _mariadb_get_trusted_issuer_attributes(self, issuer_uuid):
        return self.trusted_issuer_attributes.get(str(issuer_uuid))

    def _mariadb_update_trusted_issuer_attributes(self, data, fields=None):
        """Mock of mariadb.update_trusted_issuer_attributes().

        Honours the fields mask the way the real implementation does,
        rather than always replacing the row. A mock which ignored the
        mask would let a caller name the wrong fields and still see the
        write it expected.
        """
        if str(data.uuid) not in self.trusted_issuer_attributes:
            return False
        if fields:
            stored = self.trusted_issuer_attributes[str(data.uuid)]
            for field in fields:
                setattr(stored, field, getattr(data, field))
        else:
            self.trusted_issuer_attributes[str(data.uuid)] = data
        return True

    def _mariadb_delete_trusted_issuer_attributes(self, issuer_uuid):
        return self.trusted_issuer_attributes.pop(
            str(issuer_uuid), None) is not None

    # ------------------------------------------------------------------
    # MappingRule
    # ------------------------------------------------------------------

    def _mariadb_create_mapping_rule(self, data):
        if any(d.namespace == data.namespace and d.name == data.name
               for d in self.mapping_rules.values()):
            # The unique index on (namespace, name).
            return False
        self.mapping_rules[str(data.uuid)] = data
        return True

    def _mariadb_get_mapping_rule(self, rule_uuid):
        return self.mapping_rules.get(str(rule_uuid))

    def _mariadb_get_mapping_rule_by_name(self, namespace, name):
        for data in self.mapping_rules.values():
            if data.namespace == namespace and data.name == name:
                return data
        return None

    def _mariadb_get_mapping_rules_in_namespace(self, namespace):
        return [d for d in self.mapping_rules.values()
                if d.namespace == namespace]

    def _mariadb_get_all_mapping_rules(self):
        return list(self.mapping_rules.values())

    def _mariadb_delete_mapping_rule(self, rule_uuid):
        return self.mapping_rules.pop(str(rule_uuid), None) is not None

    def _mariadb_create_mapping_rule_attributes(self, data):
        self.mapping_rule_attributes[str(data.uuid)] = data
        return True

    def _mariadb_get_mapping_rule_attributes(self, rule_uuid):
        return self.mapping_rule_attributes.get(str(rule_uuid))

    def _mariadb_update_mapping_rule_attributes(self, data, fields=None):
        """Mock of mariadb.update_mapping_rule_attributes().

        Honours the fields mask, for the reason given on the trusted
        issuer equivalent above.
        """
        if str(data.uuid) not in self.mapping_rule_attributes:
            return False
        if fields:
            stored = self.mapping_rule_attributes[str(data.uuid)]
            for field in fields:
                setattr(stored, field, getattr(data, field))
        else:
            self.mapping_rule_attributes[str(data.uuid)] = data
        return True

    def _mariadb_delete_mapping_rule_attributes(self, rule_uuid):
        return self.mapping_rule_attributes.pop(
            str(rule_uuid), None) is not None

    def _mariadb_record_federated_exchange(
            self, token_id, rule_uuid, expires_at):
        key = (token_id, str(rule_uuid))
        if key in self.federation_replay:
            return False
        self.federation_replay[key] = expires_at
        return True

    def _mariadb_count_federated_attempt(self, source, window_start):
        key = (source, window_start)
        self.federation_rate_limits[key] = \
            self.federation_rate_limits.get(key, 0) + 1
        return self.federation_rate_limits[key]

    def _mariadb_reap_federation_replay(self, cutoff):
        stale = [k for k, expires_at in self.federation_replay.items()
                 if expires_at < cutoff]
        for k in stale:
            del self.federation_replay[k]
        return len(stale)

    def _mariadb_reap_federation_rate_limits(self, cutoff):
        stale = [k for k in self.federation_rate_limits if k[1] < cutoff]
        for k in stale:
            del self.federation_rate_limits[k]
        return len(stale)
