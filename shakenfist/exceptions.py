class HTTPError(Exception):
    pass


class VersionSpecificationError(Exception):
    pass


# Objects
class ObjectException(Exception):
    pass


class InvalidStateException(ObjectException):
    pass


class NoStateTransitionsDefined(ObjectException):
    pass


# Instance


class InstanceException(Exception):
    pass


class InstanceNotInDBException(InstanceException):
    pass


class InstanceBadDiskSpecification(InstanceException):
    pass


class SchedulerException(Exception):
    pass


class CandidateNodeNotFoundException(SchedulerException):
    pass


class LowResourceException(SchedulerException):
    pass


class AbortInstanceStartException(SchedulerException):
    pass


# Database
class DatabaseException(Exception):
    pass


class LockException(DatabaseException):
    pass


class WriteException(DatabaseException):
    pass


class ReadException(DatabaseException):
    pass


class BadObjectVersion(DatabaseException):
    pass


class VirtException(Exception):
    pass


class NoDomainException(VirtException):
    pass


class FlagException(Exception):
    pass


# Images
class BadCheckSum(Exception):
    pass


# Tasks
class TaskException(Exception):
    pass


class UnknownTaskException(TaskException):
    pass


class NoURLImageFetchTaskException(TaskException):
    pass


class ImageFetchTaskFailedException(TaskException):
    pass


class NoInstanceTaskException(TaskException):
    pass


class NoNetworkTaskException(TaskException):
    pass


class NoNetworkInterfaceTaskException(TaskException):
    pass


class NetworkNotListTaskException(TaskException):
    pass


# Networks
class NetworkException(Exception):
    pass


class DeadNetwork(NetworkException):
    pass


class CongestedNetwork(NetworkException):
    pass


class NoInterfaceStatistics(NetworkException):
    pass


# NetworkInterface
class NetworkInterfaceException(Exception):
    pass


class NetworkInterfaceAlreadyFloating(NetworkInterfaceException):
    pass


# Artifacts
class ArtifactException(Exception):
    pass


class TooManyMatches(ArtifactException):
    pass
