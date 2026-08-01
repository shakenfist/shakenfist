# Copyright 2019 Michael Still and contributors
#
# A TrustedIssuer is an external identity provider this cluster is
# willing to believe: GitHub Actions, an Authentik realm, a Keycloak
# instance. Deciding who may vouch for identities on a cluster is an
# administrative decision, so issuers live in the system namespace and
# only administrators may manage them.
#
# Issuers are referenced by name from mapping rules, and a key minted
# through a rule records that rule in its provenance, so the whole
# chain issuer <- rule <- key <- token is object-modelled. See
# docs/plans/PLAN-auth-federation-phase-03-exchange.md.

from typing import Any
from typing import Optional
from uuid import UUID
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.trusted_issuer_attributes import (
    TrustedIssuerAttributesData)
from shakenfist.schema.trusted_issuer_data import TrustedIssuerData


LOG, _ = logs.setup(__name__)


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


class TrustedIssuer(dbo):
    object_type = ObjectType.TRUSTED_ISSUER
    initial_version = 1
    current_version = 1

    ACTIVE_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED}
    HEALTHY_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED}

    # docs/developer_guide/state_machine.md describes these states.
    # Configuring an issuer is atomic, so there is no error state.
    state_targets = {
        None: (dbo.STATE_INITIAL,),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED,),
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values: dict[str, Any]) -> None:
        self.upgrade(static_values)

        super().__init__(static_values['uuid'], static_values.get('version'))

        self.__name = static_values['name']

    @classmethod
    def _static_values_to_dict(
            cls, data: TrustedIssuerData) -> dict[str, Any]:
        return {
            'uuid': str(data.uuid),
            'name': data.name,
            'version': data.version
        }

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        _uuid = _as_uuid(object_uuid)
        super()._db_create(str(_uuid), metadata)

        mariadb.create_trusted_issuer(TrustedIssuerData(
            uuid=_uuid,
            name=metadata['name'],
            version=metadata['version']
        ))

        mariadb.create_trusted_issuer_attributes(
            TrustedIssuerAttributesData(
                uuid=_uuid,
                issuer_url=metadata['issuer_url'],
                jwks_uri=metadata['jwks_uri'],
                audience=metadata['audience']
            ))

    @classmethod
    def _db_get(cls, object_uuid) -> Optional[dict[str, Any]]:
        data = mariadb.get_trusted_issuer(_as_uuid(object_uuid))
        if not data:
            return None
        return cls._static_values_to_dict(data)

    @classmethod
    def from_static_data(cls, data: TrustedIssuerData) -> 'TrustedIssuer':
        return cls(cls._static_values_to_dict(data))

    @classmethod
    def from_db_by_name(cls, name: str,
                        include_deleted: bool = False
                        ) -> Optional['TrustedIssuer']:
        """Look up an active issuer by its unique name.

        A miss is an ordinary outcome when an operator names an issuer
        that does not exist, so unlike from_db() this does not write a
        "non-existent object" audit event.

        Soft-deleted issuers are invisible by default. Deleting an
        issuer must revoke trust in it immediately rather than when
        the reaper eventually collects the row, because the exchange
        resolves issuers by name and would otherwise keep believing
        one an operator had explicitly stopped believing. The same
        filter is what lets the name be reused straight away.
        """
        data = mariadb.get_trusted_issuer_by_name(name)
        if not data:
            return None

        issuer = cls.from_static_data(data)
        if not include_deleted and issuer.state.value == cls.STATE_DELETED:
            return None
        return issuer

    @classmethod
    def new(cls, name: str, issuer_url: str, jwks_uri: str,
            audience: str) -> Optional['TrustedIssuer']:
        """Configure a new trusted issuer.

        Returns None when the name is already taken -- the unique index
        is the arbiter, so two administrators racing to create the same
        issuer name produce one issuer and one clean failure rather
        than a duplicate.

        A name held only by a soft-deleted issuer is reclaimed. The
        operator has already said they no longer trust that issuer, and
        making them wait for the reaper before they can reuse the name
        would be a surprising thing for a delete to do. The unique
        index means the old row has to actually go, so this is a hard
        delete rather than a second soft one.
        """
        if cls.from_db_by_name(name):
            return None

        superseded = cls.from_db_by_name(name, include_deleted=True)
        if superseded:
            superseded.hard_delete()

        issuer_uuid = str(uuid4())
        cls._db_create(issuer_uuid, {
            'uuid': issuer_uuid,
            'name': name,
            'version': cls.current_version,
            'issuer_url': issuer_url,
            'jwks_uri': jwks_uri,
            'audience': audience
        })

        i = cls.from_db(issuer_uuid, suppress_failure_audit=True)
        if not i:
            # Lost a create race: the unique index rejected our static
            # row, so clean up the orphaned attributes row.
            mariadb.delete_trusted_issuer_attributes(_as_uuid(issuer_uuid))
            return None

        i.state = cls.STATE_INITIAL
        i.state = cls.STATE_CREATED
        return i

    # Static values
    @property
    def name(self) -> str:
        return self.__name

    # Mutable attributes
    def _attributes(self) -> Optional[TrustedIssuerAttributesData]:
        return mariadb.get_trusted_issuer_attributes(_as_uuid(self.uuid))

    @property
    def issuer_url(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.issuer_url if attrs else None

    @property
    def jwks_uri(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.jwks_uri if attrs else None

    @property
    def audience(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.audience if attrs else None

    def update(self, issuer_url: str, jwks_uri: str, audience: str) -> None:
        """Replace the issuer's configuration.

        All three move together because they are one coherent
        configuration: a new issuer_url with a stale jwks_uri is a
        broken issuer rather than a partially updated one.
        """
        mariadb.update_trusted_issuer_attributes(
            TrustedIssuerAttributesData(
                uuid=_as_uuid(self.uuid),
                issuer_url=issuer_url,
                jwks_uri=jwks_uri,
                audience=audience))
        self.add_event(
            EVENT_TYPE_MUTATE, 'updated trusted issuer configuration',
            extra={'issuer_url': issuer_url, 'jwks_uri': jwks_uri,
                   'audience': audience})

    def external_view(self) -> dict[str, Any]:
        retval = self._external_view()
        retval.update({
            'name': self.name,
            'issuer_url': self.issuer_url,
            'jwks_uri': self.jwks_uri,
            'audience': self.audience
        })
        return retval

    def delete(self) -> None:
        """Soft delete. The standard reaper hard deletes later."""
        self.state = self.STATE_DELETED

    def hard_delete(self) -> None:
        _uuid = _as_uuid(self.uuid)
        mariadb.delete_trusted_issuer_attributes(_uuid)
        mariadb.delete_trusted_issuer(_uuid)
        super().hard_delete()


class TrustedIssuers(dbo_iter):
    base_object = TrustedIssuer

    def get_iterator(self):
        # Issuers are cluster level rather than namespaced, and there
        # is one per identity provider, so a full listing is both
        # correct and cheap.
        for data in mariadb.get_all_trusted_issuers():
            yield str(data.uuid), TrustedIssuer._static_values_to_dict(data)

    def __iter__(self):
        for _, static_values in self.get_iterator():
            i = TrustedIssuer(static_values)
            if not i:
                continue
            out = self.apply_filters(i)
            if out:
                yield out
