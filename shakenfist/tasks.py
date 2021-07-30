from shakenfist.exceptions import (NoURLImageFetchTaskException,
                                   NetworkNotListTaskException,
                                   NoInstanceTaskException,
                                   NoNetworkTaskException,
                                   NoNetworkInterfaceTaskException
                                   )


class QueueTask(object):
    """QueueTask defines a validated task placed on the job queue."""
    _name = None
    _version = 1  # Enable future upgrades to existing tasks

    @classmethod
    def name(self):
        return self._name

    @classmethod
    def pretty_task_name(self):
        return self._name.replace('_', ' ')

    def __repr__(self):
        # All subclasses define obj_dict()
        r = 'QueueTask:' + self.__class__.__name__ + ': '
        r += str(self.obj_dict())
        return r

    def __eq__(self, other):
        if not QueueTask.__subclasscheck__(type(other)):
            raise NotImplementedError(
                'Objects must be subclasses of QueueTask not %s', type(other))
        return self.__hash__() == other.__hash__()

    def __hash__(self):
        return hash(str(self.obj_dict()))

    def obj_dict(self):
        return {'task': self._name,
                'version': self._version}


#
# Instance Tasks
#
class InstanceTask(QueueTask):
    def __init__(self, instance_uuid, network=None):
        super(InstanceTask, self).__init__()
        self._instance_uuid = instance_uuid

        # Only set network if deliberately set in function paramater. This
        # avoids setting _network to None which is not iterable.
        self._network = []
        if network:
            self._network = network

        # General checks
        if not instance_uuid:
            raise NoInstanceTaskException(
                'No instance specified for InstanceTask')
        if not isinstance(instance_uuid, str):
            raise NoInstanceTaskException('Instance UUID is not a string')
        if not isinstance(self._network, list):
            raise NetworkNotListTaskException()

    def instance_uuid(self):
        return self._instance_uuid

    def network(self):
        return self._network

    def obj_dict(self):
        return {**super(InstanceTask, self).obj_dict(),
                'instance_uuid': self._instance_uuid,
                'network': self._network}


class PreflightInstanceTask(InstanceTask):
    _name = 'instance_preflight'


class StartInstanceTask(InstanceTask):
    _name = 'instance_start'


class DeleteInstanceTask(InstanceTask):
    _name = 'instance_delete'

#
# Network Tasks
#


class NetworkTask(QueueTask):
    def __init__(self, network_uuid):
        super(NetworkTask, self).__init__()
        self._network_uuid = network_uuid

        # General checks
        if not network_uuid:
            raise NoNetworkTaskException(
                'No network specified for NetworkTask')
        if not isinstance(network_uuid, str):
            raise NoNetworkTaskException('Network UUID is not a string')

    def network_uuid(self):
        return self._network_uuid

    def obj_dict(self):
        return {**super(NetworkTask, self).obj_dict(),
                'network_uuid': self._network_uuid}


class DeployNetworkTask(NetworkTask):
    _name = 'network_deploy'


class DestroyNetworkTask(NetworkTask):
    _name = 'network_destroy'


class UpdateDHCPNetworkTask(NetworkTask):
    _name = 'network_update_dhcp'


class RemoveDHCPNetworkTask(NetworkTask):
    _name = 'network_remove_dhcp'


class RemoveNATNetworkTask(NetworkTask):
    _name = 'network_remove_nat'


#
# NetworkInterface Tasks
#
class NetworkInterfaceTask(QueueTask):
    def __init__(self, network_uuid, interface_uuid):
        super(NetworkInterfaceTask, self).__init__()
        self._network_uuid = network_uuid
        self._interface_uuid = interface_uuid

        # General checks
        if not network_uuid:
            raise NoNetworkTaskException(
                'No network specified for NetworkTask')
        if not isinstance(network_uuid, str):
            raise NoNetworkTaskException('Network UUID is not a string')

        if not interface_uuid:
            raise NoNetworkInterfaceTaskException(
                'No network interface specified for NetworkInterfaceTask')
        if not isinstance(interface_uuid, str):
            raise NoNetworkInterfaceTaskException(
                'Network interface UUID is not a string')

    def network_uuid(self):
        return self._network_uuid

    def interface_uuid(self):
        return self._interface_uuid

    def obj_dict(self):
        return {**super(NetworkInterfaceTask, self).obj_dict(),
                'network_uuid': self._network_uuid,
                'interface_uuid': self._interface_uuid}


class FloatNetworkInterfaceTask(NetworkInterfaceTask):
    _name = 'network_interface_float'


class DefloatNetworkInterfaceTask(NetworkInterfaceTask):
    _name = 'network_interface_defloat'

#
# Image Tasks
#


class ImageTask(QueueTask):
    def __init__(self, url):
        super(ImageTask, self).__init__()
        self._url = url

        if not isinstance(url, str):
            raise NoURLImageFetchTaskException

    def obj_dict(self):
        return {**super(ImageTask, self).obj_dict(),
                'url': self._url}

    # Data methods
    def url(self):
        return self._url


class FetchImageTask(ImageTask):
    _name = 'image_fetch'

    def __init__(self, url, instance_uuid=None):
        super(FetchImageTask, self).__init__(url)
        self._instance_uuid = instance_uuid

    def obj_dict(self):
        return {**super(FetchImageTask, self).obj_dict(),
                'instance_uuid': self._instance_uuid}

    # Data methods
    def instance_uuid(self):
        return self._instance_uuid

#
# Snapshot Tasks
#


class SnapshotTask(QueueTask):
    _name = 'snapshot'

    def __init__(self, instance_uuid, disk, artifact_uuid, blob_uuid):
        super(SnapshotTask, self).__init__()
        self._instance_uuid = instance_uuid
        self._disk = disk
        self._artifact_uuid = artifact_uuid
        self._blob_uuid = blob_uuid

    def obj_dict(self):
        return {**super(SnapshotTask, self).obj_dict(),
                'instance_uuid': self._instance_uuid,
                'disk': self._disk,
                'artifact_uuid': self._artifact_uuid,
                'blob_uuid': self._blob_uuid}

    # Data methods
    def instance_uuid(self):
        return self._instance_uuid

    def disk(self):
        return self._disk

    def artifact_uuid(self):
        return self._artifact_uuid

    def blob_uuid(self):
        return self._blob_uuid
