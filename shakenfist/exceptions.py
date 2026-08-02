class HTTPError(Exception):
    ...


class VersionSpecificationError(Exception):
    ...


# MariaDB
class MariaDBIncompatibleError(Exception):
    """The connected MariaDB server is incompatible with this SF release."""


class SchemaVersionMismatchError(Exception):
    """The MariaDB schema version does not match what this SF release expects."""


# Configuration
class NoNetworkNode(Exception):
    ...


class NotOnNetworkNode(Exception):
    """A network-node-only ``_apply_*`` method was invoked on a host that
    is not the elected network node. The cluster-wide effect of these
    methods (dnsmasq config, NAT/floating-IP rules, network-namespace
    state) only lives on the network node, so calling them elsewhere
    silently does nothing -- which used to manifest as DNS lookups
    missing entries and floating IPs not appearing, with no error.
    Raising at the call site instead surfaces the bug immediately.
    """
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


class NoSuchChannel(InstanceException):
    ...


# Scheduler
class SchedulerException(Exception):
    ...


class CandidateNodeNotFoundException(SchedulerException):
    ...


class LowResourceException(SchedulerException):
    ...


# Database
class DatabaseException(Exception):
    ...


class DatabaseUnavailable(DatabaseException):
    """Raised when the database service cannot be reached (retries on
    UNAVAILABLE or DEADLINE_EXCEEDED exhausted). Deliberately not a
    grpc.RpcError subclass so the mariadb.py client wrappers, which
    map RpcError to "object not found" return values, let it propagate
    to callers instead -- an unreachable database must not be
    indistinguishable from a missing object (issue 3373)."""
    ...


class LockException(DatabaseException):
    ...


class LockNotHeld(LockException):
    """Raised when a caller tries to release or refresh a cluster lock
    that the database does not record them as holding -- typically
    because the lease expired and another node stole it."""
    ...


class WriteException(DatabaseException):
    ...


class ReadException(DatabaseException):
    ...


class BadObjectVersion(DatabaseException):
    ...


class CorruptMappingRule(DatabaseException):
    """A mapping rule's policy columns could not be decoded.

    Raised rather than defaulted because the columns are NOT NULL and
    are written only by us, so an absent value means the row is
    damaged. An empty bound_claims dict would be a matcher set that
    matches every token, which is the one thing a rule must never
    silently become.
    """


class PreExistingReadOnlyCache(DatabaseException):
    ...


class PrefixNotInCache(DatabaseException):
    ...


class CannotEnqueueWork(DatabaseException):
    ...


# Virt
class VirtException(Exception):
    ...


class NoDomainException(VirtException):
    ...


class InvalidDomainXML(VirtException):
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


class InvalidAddress(NetworkException):
    ...


class NatOnlyNetworksShouldNotHaveDnsMasq(NetworkException):
    ...


class CannotAssignFloatingGateway(NetworkException):
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


class BlobSizeCannotChange(BlobException):
    ...


# Events
class EventException(Exception):
    ...


class InvalidEventType(EventException):
    ...


class CorruptEventChunk(EventException):
    ...


# IPAM
class IPAMException(Exception):
    ...


class InvalidIPAMAddress(IPAMException):
    ...


# Nodes
class NodeException(Exception):
    ...


class NodeShouldExist(NodeException):
    ...


class NoSuchDaemon(NodeException):
    ...


class NoSuchDaemonState(NodeException):
    ...


# Lockless update failures
class LocklessUpdateFailed(Exception):
    ...


class LocklessUpdateFailed(LocklessUpdateFailed):
    ...


# gRPC call failures
class gRPCException(Exception):
    ...


# Authentication exceptions
class AuthException(Exception):
    ...


class CannotParseJWTIdentity(AuthException):
    ...


# Utility exceptions
class MissingNodeLockSocket(Exception):
    ...


class TruncatedNodeLockResponse(Exception):
    ...


class UnknownNodeLockReplyException(Exception):
    ...


class MissingPrivExecSocket(Exception):
    ...


class TruncatedPrivExecResponse(Exception):
    ...


class UnknownPrivExecReplyException(Exception):
    ...


class EnableNATFailed(Exception):
    ...


class EnsureMeshFailed(Exception):
    ...


class AddFloatingIPFailed(Exception):
    ...


class RemoveFloatingIPFailed(Exception):
    ...


class CreateVXLANInterfaceFailed(Exception):
    ...


class CreateNetworkNamespaceFailed(Exception):
    ...


class HashFailed(Exception):
    ...


class ListingInterfaceAddressesFailed(Exception):
    ...


class OperationTimeout(Exception):
    """Raised when a cluster operation does not reach a terminal state
    within the requested timeout."""
    ...


class NetworkOperationFailed(Exception):
    """Raised by ``op.raise_for_error()`` when a cluster operation
    reached ``STATE_ERROR``. Carries the persisted ``ErrorReport`` as
    an attribute so callers can branch on its stable ``code`` field."""

    def __init__(self, error_report):
        super().__init__()
        self.error_report = error_report

    def __str__(self):
        return f'{self.error_report.code}: {self.error_report.message}'


class ProcessExecutionError(Exception):
    def __init__(self, stdout=None, stderr=None, exit_code=None, cmd=None):
        super().__init__(stdout, stderr, exit_code, cmd)
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout
        self.cmd = cmd
