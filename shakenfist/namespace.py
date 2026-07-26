# Copyright 2019 Michael Still and contributors
import base64
import secrets
import string
import time
from typing import Optional

import bcrypt
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as sfrandom  # noreorder

from shakenfist import mariadb
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


LOG, _ = logs.setup(__name__)


def _prune_expired_keys(nonced_keys):
    # Expired keys must be dropped whenever the keys attribute is
    # written, not just filtered on read: get_api_token() mints a
    # short-lived _service_key_* every few minutes per daemon, and
    # without pruning the stored blob grows without bound until it
    # crosses gRPC's maximum message size and namespace reads start
    # failing cluster-wide (issue #3521).
    now = time.time()
    return {k: v for k, v in nonced_keys.items()
            if 'expiry' not in v or v['expiry'] >= now}


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
                          extra={'caller': util_callstack.get_caller(offset=-3)},
                          log_as_error=True)
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
        attrs = self._load_attributes()
        if not attrs:
            return {'nonced_keys': {}}

        return {'nonced_keys': _prune_expired_keys(attrs.keys.get('nonced_keys', {}))}

    def add_key(self, name, value, expiry=None):
        encoded = str(base64.b64encode(bcrypt.hashpw(value.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
        nonce = sfrandom.random_id()

        with self.get_lock_attr('keys', 'Add key'):
            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            k = dict(attrs.keys)
            nk = _prune_expired_keys(k.get('nonced_keys', {}))
            nk[name] = {'key': encoded, 'nonce': nonce}
            if expiry:
                nk[name]['expiry'] = expiry
            k['nonced_keys'] = nk
            attrs.keys = k
            self._save_attributes(fields=['keys'])

        return nonce

    def remove_key(self, name):
        with self.get_lock_attr('keys', 'Remove key'):
            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            k = dict(attrs.keys)
            nk = _prune_expired_keys(k.get('nonced_keys', {}))
            if name in nk or len(nk) != len(k.get('nonced_keys', {})):
                nk.pop(name, None)
                k['nonced_keys'] = nk
                attrs.keys = k
                self._save_attributes(fields=['keys'])

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

    key = ''.join(secrets.choice(string.ascii_letters) for i in range(50))
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
