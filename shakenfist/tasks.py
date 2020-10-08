from shakenfist.exceptions import (NoURLImageFetchTaskException,
                                   NetworkNotListTaskException,
                                   NoInstanceTaskException,
                                   NoNextStateTaskException,
                                   )


class QueueTask(object):
    '''QueueTask defines a validated task placed on the job queue.
    '''
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
        return self.name() + ': ' + str(self.json_dump())

    def __eq__(self, other):
        if not QueueTask.__subclasscheck__(type(other)):
            raise NotImplementedError('Objects must be subclasses of QueueTask')
        return self.__hash__() == other.__hash__()

    def __hash__(self):
        return hash((self._name, self._version))

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
        self._network = network

        # General checks
        if not instance_uuid:
            raise NoInstanceTaskException('No instance specified for InstanceTask')
        if network and not isinstance(network, list):
            raise NetworkNotListTaskException()

    def __hash__(self):
        return hash((super(InstanceTask, self).__hash__(),
                     self._instance_uuid,
                     hash(''.join(self._network) if self._network else '')))

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

    def __init__(self, instance_uuid, next_state, next_state_message=None,
                 network=None):
        super(DeleteInstanceTask, self).__init__(instance_uuid)

        if not next_state:
            raise NoNextStateTaskException(
                'DeleteInstanceTask requires a next_state')

        # TODO(andy): next_state should be built into current state
        self._next_state = next_state
        self._next_state_message = next_state_message

    def __hash__(self):
        return hash((super(DeleteInstanceTask, self).__hash__(),
                     self._next_state,
                     self._next_state_message))

    def json_dump(self):
        return {**super(DeleteInstanceTask, self).json_dump(),
                'next_state': self._next_state,
                'next_state_message': self._next_state_message}

    def next_state(self):
        return self._next_state

    def next_state_message(self):
        return self._next_state_message


#
# Image Tasks
#
class ImageTask(QueueTask):
    def __init__(self, url):
        super(ImageTask, self).__init__()
        self._url = url

        if not isinstance(url, str):
            raise NoURLImageFetchTaskException

    def __hash__(self):
        return hash((super(ImageTask, self).__hash__(),
                     self._url))

    def json_dump(self):
        return {**super(ImageTask, self).json_dump(),
                'url': self._url,
                'instance_uuid': self._instance_uuid}

    # Data methods
    def url(self):
        return self._url


class FetchImageTask(ImageTask):
    _name = 'image_fetch'

    def __init__(self, url, instance_uuid=None):
        super(FetchImageTask, self).__init__(url)
        self._instance_uuid = instance_uuid

    def __hash__(self):
        return hash((super(FetchImageTask, self).__hash__(),
                     self._instance_uuid))

    # Data methods
    def instance_uuid(self):
        return self._instance_uuid
