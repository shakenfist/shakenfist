# Copyright 2026 Michael Still and contributors
"""Hypervisor-local instance state assertions, shared by both CI suites.

These need one UEFI instance and host access to the hypervisor it is
pinned to, which every topology has -- on the single machine topology the
one node is that hypervisor -- so they live here rather than in a suite
directory. stestr discovers tests per directory and the two suites are
disjoint, so a test defined only in ``cluster_ci_tests/`` runs for the
first time in the merge queue. Subclassing
``InstanceDiskStateTestsMixin`` from both suites runs it in PR CI as
well, without giving up the multi-node coverage. This is the
instance-side counterpart of ``dhcp_state.py``, which does the same for a
deleted network's dnsmasq directory.
"""

import json
import time

from testtools import content


# shakenfist.config.STORAGE_PATH, which the deployer does not move, and
# the directory Instance.instance_path builds under it.
INSTANCE_STORAGE_ROOT = '/srv/shakenfist/instances'

# How long to wait for the hypervisor to finish tearing a deleted
# instance down. The delete is asynchronous and queued behind whatever
# else that node's workers are doing, and the cleaner -- which is what
# finishes a delete whose first attempt could not stop the domain --
# runs on its own cadence.
DELETE_TEARDOWN_TIMEOUT = 300

# How long to wait for the state the delete has to remove to be there in
# the first place. The instance is already in the created state by the
# time this is spent, so the only thing outstanding is libvirt writing
# the nvram file out from its template, which happens as the domain
# starts. This is a failure deadline rather than a settle wait: a healthy
# cluster answers on the first read.
CREATE_SETTLE_TIMEOUT = 60

# The number of kinds of hypervisor-local state an instance has, which is
# what _hypervisor_residue() below counts: its directory, its nvram file
# and its libvirt domain.
RESIDUE_KINDS = 3


