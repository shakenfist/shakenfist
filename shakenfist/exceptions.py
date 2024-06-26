class HTTPError(Exception):
    ...


class VersionSpecificationError(Exception):
    ...


# Configuration
class NoNetworkNode(Exception):
    ...


# Objects
class ObjectException(Exception):
    ...


class InvalidStateException(ObjectException):
    ...


class NoStateTransitionsDefined(ObjectException):
    ...


class MultipleObjects(ObjectException):
    ...


class UpgradeException(ObjectException):
    ...


class InvalidObjectPrefilter(ObjectException):
    ...


# Instance
class InstanceException(Exception):
    ...


class InstanceNotInDBException(InstanceException):
    ...


class InstanceBadDiskSpecification(InstanceException):
    ...


class NVRAMTemplateMissing(InstanceException):
    ...


class InvalidLifecycleState(InstanceException):
    ...


# Scheduler
class SchedulerException(Exception):
    ...


class CandidateNodeNotFoundException(SchedulerException):
    ...


class LowResourceException(SchedulerException):
    ...


class AbortInstanceStartException(SchedulerException):
    ...


# Database
class DatabaseException(Exception):
    ...


class LockException(DatabaseException):
    ...


class WriteException(DatabaseException):
    ...


class ReadException(DatabaseException):
    ...


class BadObjectVersion(DatabaseException):
    ...


class PreExistingReadOnlyCache(DatabaseException):
    ...


class PrefixNotInCache(DatabaseException):
    ...


# Virt
class VirtException(Exception):
    ...


class NoDomainException(VirtException):
    ...


# Config
class FlagException(Exception):
    ...


# Images
class ImagesCannotShrinkException(Exception):
    ...


class ImageMissingFromCache(Exception):
    ...


# Tasks
class TaskException(Exception):
    ...


class UnknownTaskException(TaskException):
    ...


class NoURLImageFetchTaskException(TaskException):
    ...


class ImageFetchTaskFailedException(TaskException):
    ...


class NoInstanceTaskException(TaskException):
    ...


class NoNetworkTaskException(TaskException):
    ...


class NoNetworkInterfaceTaskException(TaskException):
    ...


class NetworkNotListTaskException(TaskException):
    ...


# Networks
class NetworkException(Exception):
    ...


class DeadNetwork(NetworkException):
    ...


class CongestedNetwork(NetworkException):
    ...


class NoInterfaceStatistics(NetworkException):
    ...


class NetworkMissing(NetworkException):
    ...


class IPManagerMissing(NetworkException):
    ...


class InvalidAddress(NetworkException):
    ...


# NetworkInterface
class NetworkInterfaceException(Exception):
    ...


class NetworkInterfaceAlreadyFloating(NetworkInterfaceException):
    ...


# Artifacts
class ArtifactException(Exception):
    ...


class TooManyMatches(ArtifactException):
    ...


class ArtifactHasNoBlobs(ArtifactException):
    ...


class ArtifactHasNoNamespace(ArtifactException):
    ...


class LabelHierarchyTooDeep(ArtifactException):
    ...


# Blobs
class BlobException(Exception):
    ...


class BlobMissing(BlobException):
    ...


class BlobDeleted(BlobException):
    ...


class BlobFetchFailed(BlobException):
    ...


class BlobDependencyMissing(BlobException):
    ...


class BlobsMustHaveContent(BlobException):
    ...


class BlobAlreadyBeingTransferred(BlobException):
    ...


class BlobTransferSetupFailed(BlobException):
    ...


class BadCheckSum(BlobException):
    ...


# Events
class EventException(Exception):
    ...


class InvalidEventType(EventException):
    ...
