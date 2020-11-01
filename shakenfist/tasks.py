from shakenfist.exceptions import (NoURLImageFetchTaskException,
                                   NetworkNotListTaskException,
                                   NoInstanceTaskException,
                                   NoNetworkTaskException,
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
        # All subclasses define json_dump()
        r = 'QueueTask:' + self.__class__.__name__ + ': '
        r += str(self.json_dump())
        return r

    def __eq__(self, other):
        if not QueueTask.__subclasscheck__(type(other)):
            raise NotImplementedError(
                'Objects must be subclasses of QueueTask')
        return self.__hash__() == other.__hash__()

    def __hash__(self):
        return hash(str(self.json_dump()))

    def json_dump(self):
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

    def json_dump(self):
        return {**super(InstanceTask, self).json_dump(),
                'instance_uuid': self._instance_uuid,
                'network': self._network}


class PreflightInstanceTask(InstanceTask):
    _name = 'instance_preflight'


class StartInstanceTask(InstanceTask):
    _name = 'instance_start'


class DeleteInstanceTask(InstanceTask):
    _name = 'instance_delete'


class ErrorInstanceTask(InstanceTask):
    _name = 'instance_error'

    def __init__(self, instance_uuid, error_msg=None, network=None):
        super(ErrorInstanceTask, self).__init__(instance_uuid)
        self._error_msg = error_msg

    def json_dump(self):
        return {**super(ErrorInstanceTask, self).json_dump(),
                'error_msg': self._error_msg}

    def error_msg(self):
        return self._error_msg


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

    def json_dump(self):
        return {**super(NetworkTask, self).json_dump(),
                'network_uuid': self._network_uuid}


class DeployNetworkTask(NetworkTask):
    _name = 'network_deploy'


class UpdateDHCPNetworkTask(NetworkTask):
    _name = 'network_update_dhcp'


class RemoveDHCPNetworkTask(NetworkTask):
    _name = 'network_remove_dhcp'


#
# Image Tasks
#
class ImageTask(QueueTask):
    def __init__(self, url):
        super(ImageTask, self).__init__()
        self._url = url

        if not isinstance(url, str):
            raise NoURLImageFetchTaskException

    def json_dump(self):
        return {**super(ImageTask, self).json_dump(),
                'url': self._url}

    # Data methods
    def url(self):
        return self._url


class FetchImageTask(ImageTask):
    _name = 'image_fetch'

    def __init__(self, url, instance_uuid=None):
        super(FetchImageTask, self).__init__(url)
        self._instance_uuid = instance_uuid

    def json_dump(self):
        return {**super(FetchImageTask, self).json_dump(),
                'instance_uuid': self._instance_uuid}

    # Data methods
    def instance_uuid(self):
        return self._instance_uuid
