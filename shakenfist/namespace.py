# Copyright 2019 Michael Still and contributors
import secrets
import string
import time
from typing import Optional

from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import mapping_rule
from shakenfist import mariadb
from shakenfist import namespace_claim
from shakenfist import namespace_key
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.eventlog import add_event
from shakenfist.schema.namespace_attributes import NamespaceAttributesData
from shakenfist.schema.namespace_data import NamespaceData
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import access_tokens
from shakenfist.util import callstack as util_callstack
from shakenfist.util import credentials


LOG, _ = logs.setup(__name__)


class Namespace(dbo):
    object_type = ObjectType.NAMESPACE
    initial_version = 6
    current_version = 7

    # docs/developer_guide/state_machine.md has a description of these states.
    ACTIVE_STATES = {dbo.STATE_CREATED}

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
    }

    def __init__(self, data) -> None:
        if isinstance(data, dict):
            # Legacy dict format from etcd — convert to Pydantic
            data = NamespaceData(
                name=data.get('uuid', data.get('name')),
                version=data.get('version', self.current_version)
            )

        data = self.upgrade_pydantic_data(data, NamespaceData)

        # Namespace uses name as identifier, not a UUID.
        self._DatabaseBackedObject__uuid = data.name  # type: ignore
        self._DatabaseBackedObject__version = data.version  # type: ignore
        self._DatabaseBackedObject__in_memory_only = False  # type: ignore
        self.log = LOG.with_fields({self.object_type: data.name})

        # Lazy-load attributes from MariaDB
        self.__attributes: Optional[NamespaceAttributesData] = None
        self.__attributes_loaded: bool = False

    @property
    def uuid(self) -> str:
        """Return the Namespace's identifier (name).

        This overrides the base class to return a string instead of uuid.UUID because
        namespaces use their name as their identifier for historical reasons.
        """
        return self._DatabaseBackedObject__uuid  # type: ignore

    def _load_attributes(self) -> Optional[NamespaceAttributesData]:
        """Load attributes from MariaDB."""
        if not self.__attributes_loaded:
            self.__attributes = mariadb.get_namespace_attributes(self.uuid)
            self.__attributes_loaded = True
        return self.__attributes

    def _ensure_attributes(self) -> NamespaceAttributesData:
        """Ensure attributes record exists, creating if needed."""
        attrs = self._load_attributes()
        if attrs is None:
            attrs = NamespaceAttributesData(name=self.uuid)
            if not mariadb.create_namespace_attributes(attrs):
                # Another thread/process created first; reload
                attrs = mariadb.get_namespace_attributes(self.uuid)
            self.__attributes = attrs
        return attrs

    def _save_attributes(self, fields: Optional[list[str]]) -> None:
        """Persist the named attribute fields to MariaDB.

        fields is deliberately required: callers must name exactly the
        fields they changed so concurrent writers of other attributes
        on the same row (keys against trust, which hold different
        attribute locks) cannot lose their committed columns to this
        writer's read-modify-write. None writes every column and is
        reserved for row creation and upgrade persistence.
        """
        if self.__attributes is not None:
            mariadb.update_namespace_attributes(self.__attributes, fields=fields)

    def _invalidate_attributes(self) -> None:
        """Force reload of attributes on next access."""
        self.__attributes_loaded = False
        self.__attributes = None

    @classmethod
    def _upgrade_step_6_to_7(cls, static_values):
        ...

    @classmethod
    def _persist_pydantic_upgrade(cls, data: NamespaceData) -> None:  # type: ignore[override]
        """Persist an upgraded NamespaceData to MariaDB."""
        # Delete and recreate since we can't update the primary key or version in-place easily.
        mariadb.delete_namespace(data.name)
        mariadb.create_namespace(data.name, data.version)

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        """Create a namespace record in MariaDB."""
        name = metadata.get('uuid', object_uuid)
        version = metadata.get('version', cls.current_version)
        mariadb.create_namespace(name, version)
        add_event(EVENT_TYPE_AUDIT, cls.object_type, name, 'db record created',
                  extra={'version': version})

    @classmethod
    def _db_get(cls, object_uuid) -> Optional[NamespaceData]:
        """Get namespace static values from MariaDB."""
        data = mariadb.get_namespace(object_uuid)
        if data is None:
            return None

        if data.version != cls.current_version:
            if not cls.upgrade_supported:
                from shakenfist import exceptions
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {data}')
        return data

    @classmethod
    def from_db(cls, identifier, suppress_failure_audit=False):
        """Load a Namespace from the database."""
        if not identifier:
            return None

        data = cls._db_get(identifier)
        if not data:
            if not suppress_failure_audit:
                add_event(EVENT_TYPE_AUDIT, cls.object_type, str(identifier),
                          'attempt to lookup non-existent object',
                          extra={'caller': util_callstack.get_caller(offset=-3)})
            return None

        return cls(data)

    @classmethod
    def new(cls, name):
        n = Namespace.from_db(name, suppress_failure_audit=True)
        if n:
            return n

        cls._db_create(name, {'uuid': name, 'version': cls.current_version})
        n = Namespace.from_db(name)
        n.state = cls.STATE_CREATED

        # Initialize attributes in MariaDB
        attrs = n._ensure_attributes()
        attrs.trust = ['system']
        n._save_attributes(fields=['trust'])
        return n

    @property
    def keys(self):
        """This namespace's usable keys, in the historical wire shape.

        Keys are NamespaceKey objects since phase 2 of the auth
        federation plan, but the shape returned here is unchanged: a
        dict of key name to {'key': hash, 'nonce': ..., 'expiry': ...},
        with expired keys omitted and 'expiry' absent entirely for keys
        which never expire.

        Two things did change. The read is a single indexed listing of
        the (namespace, name) index instead of a whole
        namespace_attributes row load, and the expiry filter is pushed
        into SQL rather than applied in Python -- so /auth no longer
        bcrypt compares keys it was always going to reject. The legacy
        namespace_attributes.keys JSON column is neither read nor
        written any more; it is vestigial until a later schema version
        drops it.

        This accessor hands out hashes and nonces because /auth and
        verify_token need them. Nothing which does not authenticate a
        request should use it -- external_view() is the operator
        visible shape.

        The 'key' and 'nonce' values are SecretStr, deliberately left
        wrapped rather than unwrapped into this dict. An untyped dict is
        exactly the shape which stringifies into a log line without
        anything objecting, and this is the highest traffic secret path
        in the system, so the protection is kept all the way to the two
        consumers -- the bcrypt comparison and token minting, both in
        /auth -- which unwrap at the point of use. Callers reading only
        key names (there are several) are unaffected.
        """
        nonced_keys = {}
        for key, attrs in namespace_key.keys_with_attributes(
                self.uuid, now=time.time()):
            entry = {'key': attrs.key, 'nonce': attrs.nonce}
            if attrs.expiry is not None:
                entry['expiry'] = attrs.expiry
            nonced_keys[key.name] = entry

        return {'nonced_keys': nonced_keys}

    def lookup_key(self, name):
        """Point read one of this namespace's keys, honouring expiry.

        Token validation names exactly one key per request via the
        JWT's "<namespace>:<keyname>" identity string, so it gets a
        single indexed read of the unique (namespace, name) index with
        the attributes row joined in, rather than a listing.

        Returns the key's attributes, or None if there is no such key
        or the key has expired. Those two cases are deliberately
        indistinguishable to the caller, exactly as they were when the
        whole-blob accessor filtered expired entries out of the dict it
        returned.

        The expiry comparison happens here rather than in SQL because
        the point read deliberately returns expired keys so that other
        callers can tell the two cases apart. It uses this module's
        clock so that it agrees with the `keys` accessor.
        """
        row = mariadb.get_namespace_key_by_name(self.uuid, name)
        if not row:
            return None

        _, attrs = row
        if attrs.expiry is not None and attrs.expiry <= time.time():
            return None
        return attrs

    def add_key(self, name, value, expiry=None, scopes=None):
        """Create one of this namespace's keys, or rotate it if it exists.

        Adding a key whose name is already in use has always silently
        overwritten it, which is a rotation: a new hash, a new nonce
        (so outstanding tokens for the old secret stop validating) and
        the expiry replaced by whatever this call passes, including no
        expiry at all. NamespaceKey.new() implements exactly that.

        The whole-blob read-modify-write under a per-namespace cluster
        lock is gone. Concurrent adds of the same name are now
        arbitrated by the unique (namespace, name) index, with the
        loser of the race falling back to the rotation it would have
        performed anyway.

        ``scopes`` of None records no scopes, which mints wildcard
        tokens. That is the right default for an operator creating a
        key by hand, and the wrong one when the caller is itself
        scoped -- see the REST layer, which passes its own scopes down
        so a key cannot be minted with more privilege than the caller
        creating it.

        Returns the new nonce, as it always has, wrapped as a SecretStr
        to match NamespaceKey.rotate(). Only one caller uses the return
        value -- get_api_token() below, which hands it straight to
        create_token() and so never needs it unwrapped.

        That nonce comes from the mint rather than from the key's nonce
        property. The property re-reads the attributes row and is
        Optional because a concurrent hard delete can empty it, so
        returning it would make this Optional too, and create_token()
        declares a SecretStr and unwraps immediately. Reading the value
        the mint already produced is both honestly typed and one fewer
        database read on the key creation path.
        """
        key = namespace_key.NamespaceKey.new(
            self.uuid, name, value, expiry=expiry, scopes=scopes)
        if key.minted_nonce is None:
            # Unreachable: new() sets this on all three of its paths.
            # Asserted rather than assumed because the alternative is
            # create_token() raising AttributeError deep inside JWT
            # minting, where the cause is far from obvious.
            raise exceptions.WriteException(
                f'namespace key {self.uuid}:{name} minted no nonce')
        return key.minted_nonce

    def remove_key(self, name):
        """Remove one of this namespace's keys.

        Key removal has always been immediate and complete: the name
        becomes available for reuse and outstanding tokens for the key
        stop validating on their next request. That is a hard delete
        rather than the soft delete the expiry sweep performs, because
        a soft deleted key would keep its (namespace, name) row and
        re-adding the same name would then have to resurrect a deleted
        object, which objects are not permitted to do.

        Removing a key which does not exist is not an error, matching
        the previous behaviour.
        """
        key = namespace_key.NamespaceKey.from_db_by_name(self.uuid, name)
        if not key:
            return
        key.hard_delete()

    @property
    def trust(self):
        attrs = self._load_attributes()
        if not attrs:
            return []
        return list(attrs.trust)

    def add_trust(self, namespace):
        with self.get_lock_attr('trust', 'Add trust'):
            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            if namespace not in attrs.trust:
                attrs.trust = list(attrs.trust) + [namespace]
                self._save_attributes(fields=['trust'])

    def remove_trust(self, namespace):
        with self.get_lock_attr('trust', 'Remove trust'):
            # You cannot remove the trust of the system namespace, because if you could
            # then the cluster admin wouldn't see your resources.
            if namespace == 'system':
                return

            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            if namespace in attrs.trust:
                new_trust = [n for n in attrs.trust if n != namespace]
                attrs.trust = new_trust
                self._save_attributes(fields=['trust'])

    def hard_delete(self):
        # Keys are owned by the namespace and are meaningless without
        # it, so they go too. This used to happen for free, because the
        # keys lived in the namespace attributes row.
        # Every key, whatever its state or expiry -- this is the last
        # chance to remove them.
        for key, _ in namespace_key.keys_with_attributes(
                self.uuid, include_expired=True):
            key.hard_delete()

        # Mapping rules are owned the same way and go for the same
        # reason. Leaving one behind would be worse than leaving a key
        # behind: a rule names its namespace by name, so if the name
        # were ever recreated the new owner would inherit a federation
        # trust they never asked for.
        for rule in mapping_rule.rules_in_namespace(self.uuid):
            rule.hard_delete()

        # Capacity claims are owned the same way, and leaving one behind
        # is worse than leaving a key or a rule behind: a claim holds
        # cluster capacity in cluster_capacity.claimed_*, and with its
        # namespace gone nothing would ever release it. The cluster would
        # simply have less capacity than it has, permanently, with no
        # object left to explain why. Deleting through the claim object
        # rather than the row is what returns the capacity -- see
        # NamespaceClaim.hard_delete().
        for claim in namespace_claim.claims_in_namespace(self.uuid):
            claim.hard_delete()

        mariadb.delete_namespace_attributes(self.uuid)
        mariadb.delete_namespace(self.uuid)
        super().hard_delete()

    def external_view(self):
        # If this is an external view, then mix back in attributes that users expect
        retval = self._external_view()
        del retval['uuid']
        retval.update({
            'name': self.uuid,
            'keys': [],
            'trust': {
                'full': self.trust
            }
        })

        # Mix in key names
        keys = self.keys
        for k in keys.get('nonced_keys', {}):
            retval['keys'].append(k)

        return retval