class InstanceDiskStateTestsMixin:
    """Deleting an instance removes what it left on its hypervisor's disk.

    An instance's hypervisor-local state is its directory under
    STORAGE_PATH -- root disk, config drive, console log -- the nvram
    file libvirt boots a UEFI instance from, and the libvirt domain
    itself. ``Instance._delete_on_hypervisor()`` removes the nvram,
    undefines the domain and then removes the directory, in that order.

    Those steps are coupled more tightly than they look: libvirt refuses
    to undefine a domain which still has nvram unless it is asked to
    remove the nvram too, so a failure to unlink the file leaves the
    domain defined as well, and a domain which cannot be undefined leaves
    its disks behind. That is why the cleaner carries several
    ``virsh undefine --nvram`` paths -- see
    ``daemons/cleaner/scheduled_tasks.py``.

    Nothing asserted any of it. The suite checked an instance's deletion
    only through the API, which reports the instance gone once the
    cluster-wide bookkeeping is done and says nothing about the node. So
    a teardown regression would be found from its symptom -- a hypervisor
    which has run out of disk -- rather than from the leak.

    Mix into a ``base.BaseNamespacedTestCase`` subclass in a suite
    directory; this class defines no ``__init__`` so the concrete class
    sets its own namespace prefix.
    """

    def setUp(self):
        super().setUp()

        self.hypervisor = self._state_hypervisor()
        self._require_node_exec(self.hypervisor)
        self._require_node_sudo(self.hypervisor)

    def _state_hypervisor(self):
        """The hypervisor this test pins its instance to.

        Chosen before the create rather than read back from the instance
        afterwards, so a node which cannot be reached skips the test
        before it has built anything, and sorted by name so that a
        failure on a given cluster names the same node every run.

        A hypervisor the API reported without a name is not a candidate:
        the pin is by name, and ``force_placement=None`` is no pin at
        all, which would place the instance wherever the scheduler liked
        and leave every assertion below reading a node it was never on.
        """
        hypervisors = sorted(
            [n for n in self._hypervisor_nodes() if n.get('name')],
            key=lambda n: n['name'])
        if not hypervisors:
            self.skipTest(
                'The API reports no named hypervisor, so there is no node to '
                'pin an instance to and no host state to assert on.')
        return hypervisors[0]

    def _hypervisor_residue(self, instance_uuid, directory, nvram_path):
        """What the hypervisor still holds for an instance.

        A list of descriptions, empty when the node holds nothing, so
        that one poll covers all three kinds of state and a failure can
        name which of them survived. The nvram normally lives inside the
        instance's directory, so an incomplete teardown usually reports
        both.
        """
        residue = []
        if self._node_path_present(self.hypervisor, directory):
            residue.append('its directory %s' % directory)
        if self._node_path_present(self.hypervisor, nvram_path):
            residue.append('its nvram file %s' % nvram_path)
        if self._domain_is_defined(self.hypervisor, instance_uuid):
            residue.append('a libvirt domain sf:%s' % instance_uuid)
        return residue

    def _await_hypervisor_residue(self, wanted, instance_uuid, directory,
                                  nvram_path, timeout, why):
        """Poll until the hypervisor holds exactly wanted kinds of state.

        wanted is RESIDUE_KINDS before the delete and zero after it, and
        both directions are polled: the nvram file appears as the domain
        starts, and the teardown which removes it all is asynchronous.
        """
        deadline = time.time() + timeout
        while True:
            residue = self._hypervisor_residue(
                instance_uuid, directory, nvram_path)
            if len(residue) == wanted:
                return
            if time.time() > deadline:
                break
            time.sleep(5)

        self.fail(
            '%s. After %d seconds hypervisor %s holds %d of the %d kinds of '
            'host state for instance %s rather than %d: %s'
            % (why, timeout, self.hypervisor.get('name'), len(residue),
               RESIDUE_KINDS, instance_uuid, wanted,
               ', '.join(residue) or 'nothing'))

    def test_delete_removes_the_instance_from_the_hypervisor(self):
        # UEFI, because the nvram file only exists for a UEFI instance,
        # and one empty gigabyte with no network: this instance never has
        # to boot anything. It only has to reach the created state, which
        # is where the domain has been defined and started, and so where
        # libvirt has written its nvram.
        inst = self.create_instance(
            'instancediskstate', 1, 1024, None,
            [{'size': 1, 'type': 'disk'}], None, None, uefi=True,
            force_placement=self.hypervisor.get('name'))
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        instance_uuid = inst['uuid']
        self._await_instance_create(instance_uuid)

        # The pin is what makes every assertion below sharp, so it is
        # checked rather than trusted: state read from a node the
        # instance was never on is not residue, and its absence would
        # prove nothing at all.
        # Resolved through the roster rather than compared as strings,
        # because the instance's node field has carried both a node name
        # and a node uuid -- and matched on the name, which is what the
        # pin was, and which the chosen node is known to have.
        placed = self._node_by_identifier(
            self._get_cluster_nodes(),
            self.system_client.get_instance(instance_uuid).get('node'))
        self.assertEqual(
            self.hypervisor['name'], (placed or {}).get('name'),
            'Instance %s was pinned to %s but the API reports it on %s'
            % (instance_uuid, self.hypervisor['name'],
               (placed or {}).get('name')))

        directory = '%s/%s' % (INSTANCE_STORAGE_ROOT, instance_uuid)
        # Reading the nvram path out of the domain also proves the domain
        # is there, because virsh dumpxml fails without one.
        nvram_path = self._domain_nvram_path(self.hypervisor, instance_uuid)
        self._await_hypervisor_residue(
            RESIDUE_KINDS, instance_uuid, directory, nvram_path,
            CREATE_SETTLE_TIMEOUT,
            'A created UEFI instance has a directory, an nvram file and a '
            'libvirt domain on its hypervisor. Without all three the '
            'assertion after the delete would prove nothing, because state '
            'which was never there is also state which was removed')

        self.test_client.delete_instance(instance_uuid)
        self._await_instance_deleted(instance_uuid)

        self._await_hypervisor_residue(
            0, instance_uuid, directory, nvram_path,
            DELETE_TEARDOWN_TIMEOUT,
            'Instance %s was deleted, so its hypervisor must hold nothing '
            'for it' % instance_uuid)
