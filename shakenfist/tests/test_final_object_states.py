# Copyright 2026 Michael Still and contributors

"""Which states the hard-delete sweep may take an object in.

'error' is final for some object types and not for others, and the
split is a decision per type rather than one value shared by all of
them (issue 3922). These tests pin the decision, because the cost of
getting it wrong in either direction is asymmetric and severe: a type
wrongly included has its live objects deleted, and a type wrongly
excluded leaks an object_states row per errored object forever.
"""

from unittest import mock

from shakenfist import constants
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.clusteroperationmapping import \
    OPERATION_NAMES_TO_CLASSES
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

    def test_every_op_suffixed_type_is_a_pure_database_cluster_operation(self):
        # error_is_final() takes every '_op' type on its name alone, so this
        # is what makes that safe: each one must be a cluster operation, and
        # declare no on-disk state, because health_dependencies is how a
        # type says it has files behind it (as blob, upload and instance
        # do). A future 'volume_op' with on-disk state fails here rather
        # than being swept on error. Derived from the enum, so a new type
        # cannot arrive outside the check.
        op_types = [t for t in ObjectType if str(t).endswith('_op')]
        self.assertNotEqual([], op_types)
        for object_type in op_types:
            name = str(object_type)
            self.assertIn(name, OPERATION_NAMES_TO_CLASSES, name)
            cls = OPERATION_NAMES_TO_CLASSES[name]
            self.assertTrue(issubclass(cls, BaseClusterOperation), name)
            self.assertEqual(name, str(cls.object_type), name)
            self.assertFalse(getattr(cls, 'health_dependencies', None), name)

    def test_every_cluster_operation_has_the_op_suffix(self):
        # The converse: a cluster operation without the suffix would keep
        # its errored rows forever, and would not get
        # CLUSTER_OPERATION_RETENTION either. Walks the class hierarchy as
        # well as the mapping, so a subclass the mapping omits is caught.
        def subclasses(cls):
            for sub in cls.__subclasses__():
                yield sub
                yield from subclasses(sub)

        # Other test modules subclass it with stubs, which are not ours to
        # check and are only present when those modules happen to load.
        classes = {cls for cls in subclasses(BaseClusterOperation)
                   if not cls.__module__.startswith('shakenfist.tests.')}
        self.assertEqual(set(OPERATION_NAMES_TO_CLASSES.values()), classes)
        for cls in classes:
            name = str(cls.object_type)
            self.assertTrue(name.endswith('_op'), cls.__name__)
            self.assertTrue(constants.error_is_final(cls.object_type), name)

    def test_the_extension_set_makes_error_final_by_type_value(self):
        # ERROR_IS_FINAL_OBJECT_TYPES is empty today, so prove the branch
        # works for whoever adds the first type: membership is by the
        # type's string value, which is what the sweep passes.
        self.assertFalse(constants.error_is_final(ObjectType.UPLOAD))
        with mock.patch.object(constants, 'ERROR_IS_FINAL_OBJECT_TYPES',
                               frozenset({'upload'})):
            self.assertTrue(constants.error_is_final(ObjectType.UPLOAD))
            self.assertTrue(constants.error_is_final('upload'))
            self.assertIn(
                'error', constants.final_object_states(ObjectType.UPLOAD))
            self.assertFalse(constants.error_is_final(ObjectType.BLOB))

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