class Namespaces(dbo_iter):
    base_object = Namespace

    def _resolve_prefilter_to_states(self) -> set[str]:
        """Preserve the pre-phase-5 behaviour: when no prefilter is set,
        do not filter on state (return every namespace). The base-class
        default of ACTIVE_STATES is used when a prefilter is explicitly given.
        """
        if self.prefilter is None:
            return set()
        return super()._resolve_prefilter_to_states()

    def __iter__(self):
        # NamespaceData has no .uuid attribute (namespace uses name as
        # primary key), so we iterate _find directly rather than going
        # through get_iterator() which assumes a .uuid field on the data.
        # Sort by name for stable output — the pre-phase-5 path used
        # mariadb.get_all_namespace_names() which returned names in
        # alphabetical order, and at least one REST test asserts that.
        target_states = self._resolve_prefilter_to_states()
        criteria = ObjectFilterCriteria(states=list(target_states), namespace=None)
        for data in sorted(self._find(criteria), key=lambda d: d.name):
            n = Namespace(data)
            if not n:
                continue
            out = self.apply_filters(n)
            if out:
                yield out


CACHED_TOKENS = {}


def get_api_token(base_url, namespace='system'):
    if namespace in CACHED_TOKENS:
        expiry, access_token = CACHED_TOKENS[namespace]
        if expiry - time.time() > 15:
            return 'Bearer %s' % access_token

    auth_url = base_url + '/auth'
    LOG.info('Fetching %s auth token from %s', namespace, auth_url)

    ns = Namespace.from_db(namespace)

    # Service keys are cluster generated, so they carry the recognisable
    # sfk_ format like any other secret we mint ourselves.
    key = credentials.generate()
    unique = ''.join(secrets.choice(string.ascii_letters) for i in range(5))
    keyname = '_service_key_%s' % unique
    expiry = time.time() + 300
    nonce = ns.add_key(keyname, key, expiry=expiry)

    # Cheat and don't actually call the auth API to create a token, just call its
    # underlying code, thus saving a network round trip.
    token = access_tokens.create_token(ns, keyname, nonce, duration=5)

    CACHED_TOKENS[namespace] = (expiry, token['access_token'])
    return 'Bearer %s' % token['access_token']


def namespace_is_trusted(namespace, requestor):
    if namespace == requestor:
        return True

    ns = Namespace.from_db(namespace, suppress_failure_audit=True)
    if not ns:
        return False

    if requestor not in ns.trust:
        return False

    return True
