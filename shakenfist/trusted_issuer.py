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
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.trusted_issuer_attributes import (
    TrustedIssuerAttributesData)
from shakenfist.schema.trusted_issuer_data import TrustedIssuerData


LOG, _ = logs.setup(__name__)


# The attribute columns a successful discovery run owns.
#
# They are listed once, here, because they are always written as a set:
# a document which no longer names an endpoint is saying the provider
# has withdrawn that flow, so writing the three endpoints individually
# would leave a withdrawn one advertised forever. discovered_at travels
# with them so that "when was this last asked" can never disagree with
# what was found.
DISCOVERED_FIELDS = [
    'authorization_endpoint',
    'token_endpoint',
    'device_authorization_endpoint',
    'discovered_at',
]


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
                audience=metadata['audience'],
                authorization_endpoint=metadata.get('authorization_endpoint'),
                token_endpoint=metadata.get('token_endpoint'),
                device_authorization_endpoint=metadata.get(
                    'device_authorization_endpoint'),
                client_id=metadata.get('client_id'),
                oidc_scopes=metadata.get('oidc_scopes'),
                discovered_at=metadata.get('discovered_at')
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
    def zombie_repair_state(cls, object_uuid: str) -> str:
        """Repair a stateless issuer forward if the exchange would trust it.

        from_db_by_name() and the federated exchange's issuer listing
        (federation.issuer_claiming_url()) reject only an issuer whose
        state is deleted, so one with no state row is still trusted.
        Marking it deleted would silently revoke trust an operator
        configured (issue 3836), so an issuer with its attributes row is
        repaired to created instead.

        An issuer with no attributes row has no issuer URL or JWKS URI,
        so the exchange can never match a token to it and it is garbage.

        A failed attributes read raises DatabaseUnavailable rather than
        reading as "absent", so the reconciler leaves the issuer alone.
        """
        if mariadb.get_trusted_issuer_attributes(_as_uuid(object_uuid)):
            return cls.STATE_CREATED
        return cls.STATE_DELETED

    @classmethod
    def new(cls, name: str, issuer_url: str, jwks_uri: Optional[str],
            audience: str, authorization_endpoint: Optional[str] = None,
            token_endpoint: Optional[str] = None,
            device_authorization_endpoint: Optional[str] = None,
            client_id: Optional[str] = None,
            oidc_scopes: Optional[str] = None,
            discovered_at: Optional[float] = None
            ) -> Optional['TrustedIssuer']:
        """Configure a new trusted issuer.

        jwks_uri may be None, which records an issuer whose signing key
        location is not known yet. Such an issuer cannot verify a token
        at all, so a caller passes None only when it has a plan for
        filling the value in -- a discovery run, or an operator
        updating the issuer. The API endpoints never make one: a create
        which omits jwks_uri requires the discovery fetch to succeed,
        and a document which succeeds supplies the value.

        The three endpoints and discovered_at come from a discovery
        run; client_id and oidc_scopes come from the operator, because
        a client registration at the provider is not something a
        document can tell us.

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
        superseded_uuid = str(superseded.uuid) if superseded else None
        if superseded:
            superseded.hard_delete()

        issuer_uuid = str(uuid4())
        cls._db_create(issuer_uuid, {
            'uuid': issuer_uuid,
            'name': name,
            'version': cls.current_version,
            'issuer_url': issuer_url,
            'jwks_uri': jwks_uri,
            'audience': audience,
            'authorization_endpoint': authorization_endpoint,
            'token_endpoint': token_endpoint,
            'device_authorization_endpoint': device_authorization_endpoint,
            'client_id': client_id,
            'oidc_scopes': oidc_scopes,
            'discovered_at': discovered_at
        })

        i = cls.from_db(issuer_uuid, suppress_failure_audit=True)
        if not i:
            # Lost a create race: the unique index rejected our static
            # row, so clean up the orphaned attributes row.
            mariadb.delete_trusted_issuer_attributes(_as_uuid(issuer_uuid))
            return None

        i.state = cls.STATE_INITIAL
        i.state = cls.STATE_CREATED

        if superseded_uuid:
            # Same reasoning as MappingRule.new(): reclaiming the name
            # hard deletes the old row, and hard_delete() takes its
            # events with it. An issuer's trail is thinner than a rule's,
            # but "who repointed this issuer, and when" is exactly the
            # question asked after a federation starts trusting the wrong
            # provider.
            i.add_event(
                EVENT_TYPE_AUDIT, 'replaced a deleted issuer of the same name',
                extra={'superseded_issuer': superseded_uuid})

        return i

    # Static values
    @property
    def name(self) -> str:
        return self.__name

    # Mutable attributes
    def _attributes(self) -> Optional[TrustedIssuerAttributesData]:
        return mariadb.get_trusted_issuer_attributes(_as_uuid(self.uuid))

    def configuration(self) -> Optional[TrustedIssuerAttributesData]:
        """The whole of the issuer's configuration in a single read.

        The counterpart to MappingRule.policy(), and for the same
        reason: each property below issues its own database read, which
        is fine for a one-off and wasteful for a caller that wants more
        than one of them.

        The federated exchange is that caller. Resolving an issuer
        already scans every configured issuer, and validate_token then
        went back to the same row three more times -- jwks_uri to find
        the signing key, then audience and issuer_url to verify the
        claims. Reading once turns those three into one.

        Named configuration rather than policy because that is what it
        is: where a rule's policy says who may act as a namespace, this
        says only where an issuer's keys live and what it must say.
        """
        return self._attributes()

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

    @property
    def authorization_endpoint(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.authorization_endpoint if attrs else None

    @property
    def token_endpoint(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.token_endpoint if attrs else None

    @property
    def device_authorization_endpoint(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.device_authorization_endpoint if attrs else None

    @property
    def client_id(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.client_id if attrs else None

    @property
    def oidc_scopes(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.oidc_scopes if attrs else None

    @property
    def discovered_at(self) -> Optional[float]:
        attrs = self._attributes()
        return attrs.discovered_at if attrs else None

    def update(self, issuer_url: str, jwks_uri: Optional[str], audience: str,
               client_id: Optional[str] = None,
               oidc_scopes: Optional[str] = None,
               discovered: Optional[dict[str, Any]] = None) -> None:
        """Replace the issuer's configuration.

        issuer_url, jwks_uri and audience move together because they
        are one coherent configuration: a new issuer_url with a stale
        jwks_uri is a broken issuer rather than a partially updated
        one. That is why the field mask names them rather than passing
        None: the caller really is writing those columns, and saying so
        keeps the "None is for creation and upgrades only" rule intact.

        client_id and oidc_scopes do *not* move with them, and are not
        replaced when they are absent. They are a client registration
        at the provider rather than part of the triple above, and the
        documented way to repoint an issuer is to send the three
        identity fields -- so if an omission cleared them, an operator
        correcting an audience would silently turn off human login as a
        side effect, with nothing to say so and a guessed client
        identifier as the obvious next step. Every client and script
        written before these fields existed sends exactly that body.

        So None -- which is what both an absent key and an explicit
        null arrive as -- means "leave this alone", and the field is
        left out of the mask. An empty string means "clear this", and
        is stored as NULL so a cleared column has one spelling rather
        than two. Empty-string-means-clear rather than
        null-means-clear because an absent key and an explicit null are
        indistinguishable by the time they reach here, while an empty
        string is not a valid client identifier or scope list and so
        can only have been meant as an instruction.

        They are also deliberately not tied to a discovery run: a
        client registration changes at the provider's console without
        anything about the provider's endpoints moving, so an operator
        must be able to set one without a document being reachable.

        The discovered endpoints are the fields which do not move with
        the rest, which is what `discovered` is for. None means no
        discovery ran -- the operator asked for none, or the fetch
        failed and the caller chose to continue -- and leaves those
        columns exactly as they were, because the alternative is that
        a configuration edit silently forgets where the provider's
        login endpoints are. A dict means a run succeeded, and then all
        four are written from it including the endpoints it did *not*
        contain: a successful document is authoritative about what the
        provider publishes, so a provider which has stopped offering
        the device code grant must stop advertising it here too.
        """
        fields = ['issuer_url', 'jwks_uri', 'audience']
        attrs = TrustedIssuerAttributesData(
            uuid=_as_uuid(self.uuid),
            issuer_url=issuer_url,
            jwks_uri=jwks_uri,
            audience=audience)

        for name, value in (('client_id', client_id),
                            ('oidc_scopes', oidc_scopes)):
            if value is None:
                continue
            # An empty string is the instruction to clear, and lands in
            # the column as NULL rather than as an empty string: the
            # readers of these fields test them for presence, and two
            # spellings of "not set" is two chances to test for the
            # wrong one.
            fields.append(name)
            setattr(attrs, name, value or None)

        if discovered is not None:
            fields += DISCOVERED_FIELDS
            for field in DISCOVERED_FIELDS:
                setattr(attrs, field, discovered.get(field))

        mariadb.update_trusted_issuer_attributes(attrs, fields=fields)
        extra = {field: getattr(attrs, field) for field in fields}
        self.add_event(
            EVENT_TYPE_MUTATE, 'updated trusted issuer configuration',
            extra=extra)

    def record_discovery(self, current: TrustedIssuerAttributesData,
                         jwks_uri: Optional[str],
                         discovered: dict[str, Any]) -> None:
        """Write what a discovery run found, and nothing else.

        The counterpart to update() for a caller which re-ran discovery
        on an issuer whose configuration it is not otherwise editing.
        The mask is why this is a method of its own rather than an
        update() with most of its arguments read back first: writing
        only these columns means an administrator refreshing one
        issuer's endpoints cannot revert an audience somebody changed
        between this call's read and its write.

        jwks_uri is passed in rather than taken from the document
        because the decision about whose jwks_uri wins is the caller's
        -- an operator-supplied value is never overwritten by
        discovery, and only the caller knows what the operator
        supplied.

        `current` is the configuration the caller already read, under
        the lock it is writing beneath. It is here so that the two
        fields this method must construct but must not write carry the
        issuer's real values rather than blanks. They were blanks
        first, which made the field mask load bearing in a way nothing
        would have caught: widen it by one name and every refresh would
        have written an empty issuer_url -- the single value compared
        byte for byte against a token's iss claim -- and the issuer
        would have stopped validating anything, silently. The mask is
        still the thing that decides what is written; it is simply no
        longer the only thing standing between a refresh and a wiped
        row.
        """
        attrs = TrustedIssuerAttributesData(
            uuid=_as_uuid(self.uuid),
            issuer_url=current.issuer_url,
            audience=current.audience,
            jwks_uri=jwks_uri)
        for field in DISCOVERED_FIELDS:
            setattr(attrs, field, discovered.get(field))

        mariadb.update_trusted_issuer_attributes(
            attrs, fields=['jwks_uri'] + DISCOVERED_FIELDS)
        self.add_event(
            EVENT_TYPE_MUTATE, 'recorded an OIDC discovery run',
            extra={field: getattr(attrs, field)
                   for field in ['jwks_uri'] + DISCOVERED_FIELDS})

    def external_view(self) -> dict[str, Any]:
        retval = self._external_view()

        # One read rather than three, see configuration().
        attrs = self.configuration()
        retval.update({
            'name': self.name,
            'issuer_url': attrs.issuer_url if attrs else None,
            'jwks_uri': attrs.jwks_uri if attrs else None,
            'audience': attrs.audience if attrs else None,
            'authorization_endpoint': (
                attrs.authorization_endpoint if attrs else None),
            'token_endpoint': attrs.token_endpoint if attrs else None,
            'device_authorization_endpoint': (
                attrs.device_authorization_endpoint if attrs else None),
            'client_id': attrs.client_id if attrs else None,
            'oidc_scopes': attrs.oidc_scopes if attrs else None,
            'discovered_at': attrs.discovered_at if attrs else None
        })
        return retval

    def delete(self) -> None:
        """Soft delete. The standard reaper hard deletes later."""
        self.state = self.STATE_DELETED


class TrustedIssuers(dbo_iter):
    base_object = TrustedIssuer

    def get_iterator(self):
        # Issuers are cluster level rather than namespaced, and there
        # is one per identity provider, so a full listing is both
        # correct and cheap.
        for data in mariadb.get_all_trusted_issuers():  # nopushdown: as above
            yield str(data.uuid), TrustedIssuer._static_values_to_dict(data)

    def __iter__(self):
        for _, static_values in self.get_iterator():
            i = TrustedIssuer(static_values)
            if not i:
                continue
            out = self.apply_filters(i)
            if out:
                yield out
