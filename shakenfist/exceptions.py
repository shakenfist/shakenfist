class HTTPError(Exception):
    pass


class VersionSpecificationError(Exception):
    pass


# Configuration
class NoNetworkNode(Exception):
    pass


class NoEtcd(Exception):
    pass


# Objects
class ObjectException(Exception):
    pass


class InvalidStateException(ObjectException):
    pass


class NoStateTransitionsDefined(ObjectException):
    pass


class MultipleObjects(ObjectException):
    pass


# Instance
class InstanceException(Exception):
    pass


class InstanceNotInDBException(InstanceException):
    pass


class InstanceBadDiskSpecification(InstanceException):
    pass


class NVRAMTemplateMissing(InstanceException):
    pass


class InvalidLifecycleState(InstanceException):
    pass


# Scheduler
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


class PreExistingReadOnlyCache(DatabaseException):
    pass


class ForbiddenWhileUsingReadOnlyCache(DatabaseException):
    pass


class PrefixNotInCache(DatabaseException):
    pass


# Virt
class VirtException(Exception):
    pass


class NoDomainException(VirtException):
    pass


# Config
class FlagException(Exception):
    pass


# Images
class BadCheckSum(Exception):
    pass


class ImagesCannotShrinkException(Exception):
    pass


class ImageMissingFromCache(Exception):
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


class NetworkMissing(NetworkException):
    pass


class IPManagerNotFound(NetworkException):
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


class UnknownChecksumType(ArtifactException):
    pass


class ArtifactHasNoBlobs(ArtifactException):
    pass


class ArtifactHasNoNamespace(ArtifactException):
    pass


# Blobs
class BlobMissing(ArtifactException):
    pass


class BlobDeleted(ArtifactException):
    pass


class BlobFetchFailed(Exception):
    pass


class BlobDependencyMissing(Exception):
    pass


class BlobsMustHaveContent(Exception):
    pass
