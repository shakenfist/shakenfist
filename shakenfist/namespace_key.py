# Copyright 2019 Michael Still and contributors
#
# NamespaceKey is an authentication key owned by a namespace. It is the
# thing an operator names when creating an API key, and the thing a
# JWT's "<namespace>:<keyname>" identity string refers to.
#
# Keys used to be anonymous entries in the namespace_attributes "keys"
# JSON column. Phase 2 of the auth federation plan promotes them to
# first-class objects with their own tables, lifecycle, events, expiry
# and reaping -- see
# docs/plans/PLAN-auth-federation-phase-02-key-objects.md.
#
# The secret material (the base64 encoded bcrypt hash and the nonce)
# lives in the attributes row because rotation mutates it. It is never
# returned by external_view(), and never written into an event.

import base64
import time
from typing import Any
from typing import Optional
from uuid import UUID
from uuid import uuid4

import bcrypt
from pydantic import SecretStr
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as sfrandom  # noreorder

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.schema.namespace_key_attributes import NamespaceKeyAttributesData
from shakenfist.schema.namespace_key_data import NamespaceKeyData
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.object_types import ObjectType


LOG, _ = logs.setup(__name__)


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def hash_secret(plaintext_secret: str) -> str:
    """Hash a key secret exactly as Namespace.add_key() always has.

    bcrypt with a freshly generated salt at the library default cost,
    then base64 encoded and stored as a string. The encoding is load
    bearing: tokens minted before this phase were checked against
    hashes produced by precisely this expression, so it must not
    change.
    """
    return str(base64.b64encode(
        bcrypt.hashpw(plaintext_secret.encode('utf-8'), bcrypt.gensalt())),
        'utf-8')


