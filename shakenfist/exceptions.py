from typing import Any


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


class AffinityConstraintUnsatisfiable(LowResourceException):
    """No candidate node satisfies a hard affinity constraint.

    A subclass of LowResourceException rather than a sibling, because
    preflight catches LowResourceException to redirect an instance to
    another node, and that is exactly the right behaviour for a
    constraint some other node may satisfy. A sibling would escape that
    handler as a traceback.

    The create path distinguishes them, because the two mean different
    things to a caller: LowResourceException is 507, the cluster is
    full, while this is 409, the request conflicts with the current
    state of the cluster. Answering 507 here would tell an operator to
    add capacity for a tag nothing carries.
    """


class CapacityAdmissionDenied(SchedulerException):
    """The atomic placement admission refused to place an instance.

    Raised by ``Instance.place_instance()`` when
    ``mariadb.admit_instance_placement()`` reports a guarded capacity
    UPDATE which matched no row: the instance was not placed and no
    counter moved. ``failing_stage`` names the guard which refused
    ('cluster', 'claim' or 'node') and ``dimensions`` carries the
    per-dimension limit/used/requested numbers read back after the
    rollback, so a caller walking the scheduler's candidate list can
    say why each candidate was refused.

    A denial is not a failure: it means the cluster is full, not that
    the database was unreachable. Those are told apart by the RPC's
    separate ``success`` and ``admitted`` fields, and the unreachable
    case raises WriteException instead."""

    def __init__(self, failing_stage: str,
                 dimensions: list[dict[str, Any]]) -> None:
        super().__init__(failing_stage)
        self.failing_stage = failing_stage
        self.dimensions = dimensions

    def __str__(self) -> str:
        exceeded = [d['dimension'] for d in self.dimensions
                    if d.get('exceeded')]
        if not exceeded:
            exceeded = [d['dimension'] for d in self.dimensions]
        if not exceeded:
            return f'{self.failing_stage} capacity guard refused placement'
        return (f'{self.failing_stage} capacity guard refused placement: '
                f'{", ".join(exceeded)}')

    @property
    def demand_only(self) -> bool:
        """True when only the D13 demand feedforward refused this node.

        The demand term exists to spread correlated placement bursts
        across nodes, not to bound capacity, so a walker whose every
        candidate was refused on demand alone may retry the walk with
        the demand clause waived: real capacity was free everywhere,
        and there is no quieter node to spread to. A denial with any
        real dimension exceeded (or from the cluster or claim stage,
        which have no demand term) must never be waived."""
        if self.failing_stage != 'node':
            return False
        exceeded = {d['dimension'] for d in self.dimensions
                    if d.get('exceeded')}
        return exceeded == {'demand'}


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


class FederationException(Exception):
    """An identity token could not be turned into a grant.

    Subclassed rather than flattened so the exchange endpoint can
    decide what to tell an unauthenticated caller and what to keep to
    the audit log: which claim failed is useful to a namespace owner
    reading their events, and an oracle to somebody probing.
    """


class UntrustedIssuer(FederationException):
    """No configured trusted issuer matches the token's iss claim."""


class TokenValidationFailed(FederationException):
    """Signature, audience, issuer, or lifetime verification failed."""


class ClaimMismatch(FederationException):
    """The token is genuine, but does not satisfy the rule's claims."""


class TokenReplayed(FederationException):
    """This token has already been exchanged through this rule.

    Per (token, rule) rather than per token: exchanging one identity
    against two rules to reach two namespaces is a legitimate pattern
    the CI conductor design depends on, while re-exchanging the same
    identity against the same rule is not.
    """


class RateLimited(FederationException):
    """Too many federated exchange attempts from one source address."""


class JWKSTrustAnchorUnusable(FederationException):
    """FEDERATION_JWKS_CA_BUNDLE names a file which cannot be loaded.

    Deliberately not a TokenValidationFailed. This is our
    misconfiguration and not the caller's token, and answering 401
    would tell somebody holding a perfectly good token that it had been
    rejected, sending them to their identity provider to look for a
    fault which is in our config file. The exchange answers 503.
    """


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


