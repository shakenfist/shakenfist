# Copyright 2019 Michael Still and contributors
#
# A NamespaceClaim is a namespace's promise of aggregate capacity from
# the cluster: so many cpus, so much memory and so much disk, guaranteed
# against cluster_capacity for as long as the claim is active. Creating
# one is itself an admission decision -- the cluster has to be able to
# keep the promise -- which is why every mutation here goes through the
# guarded transactions in mariadb.py rather than writing rows directly.
#
# See docs/plans/PLAN-scheduler-reservations-phase-04-claims-api.md.
#
# Two states, and they are two different facts (D2)
# -------------------------------------------------
#
# The object's baseobject-managed state, in object_states, is
# *existence*: created, deleted, error, exactly like every other Shaken
# Fist object. The namespace_claims.state column is *coverage*: active
# or expired, owned by the reconciler's expiry sweep and by the claim
# RPCs. Nothing may write coverage into object_states or existence into
# namespace_claims.state.
#
# The consequences of that, which are what keep the two from quietly
# becoming one: an expired claim is still a `created` object, a deleted
# claim has no row at all, and external_view() publishes the two under
# distinct names.
#
# Coverage is not routed through object_states because
# _active_claim_for_namespace() in mariadb.py runs on *every instance
# admission* and filters `state = 'active' AND expires_at > NOW()`
# against the claims table's own index. Moving that predicate to
# object_states would turn the hot probe into a join across the two uuid
# storage conventions -- object_states.object_uuid holds the dashed 36
# character form, sa.Uuid columns here hold undashed CHAR(32) (CLAUDE.md
# pitfall 6) -- which is both slower and the exact shape this codebase
# has been burned by. Do not "simplify" this into one state.

from typing import Any
from typing import Optional
from uuid import UUID
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.schema.namespace_claim_data import NamespaceClaimData
from shakenfist.schema.object_types import ObjectType


LOG, _ = logs.setup(__name__)


# The coverage states, mirrored from mariadb so callers of this module do
# not have to reach into the database layer to name one. mariadb owns the
# values; these are the object-side spelling of the same strings.
COVERAGE_ACTIVE = mariadb.CLAIM_STATE_ACTIVE
COVERAGE_EXPIRED = 'expired'


