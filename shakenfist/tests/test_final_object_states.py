# Copyright 2026 Michael Still and contributors

"""Which states the hard-delete sweep may take an object in.

'error' is final for some object types and not for others, and the
split is a decision per type rather than one value shared by all of
them (issue 3922). These tests pin the decision, because the cost of
getting it wrong in either direction is asymmetric and severe: a type
wrongly included has its live objects deleted, and a type wrongly
excluded leaks an object_states row per errored object forever.
"""

from shakenfist import constants
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class FinalObjectStatesTestCase(base.ShakenFistTestCase):
    def test_error_is_final_for_a_cluster_operation(self):
        # The volume case: operations arrive tens of thousands a day,
        # and an errored one used to be kept forever while a complete,
        # aborted or expired one was swept.
        self.assertIn('error', constants.final_object_states('net_op'))
        self.assertIn(
            'error', constants.final_object_states('node_inst_op'))
        self.assertIn(
            'error', constants.final_object_states(ObjectType.NODE_BLOB_OP))

    def test_every_operation_object_type_treats_error_as_final(self):
        # Derived from the enum rather than a hand-written list, so a new
        # operation type cannot arrive outside the rule.
        for object_type in ObjectType:
            if not str(object_type).endswith('_op'):
                continue
            self.assertTrue(
                constants.error_is_final(object_type), str(object_type))

    def test_error_is_not_final_for_an_instance(self):
        # An operator is expected to look at an errored instance and may
        # recover it; sweeping it would destroy the evidence.
        self.assertNotIn(
            'error', constants.final_object_states(ObjectType.INSTANCE))

    def test_error_is_not_final_for_the_types_that_are_still_live(self):
        # A node or a network in error still holds real resources, and
        # sf-ctl clear-node-error returns an errored node to service, so
        # these are not "finished with" in any sense.
        for object_type in (ObjectType.NODE, ObjectType.NETWORK,
                            ObjectType.INTERFACE):
            self.assertNotIn(
                'error', constants.final_object_states(object_type),
                str(object_type))

    def test_error_is_not_final_for_a_blob_or_an_artifact(self):
        # A blob in error has partial files on disk which only a move to
        # 'deleted' removes, and an errored artifact still resolves by
        # name so a user can see why their fetch failed.
        for object_type in (ObjectType.BLOB, ObjectType.ARTIFACT):
            self.assertNotIn(
                'error', constants.final_object_states(object_type),
                str(object_type))

    def test_error_is_not_final_for_an_agent_operation(self):
        # The sidechannel daemon resolves an operation whose executor
        # died as errored rather than expired specifically so that case
        # survives for an operator to read.
        self.assertNotIn(
            'error',
            constants.final_object_states(ObjectType.AGENTOPERATION))

    def test_the_shared_final_states_are_unchanged_for_every_type(self):
        for object_type in ObjectType:
            states = constants.final_object_states(object_type)
            for state in constants.FINAL_OBJECT_STATES:
                self.assertIn(state, states, str(object_type))

    def test_a_caller_cannot_widen_the_states_for_anyone_else(self):
        # The result goes straight into a hard delete's state filter, so
        # a caller which appended to a shared list would widen what the
        # sweep deletes for every type for the rest of the process.
        for object_type in ('net_op', ObjectType.INSTANCE):
            mine = constants.final_object_states(object_type)
            mine.append('created')
            self.assertNotIn(
                'created', constants.final_object_states(object_type),
                str(object_type))
        self.assertNotIn('created', constants.FINAL_OBJECT_STATES)
        self.assertNotIn('error', constants.FINAL_OBJECT_STATES)