class InvalidMaxVersions(ArtifactException):
    """A max_versions which is not a whole number of versions to keep.

    Carries a message suitable for returning to an API caller: every
    raising site is reached from a request body.
    """
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
    """The privexec hash file helper failed to hash a file.

    Carries the HashFileReply error details so that callers can tell a
    missing file apart from a failing hasher or dying disk (issue 3744).
    """

    def __init__(self, error: str, error_text: str, path: str,
                 algorithm: str) -> None:
        super().__init__(
            f'{error}: {error_text} (path={path}, algorithm={algorithm})')
        self.error = error
        self.error_text = error_text
        self.path = path
        self.algorithm = algorithm


class ListingInterfaceAddressesFailed(Exception):
    ...


class OperationTimeout(Exception):
    """Raised when a cluster operation does not reach a terminal state
    within the requested timeout."""
    ...


class ClusterOperationEnqueueFailed(Exception):
    """Raised when a cluster operation could not be written to the
    database, so the work it describes will never happen.

    This must never be swallowed. The create_and_enqueue() wrappers
    return an operation uuid to their caller, and an API request which
    returns a uuid for an operation that was never written hands the
    client a phantom to poll while the work is silently dropped
    (issue 3631)."""
    ...


class InvalidCoalescibleEnqueue(Exception):
    """Raised when a task declared coalescible is enqueued somewhere the
    coalescing key cannot distinguish it from a sibling doing different
    work.

    Neither dedup path keys on the queue -- ``cluster_operations`` has no
    queue column -- so the coalescing key has to do that job instead. It
    does on the cluster-wide network-node queue, where one elected
    worker drains everything, and on a per-node queue when the key names
    ``node_uuid`` and the operation carries one. A coalescible task
    enqueued anywhere else is folded across nodes and one host's work is
    silently never applied. This is a programming error in the
    declaration or the call site, not a runtime condition, and it is
    raised rather than logged because the damage it prevents (a stale
    FDB on one hypervisor) is invisible until something else fails much
    later."""
    ...


class CoalescingUnavailable(Exception):
    """Raised when the database service cannot answer a coalescing query.

    Today that means only one thing: a rolling upgrade in which the
    ``sf-database`` on the other end predates the V2 coalescing RPCs
    and answers ``UNIMPLEMENTED``. It is a distinct exception rather
    than a ``None``/``[]`` return so the fold can record
    ``coalescing_unavailable`` instead of ``ran`` with nothing folded.
    Those two are indistinguishable in ``queue-wait-report.py``
    otherwise, and "a fold that ran and matched nothing" reading the
    same as "coalescing is switched off" is exactly the ambiguity
    #3878 hid behind for three months.

    Callers catch it. It never propagates to a user: the enqueue side
    treats it as "no existing op" and inserts one, the fold side skips
    and runs the task. Losing the optimisation for the length of an
    upgrade is the intended outcome -- see decision 1 of
    docs/plans/PLAN-queue-performance-phase-11-multi-column-key.md for
    why a V1 fallback is not."""
    ...


class NetworkOperationFailed(Exception):
    """Raised by ``op.raise_for_error()`` when a cluster operation
    reached ``STATE_ERROR``. Carries the persisted ``ErrorReport`` as
    an attribute so callers can branch on its stable ``code`` field."""

    def __init__(self, error_report: Any) -> None:
        super().__init__()
        self.error_report = error_report

    def __str__(self) -> str:
        return f'{self.error_report.code}: {self.error_report.message}'


class ProcessExecutionError(Exception):
    def __init__(self, stdout: Any = None, stderr: Any = None,
                 exit_code: Any = None, cmd: Any = None) -> None:
        super().__init__(stdout, stderr, exit_code, cmd)
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout
        self.cmd = cmd


class InvalidAPIDeclaration(Exception):
    """An endpoint's swagger_helper() parameter declaration is malformed.

    Raised at import time. These declarations describe the published
    API and are the input to request validation, so a malformed one
    must stop the process rather than produce a wrong specification.
    """