class ClaimRefused(Exception):
    """The cluster declined to make or change this claim.

    Nothing failed: the guarded transaction ran and decided no. The
    reason and, for a capacity refusal, the per-dimension detail are
    carried through so a caller can say *why* rather than reporting a
    generic failure -- which is the whole point of a capacity guard that
    tells you which dimension it was that did not fit.

    ``reason`` is one of the refusal reasons the mariadb claim wrappers
    document: 'capacity', 'no_cluster_capacity', 'exists',
    'below_usage', 'not_found', 'not_active' or 'conflict'.
    """

    def __init__(
            self, reason: str,
            dimensions: Optional[
                list[mariadb.CapacityDimensionDetailDict]] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.dimensions = dimensions or []


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


class NamespaceClaim(dbo):
    object_type = ObjectType.NAMESPACE_CLAIM
    initial_version = 1
    current_version = 1

    # A claim is usable while it exists and has not been deleted.
    # Coverage -- whether it is still active or has expired -- is a
    # separate fact and deliberately not consulted here.
    ACTIVE_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED}
    HEALTHY_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED}

    # docs/developer_guide/state_machine.md has a description of these
    # states. Claim mutations are single guarded transactions, so there
    # is no error state.
    #
    # There is no soft delete: see hard_delete(). The deleted state is
    # still reachable because the orphan reconciliation sweep writes it
    # directly (via mariadb.set_state) to repair a claim row whose state
    # row was lost, which is then collected by the standard reaper.
    state_targets = {
        None: (dbo.STATE_INITIAL,),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED,),
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values: dict[str, Any]) -> None:
        self.upgrade(static_values)

        super().__init__(static_values['uuid'], static_values.get('version'))

        self.__namespace = static_values['namespace']

    @classmethod
    def _static_values_to_dict(cls, data: NamespaceClaimData) -> dict[str, Any]:
        return {
            'uuid': str(data.uuid),
            'namespace': data.namespace,
            'version': data.version
        }

    @classmethod
    def _static_data_from_row(
            cls, row: mariadb.NamespaceClaimRow) -> NamespaceClaimData:
        """The static half of a claim row.

        The table carries no version column -- a claim's shape is owned
        by the table schema, which ensure_schema() migrates -- so the
        reading build's current_version is what every claim reports.
        """
        return NamespaceClaimData(
            uuid=_as_uuid(row['uuid']),
            namespace=row['namespace'],
            version=cls.current_version)

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        """Write the audit events for a new claim.

        Deliberately does *not* write the claim row, which is what the
        equivalent method on every other object does. A claim row is
        created by a guarded transaction which can refuse, and the
        refusal has to be known before an object_states row is written:
        a state row for a claim that was never granted is precisely the
        stateless-zombie shape issue 3588 was about. new() therefore
        creates the row first and calls this afterwards.
        """
        super()._db_create(str(_as_uuid(object_uuid)), metadata)

    @classmethod
    def _db_get(cls, object_uuid) -> Optional[dict[str, Any]]:
        """Get NamespaceClaim static values from MariaDB."""
        row = mariadb.get_namespace_claim(str(_as_uuid(object_uuid)))
        if not row:
            return None
        return cls._static_values_to_dict(cls._static_data_from_row(row))

    @classmethod
    def from_static_data(cls, data: NamespaceClaimData) -> 'NamespaceClaim':
        """Build a claim object from static values already in hand."""
        return cls(cls._static_values_to_dict(data))

    @classmethod
    def from_row(cls, row: mariadb.NamespaceClaimRow) -> 'NamespaceClaim':
        """Build a claim object from a row already read from the DB.

        For callers which have listed claims and so already hold the
        rows; rehydrating via from_db() would re-read what we are
        looking at.
        """
        return cls.from_static_data(cls._static_data_from_row(row))

    @classmethod
    def new(cls, namespace: str, limit_cpus: int, limit_memory_mb: int,
            limit_disk_gb: int, expires_in_seconds: int) -> 'NamespaceClaim':
        """Claim aggregate capacity for a namespace.

        The claim row is written by mariadb.create_namespace_claim(),
        which is a guarded admission decision against the cluster
        capacity singleton and which also migrates the namespace's
        existing drawdown into the new claim (D3). Only once it has said
        yes does the object get a state row, so a refused claim leaves
        nothing behind.

        ``expires_in_seconds`` is a duration, not a timestamp: the
        expiry is computed from the *server's* clock, because that is
        the only clock the expiry sweep ever compares against.

        Raises ClaimRefused when the cluster declines, carrying the
        reason and the per-dimension detail.
        """
        claim_uuid = str(uuid4())
        result = mariadb.create_namespace_claim(
            claim_uuid, namespace, limit_cpus, limit_memory_mb,
            limit_disk_gb, expires_in_seconds)

        if not result['created']:
            if not result['success']:
                raise exceptions.WriteException(
                    'namespace claim for %s could not be written: %s'
                    % (namespace, result['error']))
            raise ClaimRefused(result['refused_reason'],
                               dimensions=result['dimensions'])

        cls._db_create(claim_uuid, {
            'uuid': claim_uuid,
            'namespace': namespace,
            'version': cls.current_version,
            'limit_cpus': limit_cpus,
            'limit_memory_mb': limit_memory_mb,
            'limit_disk_gb': limit_disk_gb,
            'expires_in_seconds': expires_in_seconds
        })

        c = cls.from_db(claim_uuid, suppress_failure_audit=True)
        if not c:
            # The guarded transaction committed and reported the row it
            # wrote, so it is there. If we cannot read it back the
            # database has changed under us, and returning a claim
            # object we could not hydrate would be worse than saying so.
            raise exceptions.WriteException(
                f'namespace claim {claim_uuid} was not persisted')

        c.state = cls.STATE_INITIAL
        c.state = cls.STATE_CREATED
        return c

    # Static values
    @property
    def namespace(self) -> str:
        return self.__namespace

    # Mutable values. These all live in the claim's own row rather than a
    # separate attributes table, because the row is written by guarded
    # UPDATE statements and by the reconciler; there is nothing for an
    # attributes row to hold that would not immediately be stale.
    def _row(self) -> Optional[mariadb.NamespaceClaimRow]:
        return mariadb.get_namespace_claim(str(self.uuid))

    @property
    def limits(self) -> Optional[dict[str, int]]:
        """What this claim promises, in each dimension."""
        row = self._row()
        if not row:
            return None
        return {
            'cpus': row['limit_cpus'],
            'memory_mb': row['limit_memory_mb'],
            'disk_gb': row['limit_disk_gb']
        }

    @property
    def used(self) -> Optional[dict[str, int]]:
        """What the namespace has drawn down against this claim."""
        row = self._row()
        if not row:
            return None
        return {
            'cpus': row['used_cpus'],
            'memory_mb': row['used_memory_mb'],
            'disk_gb': row['used_disk_gb']
        }

    @property
    def coverage_state(self) -> Optional[str]:
        """Whether this claim still covers placements: active or expired.

        Not the object's state. See the note at the top of this module
        before you are tempted to make these one thing.
        """
        row = self._row()
        if not row:
            return None
        return row['state']

    @property
    def expires_at(self) -> Optional[float]:
        """Epoch seconds, from the server's clock."""
        row = self._row()
        if not row:
            return None
        return row['expires_at']

    def update(self, limit_cpus: int = 0, limit_memory_mb: int = 0,
               limit_disk_gb: int = 0, expires_in_seconds: int = 0,
               fields: Optional[list[str]] = None) -> None:
        """Grow, shrink or re-date this claim (D8).

        ``fields`` names which of the arguments this call actually sets,
        exactly as the update_*_attributes field masks do (CLAUDE.md
        pitfall 3): without it there is no way to tell a deliberate zero
        from an argument the caller never passed, and an unmasked write
        would shrink every dimension the caller did not mention to zero.

        Growing is guarded against the cluster exactly as creation is,
        shrinking is permitted down to the claim's current usage and no
        further, and one call may do both. Raises ClaimRefused when the
        cluster or the claim's own usage declines the change.
        """
        if fields is None:
            raise ValueError(
                'update() requires a field mask naming what it sets')

        result = mariadb.update_namespace_claim(
            str(self.uuid), fields, limit_cpus=limit_cpus,
            limit_memory_mb=limit_memory_mb, limit_disk_gb=limit_disk_gb,
            expires_in_seconds=expires_in_seconds)

        if not result['updated']:
            if not result['success']:
                raise exceptions.WriteException(
                    'namespace claim %s could not be updated: %s'
                    % (self.uuid, result['error']))
            raise ClaimRefused(result['refused_reason'],
                               dimensions=result['dimensions'])

        self.add_event(
            EVENT_TYPE_MUTATE, 'updated namespace claim',
            extra={'fields': list(fields), 'limit_cpus': limit_cpus,
                   'limit_memory_mb': limit_memory_mb,
                   'limit_disk_gb': limit_disk_gb,
                   'expires_in_seconds': expires_in_seconds})

    def external_view(self) -> dict[str, Any]:
        """The operator visible view of a claim.

        ``state`` is the object's existence state, where every other
        object publishes it. Coverage is published separately as
        ``coverage_state``, because they are two different facts (D2)
        and a view which collapsed them would be the first step towards
        the code doing the same. An expired claim reads as
        ``state: created, coverage_state: expired``.
        """
        retval = self._external_view()

        # One read rather than six; every field below comes from the
        # same row.
        row = self._row()

        retval.update({
            'namespace': self.namespace,
            'limit_cpus': row['limit_cpus'] if row else None,
            'limit_memory_mb': row['limit_memory_mb'] if row else None,
            'limit_disk_gb': row['limit_disk_gb'] if row else None,
            'used_cpus': row['used_cpus'] if row else None,
            'used_memory_mb': row['used_memory_mb'] if row else None,
            'used_disk_gb': row['used_disk_gb'] if row else None,
            'coverage_state': row['state'] if row else None,
            'expires_at': row['expires_at'] if row else None,
            'updated_at': row['updated_at'] if row else None
        })
        return retval

    def hard_delete(self) -> None:
        """Delete the claim, returning what it held to the cluster.

        There is no soft delete of a claim, and that is deliberate. A
        claim in a `deleted` state whose row still held cluster capacity
        would be an accounting lie for however long the reaper took to
        get to it -- the capacity would be promised to a namespace that
        no longer wanted it and refused to everybody else. Removal is
        therefore one operation: mariadb.delete_namespace_claim() gives
        the capacity back inside the same transaction that removes the
        row.

        Deleting twice is harmless; the second call finds no row and
        returns nothing. A delete that *failed*, though, is not the same
        as one that found nothing, and this raises rather than carrying
        on: the row survives holding cluster_capacity.claimed_*, and
        tearing down the state row on top of that would strand the
        capacity where nothing can find it. Raising leaves the object
        whole, so the reaper's next pass tries again -- and it matches
        new() and update(), which both raise when the write fails.

        The callers are ready for it. The reaper wraps each hard_delete()
        in ignore_exception, the REST endpoint answers 500 rather than a
        200 that says capacity was returned when it was not, and
        Namespace.hard_delete() aborts partway, which leaves the
        namespace's own state row in place for the retry.
        """
        result = mariadb.delete_namespace_claim(str(self.uuid))

        if not result['success']:
            raise exceptions.WriteException(
                f'failed to delete namespace claim {self.uuid}, so its '
                f'capacity is still held: {result["error"]}')

        if result['deleted']:
            returned = {
                'claim': str(self.uuid),
                'returned_cpus': result['returned_cpus'],
                'returned_memory_mb': result['returned_memory_mb'],
                'returned_disk_gb': result['returned_disk_gb'],
                'clamped': result['clamped']
            }
            self.log.with_fields(returned).info(
                'Namespace claim deleted, capacity returned to the cluster')

            # Recorded against the namespace rather than the claim,
            # because super().hard_delete() is about to remove this
            # object's events and the question "what happened to my
            # namespace's claim" outlives the claim.
            eventlog.add_event(
                EVENT_TYPE_AUDIT, 'namespace', self.namespace,
                'namespace claim deleted, capacity returned',
                extra=returned, suppress_event_logging=True)

        super().hard_delete()