class NamespaceKey(dbo):
    object_type = ObjectType.NAMESPACE_KEY
    initial_version = 1
    current_version = 1

    # A key is usable while it exists and has not been deleted. Expiry is
    # enforced check-at-use by callers, not by the state machine.
    ACTIVE_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED}
    HEALTHY_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED}

    # docs/developer_guide/state_machine.md has a description of these
    # states. Key operations are atomic, so there is no error state. The
    # standard reaper hard deletes soft deleted keys once they have been
    # in the deleted state for config.CLEANER_DELAY.
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
        self.__name = static_values['name']

        # The nonce this call minted, when this object came from new()
        # or rotate(). It is None on an object read from the database,
        # which is every other way of getting one.
        #
        # This exists so a caller which has just minted a key can use
        # the nonce without re-reading it. The nonce property does a
        # fresh point read and is therefore Optional -- correctly, since
        # a concurrent hard delete can empty it -- but a caller minting
        # a token from the key it just created needs a value, not a
        # maybe. See Namespace.add_key().
        self.minted_nonce: Optional[SecretStr] = None

    @classmethod
    def _static_values_to_dict(cls, data: NamespaceKeyData) -> dict[str, Any]:
        return {
            'uuid': str(data.uuid),
            'namespace': data.namespace,
            'name': data.name,
            'version': data.version
        }

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        """Create the static and attributes rows for a new key.

        Note that the base class writes the metadata dict verbatim into
        an audit event, so the hash and the nonce are stripped from the
        copy it is given -- no secret material in the event log.
        """
        _uuid = _as_uuid(object_uuid)

        loggable = {k: v for k, v in metadata.items()
                    if k not in ('key', 'nonce')}
        super()._db_create(str(_uuid), loggable)

        mariadb.create_namespace_key(NamespaceKeyData(
            uuid=_uuid,
            namespace=metadata['namespace'],
            name=metadata['name'],
            version=metadata['version']
        ))

        mariadb.create_namespace_key_attributes(NamespaceKeyAttributesData(
            uuid=_uuid,
            key=metadata['key'],
            nonce=metadata['nonce'],
            expiry=metadata.get('expiry'),
            scopes=metadata.get('scopes'),
            provenance=metadata.get('provenance')
        ))

    @classmethod
    def _db_get(cls, object_uuid) -> Optional[dict[str, Any]]:
        """Get NamespaceKey static values from MariaDB."""
        data = mariadb.get_namespace_key(_as_uuid(object_uuid))
        if not data:
            return None
        return cls._static_values_to_dict(data)

    @classmethod
    def from_static_data(cls, data: NamespaceKeyData) -> 'NamespaceKey':
        """Build a key object from a static row already read from the DB.

        For callers which have used one of the joined accessors and so
        already hold the row -- rehydrating via from_db() would be a
        second read of something we are looking at.
        """
        return cls(cls._static_values_to_dict(data))

    @classmethod
    def from_db_by_name(cls, namespace: str,
                        name: str) -> Optional['NamespaceKey']:
        """Look up a key by its unique (namespace, name) pair.

        This is a single indexed point read, which is what token
        validation does once per request. A miss is an entirely normal
        outcome for an authentication attempt, so unlike from_db() it
        does not write a "non-existent object" audit event.
        """
        row = mariadb.get_namespace_key_by_name(namespace, name)
        if not row:
            return None
        static_data, _ = row
        return cls.from_static_data(static_data)

    @classmethod
    def new(cls, namespace: str, name: str, plaintext_secret: str,
            expiry: Optional[float] = None,
            scopes: Optional[list[str]] = None,
            provenance: Optional[dict[str, Any]] = None) -> 'NamespaceKey':
        """Create a key, or rotate the existing key of that name.

        Namespace.add_key() has always overwritten an existing entry of
        the same name with a new hash, a new nonce, and whatever expiry
        the caller passed (including no expiry at all). That
        overwrite-as-rotation semantic is preserved here: outstanding
        tokens for the old secret stop validating because the nonce
        changed, but the key object, its uuid, and its event history
        survive.

        The returned object carries the nonce this call minted in
        ``minted_nonce``, on every one of the three paths out. Callers
        which need it must read that rather than the nonce property,
        which re-reads from the database and so can legitimately come
        back empty.
        """
        existing = cls.from_db_by_name(namespace, name)
        if existing:
            existing.rotate(plaintext_secret, expiry=expiry, scopes=scopes,
                            provenance=provenance)
            return existing

        key_uuid = str(uuid4())
        nonce = SecretStr(sfrandom.random_id())
        cls._db_create(key_uuid, {
            'uuid': key_uuid,
            'namespace': namespace,
            'name': name,
            'version': cls.current_version,
            # Wrapped here rather than left for the model to coerce, so
            # that the strip in _db_create() is belt and this is braces:
            # if that filter were ever removed, the audit event would
            # carry asterisks rather than the hash and the nonce.
            'key': SecretStr(hash_secret(plaintext_secret)),
            'nonce': nonce,
            'expiry': expiry,
            'scopes': scopes,
            'provenance': provenance
        })

        k = cls.from_db(key_uuid, suppress_failure_audit=True)
        if not k:
            # Another writer inserted a key of this name between the
            # lookup above and our insert, and the unique (namespace,
            # name) index rejected ours. Clean up the attributes row
            # orphaned by the rejected insert, then treat the call as
            # the rotation it would have been.
            mariadb.delete_namespace_key_attributes(_as_uuid(key_uuid))
            existing = cls.from_db_by_name(namespace, name)
            if not existing:
                raise exceptions.WriteException(
                    f'namespace key {namespace}:{name} was not persisted')
            existing.rotate(plaintext_secret, expiry=expiry, scopes=scopes,
                            provenance=provenance)
            return existing

        k.minted_nonce = nonce
        k.state = cls.STATE_INITIAL
        k.state = cls.STATE_CREATED
        return k

    def rotate(self, plaintext_secret: str, expiry: Optional[float] = None,
               scopes: Optional[list[str]] = None,
               provenance: Optional[dict[str, Any]] = None) -> SecretStr:
        """Replace the key's secret with a new hash and a new nonce.

        The whole mutable attribute set is replaced, not just the
        secret, because that is what overwriting a key of the same name
        has always done: passing no expiry clears any expiry the key
        used to have.

        Returns the new nonce, as Namespace.add_key() does today, and
        wrapped for the same reason the stored value is: a nonce tells a
        holder of captured tokens which of them are still live. It is
        also recorded on the object as ``minted_nonce``, which is how
        new() surfaces it when the caller cannot see which of create or
        rotate it performed.
        """
        _uuid = _as_uuid(self.uuid)
        nonce = SecretStr(sfrandom.random_id())

        with self.get_lock_attr('key', 'Rotate key'):
            attrs = NamespaceKeyAttributesData(
                uuid=_uuid,
                key=SecretStr(hash_secret(plaintext_secret)),
                nonce=nonce,
                expiry=expiry,
                scopes=scopes,
                provenance=provenance
            )
            if not mariadb.update_namespace_key_attributes(attrs):
                # The attributes row is written with the static row and
                # should always exist. If it somehow does not we create
                # it, because the alternative is silently leaving the
                # previous secret working.
                mariadb.create_namespace_key_attributes(attrs)

        # Note that neither the hash nor the nonce may appear here.
        self.add_event(EVENT_TYPE_MUTATE, 'rotated key',
                       extra={'expiry': expiry})
        self.minted_nonce = nonce
        return nonce

    # Static values
    @property
    def namespace(self) -> str:
        return self.__namespace

    @property
    def name(self) -> str:
        return self.__name

    # Mutable attributes
    def _attributes(self) -> Optional[NamespaceKeyAttributesData]:
        return mariadb.get_namespace_key_attributes(_as_uuid(self.uuid))

    @property
    def key(self) -> Optional[SecretStr]:
        """The base64 encoded bcrypt hash. Secret -- never externalise.

        Returned wrapped, so a caller which logs it gets asterisks. The
        only consumer needing the real bytes is the bcrypt comparison in
        /auth, which unwraps explicitly.
        """
        attrs = self._attributes()
        if not attrs:
            return None
        return attrs.key

    @property
    def nonce(self) -> Optional[SecretStr]:
        """The nonce embedded in tokens minted from this key.

        Secret in the sense that it must never leave the cluster: it is
        the revocation handle, and rotation changes it. Returned wrapped
        for the same reason as key(); the token minting path unwraps it
        into the JWT claim.
        """
        attrs = self._attributes()
        if not attrs:
            return None
        return attrs.nonce

    @property
    def expiry(self) -> Optional[float]:
        attrs = self._attributes()
        if not attrs:
            return None
        return attrs.expiry

    @property
    def scopes(self) -> Optional[list[str]]:
        attrs = self._attributes()
        if not attrs:
            return None
        return attrs.scopes

    @property
    def provenance(self) -> Optional[dict[str, Any]]:
        attrs = self._attributes()
        if not attrs:
            return None
        return attrs.provenance

    def expired(self, now: Optional[float] = None) -> bool:
        """Has this key passed its expiry?

        Enforcement is check-at-use: callers ask this question, they do
        not rely on the reaper having removed the key yet.
        """
        expiry = self.expiry
        if expiry is None:
            return False
        if now is None:
            now = time.time()
        return now > expiry

    def external_view(self) -> dict[str, Any]:
        """The operator visible view of a key.

        Deliberately without the hash and the nonce: a key's external
        view is safe to hand to anyone who can see the namespace.
        """
        retval = self._external_view()
        retval.update({
            'namespace': self.namespace,
            'name': self.name,
            'expiry': self.expiry,
            'scopes': self.scopes,
            'provenance': self.provenance
        })
        return retval

    def delete(self) -> None:
        """Soft delete. The reaper hard deletes later."""
        self.state = self.STATE_DELETED

    def hard_delete(self) -> None:
        _uuid = _as_uuid(self.uuid)
        mariadb.delete_namespace_key_attributes(_uuid)
        mariadb.delete_namespace_key(_uuid)
        super().hard_delete()


