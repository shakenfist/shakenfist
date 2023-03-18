class HTTPError(Exception):
    ...


class VersionSpecificationError(Exception):
    ...


# Configuration
class NoNetworkNode(Exception):
    ...


class NoEtcd(Exception):
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


class ForbiddenWhileUsingReadOnlyCache(DatabaseException):
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
class BadCheckSum(Exception):
    ...


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


class UnknownChecksumType(ArtifactException):
    ...


class ArtifactHasNoBlobs(ArtifactException):
    ...


class ArtifactHasNoNamespace(ArtifactException):
    ...


class LabelHierarchyTooDeep(ArtifactException):
    ...


# Blobs
class BlobMissing(ArtifactException):
    ...


class BlobDeleted(ArtifactException):
    ...


class BlobFetchFailed(Exception):
    ...


class BlobDependencyMissing(Exception):
    ...


class BlobsMustHaveContent(Exception):
    ...


class BlobAlreadyBeingTransferred(Exception):
    ...


class BlobTransferSetupFailed(Exception):
    ...