class NamespaceClaims(dbo_iter):
    base_object = NamespaceClaim

    def get_iterator(self):
        # The base iterator already carries a namespace; honouring it
        # here pushes the restriction into SQL, where the claims table's
        # namespace index serves it, rather than listing every claim in
        # the cluster and discarding most of them in Python. The common
        # caller is a namespace owner listing their own claims.
        #
        # With no namespace there is nothing to push down, and the
        # unfiltered listing is the cluster admin view.
        if self.namespace is None:
            rows = mariadb.get_namespace_claims()  # nopushdown: no namespace to filter on
        else:
            rows = mariadb.get_namespace_claims(self.namespace)

        for row in rows:
            yield row['uuid'], NamespaceClaim._static_values_to_dict(
                NamespaceClaim._static_data_from_row(row))

    def __iter__(self):
        """Every claim row, including any whose object state is deleted.

        That inclusion is deliberate, and it is why this does not
        consult the object state the way a state-driven iterator would.
        A claim has no soft delete, so a `deleted` state here does not
        mean "on its way out and already accounted for" -- it means a
        row that still holds cluster_capacity.claimed_* alongside a
        state row that zombie repair wrote, waiting for the reaper to
        run hard_delete() and give the capacity back. Hiding it would
        make the listing an accounting lie of exactly the kind this
        class refuses elsewhere: capacity is being held and the
        operator's only view of claims would not show it.

        So the listing and the by-uuid lookup disagree on purpose.
        arg_is_claim_ref() 404s such a claim because there is nothing
        useful an operator can do to it -- deleting it is already
        scheduled -- while the listing's job is to account for capacity,
        not to offer actions. test_a_deleted_claim_is_still_listed pins
        this, so a later reader changes it deliberately or not at all.
        """
        for _, static_values in self.get_iterator():
            c = NamespaceClaim(static_values)
            if not c:
                continue
            out = self.apply_filters(c)
            if out:
                yield out


def claims_in_namespace(namespace: str) -> list[NamespaceClaim]:
    """Every claim owned by a namespace, whatever its coverage state.

    Used by Namespace.hard_delete(), which is the last chance to remove
    them, so it deliberately filters on neither object state nor
    coverage: an expired claim still has a row, and a row left behind is
    capacity nothing can release.
    """
    return [
        NamespaceClaim.from_row(row)
        for row in mariadb.get_namespace_claims(namespace)
    ]