def keys_with_attributes(
        namespace: str, include_expired: bool = False,
        now: Optional[float] = None
) -> list[tuple[NamespaceKey, NamespaceKeyAttributesData]]:
    """Every key in a namespace, paired with its attributes.

    The find accessor returns each static row joined with its
    attributes row, so callers which need the secret material get it
    for free. The alternative -- iterating NamespaceKeys and then
    reading .key and .nonce -- would cost two extra point reads per
    key on /auth, which is already doing a bcrypt comparison per key.

    Expired keys are filtered out in SQL unless include_expired is
    set. Pass ``now`` when the caller owns the clock, so that its
    expiry decisions and this one cannot disagree.

    Note that this deliberately does not filter on object state. The
    only thing which soft deletes a key is the expiry sweep, and such
    a key is by construction already excluded by the expiry filter;
    user requested removal is a hard delete. Any future soft delete
    path must revisit that, here and in Namespace.lookup_key().
    """
    return [
        (NamespaceKey.from_static_data(static_data), attrs)
        for static_data, attrs in mariadb.find_namespace_keys(
            namespace, include_expired=include_expired, now=now)
    ]


class NamespaceKeys(dbo_iter):
    base_object = NamespaceKey

    def __init__(self, filters=None, prefilter=None, namespace=None,
                 suppress_failure_audit=False, include_expired=True):
        super().__init__(filters=filters, prefilter=prefilter,
                         namespace=namespace,
                         suppress_failure_audit=suppress_failure_audit)
        self.include_expired = include_expired

    def _find(self, criteria: ObjectFilterCriteria):
        # Listing one namespace's keys is served by the leading column
        # of the (namespace, name) unique index, with the expiry filter
        # pushed into SQL as well. The find accessor does not join
        # object_states though, so the state filter is applied here with
        # a point read per key.
        if criteria.namespace is None:
            # Nothing to push down -- fall back to the state indexed
            # default, which enumerates by state and hydrates.
            yield from super()._find(criteria)
            return

        states = set(criteria.states or [])
        for static_data, _ in mariadb.find_namespace_keys(
                criteria.namespace, include_expired=self.include_expired):
            if states:
                state = mariadb.get_state(
                    self.base_object.object_type, str(static_data.uuid))
                if state is None or state.value not in states:
                    continue
            yield static_data

    def _to_static_values(self, data):
        if isinstance(data, dict):
            return data
        return NamespaceKey._static_values_to_dict(data)

    def get_iterator(self):
        # The base class treats namespace='system' as "no namespace
        # filter", because for most objects the system namespace is the
        # administrator's view of everything. That is wrong for keys:
        # the system namespace owns keys of its own, and asking for them
        # must not return every other namespace's keys as well.
        target_states = self._resolve_prefilter_to_states()
        criteria = ObjectFilterCriteria(
            states=list(target_states), namespace=self.namespace)
        for data in self._find(criteria):
            if isinstance(data, dict):
                objuuid = data.get('uuid')
            else:
                objuuid = data.uuid
            yield str(objuuid), self._to_static_values(data)

    def __iter__(self):
        for _, static_values in self.get_iterator():
            k = NamespaceKey(static_values)
            if not k:
                continue

            out = self.apply_filters(k)
            if out:
                yield out
