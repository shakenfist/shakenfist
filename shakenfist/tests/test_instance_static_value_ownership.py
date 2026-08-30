# Copyright 2019 Michael Still and contributors

# Instance._static_values_to_dict() must not hand out the cached model's
# own container fields.
#
# This is the same defect that was found and fixed for AgentOperation:
# mariadb.get_instance() parks the InstanceData model in the process
# wide object cache under OBJECT_CACHE_TTL_IMMUTABLE, and pydantic's
# frozen=True on that model stops attribute assignment rather than
# mutation of a list or dict field's contents. No in-tree reader mutates
# disk_spec, video or side_channels today, so unlike the agentoperation
# case this pins a latent hazard rather than a live bug -- but
# sidechannel's instance_sidechannel_cache holds side_channels for the
# daemon's lifetime, which is the shared ownership that made the other
# case bite.

import uuid
from unittest import mock

from shakenfist.instance import Instance
from shakenfist.schema.instance_data import InstanceData
from shakenfist.tests import base


_DISK_SPEC = [{'size': 8, 'base': 'ubuntu:22.04', 'type': 'disk',
               'bus': 'virtio'}]


class InstanceStaticValueOwnershipTestCase(base.ShakenFistTestCase):
    def _data(self):
        return InstanceData(
            uuid=uuid.uuid4(), cpus=1, disk_spec=list(_DISK_SPEC),
            memory=1024, name='foo', namespace='tenant-a',
            video={'model': 'cirrus', 'memory': 16384},
            side_channels=['sf-agent2'],
            version=Instance.current_version)

    def test_the_container_fields_do_not_alias_the_cached_model(self):
        data = self._data()

        first = Instance._static_values_to_dict(data)
        second = Instance._static_values_to_dict(data)

        for field in ('disk_spec', 'video', 'side_channels'):
            self.assertEqual(getattr(data, field), first[field])
            self.assertIsNot(getattr(data, field), first[field])
            self.assertIsNot(first[field], second[field])

    def test_two_objects_from_one_cache_entry_do_not_share_them(self):
        # Asserted at the object level because that is what readers hold
        # onto: sidechannel parks inst.side_channels in a dict which
        # outlives the cache entry it came from.
        data = self._data()
        with mock.patch('shakenfist.instance.mariadb.get_instance',
                        return_value=data):
            first = Instance.from_db(str(data.uuid))
            second = Instance.from_db(str(data.uuid))

        first.side_channels.append('sf-agent3')
        first.disk_spec.pop(0)
        first.video['model'] = 'qxl'

        self.assertEqual(['sf-agent2'], second.side_channels)
        self.assertEqual(['sf-agent2'], data.side_channels)
        self.assertEqual(_DISK_SPEC, second.disk_spec)
        self.assertEqual(_DISK_SPEC, data.disk_spec)
        self.assertEqual('cirrus', second.video['model'])
        self.assertEqual('cirrus', data.video['model'])
