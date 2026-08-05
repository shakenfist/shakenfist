# Copyright 2019 Michael Still and contributors
#
# A MappingRule says which external identities a namespace is willing to
# mint keys for, and what those keys may do. It is owned by the
# namespace it targets, because deciding who may act as your namespace
# is the namespace owner's business -- unlike the TrustedIssuer it
# references, which is a cluster-level administrative decision about who
# may vouch for identities at all.
#
# The chain issuer <- rule <- key <- token is object-modelled end to
# end. See docs/plans/PLAN-auth-federation-phase-03-exchange.md.

from typing import Any
from typing import Optional
from uuid import UUID
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.schema.mapping_rule_attributes import MappingRuleAttributesData
from shakenfist.schema.mapping_rule_data import MappingRuleData
from shakenfist.schema.object_types import ObjectType
from shakenfist.trusted_issuer import TrustedIssuer
from shakenfist.util import credentials


LOG, _ = logs.setup(__name__)


# Upper bounds on what a rule may say. Every one of these exists
# because the field is stored, and a field with no bound is a way for
# an operator to turn a 400 into a database error and a 500. The
# numbers are chosen to be far above any real rule: a GitHub Actions
# rule binds a handful of claims whose values are repository paths and
# branch refs.
MAX_KEY_NAME_PREFIX_LENGTH = 64
MAX_CLAIM_NAME_LENGTH = 128
MAX_CLAIM_VALUE_LENGTH = 512
MAX_BOUND_CLAIMS = 32
MAX_CLAIM_ALTERNATIVES = 64
MAX_SCOPES = 64
MAX_SCOPE_LENGTH = 128

# One day. A federated key stands in for an identity token that is
# typically valid for minutes, so a key outliving its own justification
# by more than a working day is not a policy anyone chose on purpose.
MAX_KEY_TTL_SECONDS = 86400


class RuleValidationError(Exception):
    """A rule as described would not be a safe rule.

    Carries a message intended to reach the operator, because every one
    of these is something they can fix in the request they just made.
    """


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _validate_claim_value(claim: str, value: str) -> None:
    if len(value) > MAX_CLAIM_VALUE_LENGTH:
        raise RuleValidationError(
            f'the matcher for claim "{claim}" may not be longer than '
            f'{MAX_CLAIM_VALUE_LENGTH} characters')


def validate_bound_claims(bound_claims: Any) -> dict[str, Any]:
    """Check a claim matcher set, or raise RuleValidationError.

    The matching semantics are deliberately narrow, because this is
    where OIDC federations get compromised. A matcher is an exact
    string, or a list of acceptable strings; comparison is exact, with
    no globbing, no regular expressions and no prefix matching.
    `repository: shakenfist/*` looks reasonable right up until somebody
    registers `shakenfist-evil`.

    A rule with no bound claims is refused outright: it would accept
    any token the issuer ever signed, for anybody, which is not a
    federation but a public key to the namespace.
    """
    if not isinstance(bound_claims, dict):
        raise RuleValidationError('bound_claims must be an object')

    if not bound_claims:
        raise RuleValidationError(
            'a rule must bind at least one claim, otherwise it accepts '
            'every identity the issuer will ever vouch for')

    if len(bound_claims) > MAX_BOUND_CLAIMS:
        raise RuleValidationError(
            f'a rule may bind at most {MAX_BOUND_CLAIMS} claims')

    validated: dict[str, Any] = {}
    for claim, matcher in bound_claims.items():
        if not isinstance(claim, str) or not claim:
            raise RuleValidationError('claim names must be non-empty strings')
        if len(claim) > MAX_CLAIM_NAME_LENGTH:
            raise RuleValidationError(
                f'claim names may not be longer than '
                f'{MAX_CLAIM_NAME_LENGTH} characters')

        if isinstance(matcher, str):
            if not matcher:
                raise RuleValidationError(
                    f'the matcher for claim "{claim}" is an empty string, '
                    'which no claim value can equal')
            _validate_claim_value(claim, matcher)
            validated[claim] = matcher
            continue

        if isinstance(matcher, list):
            if not matcher:
                raise RuleValidationError(
                    f'the matcher for claim "{claim}" is an empty list, '
                    'which no claim value can match')
            if len(matcher) > MAX_CLAIM_ALTERNATIVES:
                raise RuleValidationError(
                    f'the matcher for claim "{claim}" may offer at most '
                    f'{MAX_CLAIM_ALTERNATIVES} alternatives')
            for alternative in matcher:
                if not isinstance(alternative, str) or not alternative:
                    raise RuleValidationError(
                        f'the matcher for claim "{claim}" must contain only '
                        'non-empty strings')
                _validate_claim_value(claim, alternative)
            validated[claim] = list(matcher)
            continue

        # Explicitly including bool, int and None. A YAML-ish client
        # sending `true` rather than `"true"` would otherwise store a
        # matcher that never matches a JSON string claim, and the rule
        # would silently never fire.
        raise RuleValidationError(
            f'the matcher for claim "{claim}" must be a string or a list '
            'of strings, not a ' + type(matcher).__name__)

    return validated


def validate_scopes(scopes: Any) -> list[str]:
    """Check the scopes a rule grants, or raise RuleValidationError.

    Unlike a NamespaceKey, where a missing scope list means "unscoped"
    and therefore wildcard for upgrade compatibility, a rule must say
    what it grants. An omitted or empty list here would otherwise be
    the loosest possible grant wearing the appearance of the tightest.
    """
    if not isinstance(scopes, list):
        raise RuleValidationError('scopes must be a list of strings')

    if not scopes:
        raise RuleValidationError(
            'a rule must grant at least one scope; a rule granting nothing '
            'can only mint keys that can do nothing')

    if len(scopes) > MAX_SCOPES:
        raise RuleValidationError(
            f'a rule may grant at most {MAX_SCOPES} scopes')

    for scope in scopes:
        if not isinstance(scope, str) or not scope:
            raise RuleValidationError(
                'scopes must be non-empty strings')
        if len(scope) > MAX_SCOPE_LENGTH:
            raise RuleValidationError(
                f'scopes may not be longer than {MAX_SCOPE_LENGTH} '
                'characters')

    return list(scopes)


def validate_key_ttl(key_ttl: Any) -> int:
    """Check the lifetime of keys this rule mints."""
    if isinstance(key_ttl, bool) or not isinstance(key_ttl, int):
        raise RuleValidationError('key_ttl must be an integer number '
                                  'of seconds')
    if key_ttl <= 0:
        raise RuleValidationError(
            'key_ttl must be positive; a rule minting already-expired keys '
            'is a configuration error rather than a policy')
    if key_ttl > MAX_KEY_TTL_SECONDS:
        raise RuleValidationError(
            f'key_ttl may not exceed {MAX_KEY_TTL_SECONDS} seconds; a '
            'federated key stands in for an identity token that has '
            'already expired, so a long lived one outlives the thing '
            'that justified it. Create a namespace key directly if a '
            'long lived credential is what you want')
    return key_ttl


def validate_key_name_prefix(key_name_prefix: Any) -> str:
    """Check the front of every key name this rule will ever mint.

    Held to the same reserved-name standard as a directly created key.
    Without this a rule is a way around the check the key endpoints
    perform: a prefix of `_service_key` mints keys that collide with
    the cluster's own service credentials.
    """
    if not isinstance(key_name_prefix, str) or not key_name_prefix:
        raise RuleValidationError(
            'key_name_prefix must be a non-empty string')

    if len(key_name_prefix) > MAX_KEY_NAME_PREFIX_LENGTH:
        raise RuleValidationError(
            f'key_name_prefix may not be longer than '
            f'{MAX_KEY_NAME_PREFIX_LENGTH} characters')

    if credentials.is_reserved_key_name(key_name_prefix):
        raise RuleValidationError(
            f'"{key_name_prefix}" is reserved for keys the cluster mints '
            'for itself')

    return key_name_prefix


def validate_issuer(issuer: Any) -> str:
    """Check the named issuer exists, or raise RuleValidationError.

    Checked at creation so an operator finds out immediately rather
    than at the first exchange. The issuer can still be deleted later,
    in which case the rule resolves to nothing and the exchange
    refuses -- which is the safe direction, and why the reference is
    not enforced by a foreign key.
    """
    if not isinstance(issuer, str) or not issuer:
        raise RuleValidationError('issuer must be a non-empty string')

    if not TrustedIssuer.from_db_by_name(issuer):
        raise RuleValidationError(f'no trusted issuer named "{issuer}"')

    return issuer


class MappingRule(dbo):
    object_type = ObjectType.MAPPING_RULE
    initial_version = 1
    current_version = 1

    ACTIVE_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED}
    HEALTHY_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED}

    # docs/developer_guide/state_machine.md describes these states.
    # Writing a rule is atomic, so there is no error state.
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

    @classmethod
    def _static_values_to_dict(cls, data: MappingRuleData) -> dict[str, Any]:
        return {
            'uuid': str(data.uuid),
            'namespace': data.namespace,
            'name': data.name,
            'version': data.version
        }

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        _uuid = _as_uuid(object_uuid)
        super()._db_create(str(_uuid), metadata)

        mariadb.create_mapping_rule(MappingRuleData(
            uuid=_uuid,
            namespace=metadata['namespace'],
            name=metadata['name'],
            version=metadata['version']
        ))

        mariadb.create_mapping_rule_attributes(
            MappingRuleAttributesData(
                uuid=_uuid,
                issuer=metadata['issuer'],
                bound_claims=metadata['bound_claims'],
                scopes=metadata['scopes'],
                key_ttl=metadata['key_ttl'],
                key_name_prefix=metadata['key_name_prefix']
            ))

    @classmethod
    def _db_get(cls, object_uuid) -> Optional[dict[str, Any]]:
        data = mariadb.get_mapping_rule(_as_uuid(object_uuid))
        if not data:
            return None
        return cls._static_values_to_dict(data)

    @classmethod
    def from_static_data(cls, data: MappingRuleData) -> 'MappingRule':
        return cls(cls._static_values_to_dict(data))

    @classmethod
    def from_db_by_name(cls, namespace: str, name: str,
                        include_deleted: bool = False
                        ) -> Optional['MappingRule']:
        """Look up an active rule by its (namespace, name) pair.

        A miss is an ordinary outcome when an operator names a rule
        that does not exist, so unlike from_db() this does not write a
        "non-existent object" audit event.

        Soft-deleted rules are invisible by default. Deleting a rule
        must stop it minting keys immediately rather than when the
        reaper eventually collects the row, and the same filter is what
        lets the name be reused straight away.
        """
        data = mariadb.get_mapping_rule_by_name(namespace, name)
        if not data:
            return None

        rule = cls.from_static_data(data)
        if not include_deleted and rule.state.value == cls.STATE_DELETED:
            return None
        return rule

    @classmethod
    def new(cls, namespace: str, name: str, issuer: str,
            bound_claims: dict[str, Any], scopes: list[str],
            key_ttl: int, key_name_prefix: str) -> Optional['MappingRule']:
        """Create a new mapping rule.

        Every argument that describes policy is validated first, so a
        rule that exists is a rule that was safe to write. Raises
        RuleValidationError for anything an operator can fix.

        Returns None when the name is already taken in this namespace --
        the unique index is the arbiter, so two callers racing produce
        one rule and one clean failure rather than a duplicate.

        A name held only by a soft-deleted rule is reclaimed, for the
        same reason TrustedIssuer.new() does it: the operator has
        already said what they think of the old rule, and the unique
        index means the old row has to actually go.
        """
        issuer = validate_issuer(issuer)
        bound_claims = validate_bound_claims(bound_claims)
        scopes = validate_scopes(scopes)
        key_ttl = validate_key_ttl(key_ttl)
        key_name_prefix = validate_key_name_prefix(key_name_prefix)

        if cls.from_db_by_name(namespace, name):
            return None

        superseded = cls.from_db_by_name(namespace, name, include_deleted=True)
        superseded_uuid = str(superseded.uuid) if superseded else None
        if superseded:
            superseded.hard_delete()

        rule_uuid = str(uuid4())
        cls._db_create(rule_uuid, {
            'uuid': rule_uuid,
            'namespace': namespace,
            'name': name,
            'version': cls.current_version,
            'issuer': issuer,
            'bound_claims': bound_claims,
            'scopes': scopes,
            'key_ttl': key_ttl,
            'key_name_prefix': key_name_prefix
        })

        r = cls.from_db(rule_uuid, suppress_failure_audit=True)
        if not r:
            # Lost a create race: the unique index rejected our static
            # row, so clean up the orphaned attributes row.
            mariadb.delete_mapping_rule_attributes(_as_uuid(rule_uuid))
            return None

        r.state = cls.STATE_INITIAL
        r.state = cls.STATE_CREATED

        if superseded_uuid:
            # hard_delete() takes the old rule's events with it, and on a
            # rule those events are the refusal trail -- a stream of
            # near-miss claim failures is what probing looks like. The
            # natural response to spotting it is to delete the rule and
            # write a tighter one under the same name, which is exactly
            # this path, so the evidence would be erased by acting on it.
            #
            # Recorded on the replacement rather than on the namespace
            # because namespace.py imports this module, and because this
            # is where somebody asking "what happened to rule ryll" will
            # look. The detail is still gone; what survives is that there
            # was a previous rule of this name and its identifier, which
            # is enough to find it in a log aggregator that has the
            # events this database no longer does.
            r.add_event(
                EVENT_TYPE_AUDIT, 'replaced a deleted rule of the same name',
                extra={'superseded_rule': superseded_uuid})

        if namespace == 'system':
            # Not refused: a cluster's own automation legitimately
            # federates into system, and forbidding it would push
            # operators towards a long-lived static key instead, which
            # is worse. But this is the one namespace where a minted
            # key is next to a cluster-admin grant, so it is recorded
            # loudly rather than looking like any other rule.
            LOG.with_fields({
                'rule': r.uuid, 'name': name, 'issuer': issuer,
                'scopes': scopes
            }).warning('Mapping rule created targeting the system namespace')
            r.add_event(
                EVENT_TYPE_AUDIT,
                'mapping rule targets the system namespace',
                extra={'issuer': issuer, 'bound_claims': bound_claims,
                       'scopes': scopes})

        return r

    # Static values
    @property
    def namespace(self) -> str:
        return self.__namespace

    @property
    def name(self) -> str:
        return self.__name

    # Mutable attributes
    def _attributes(self) -> Optional[MappingRuleAttributesData]:
        return mariadb.get_mapping_rule_attributes(_as_uuid(self.uuid))

    def policy(self) -> Optional[MappingRuleAttributesData]:
        """The whole of the rule's policy in a single read.

        Each property below issues its own database read, which is
        fine for a one-off and wasteful for a caller that wants more
        than one of them: the federated exchange reads five, so it
        made five round trips for one row.

        Reading once also gives CorruptMappingRule a single place to
        be caught. The exception comes from decoding bound_claims or
        scopes, so it is raised here and by the properties, never by
        from_db_by_name -- that reads the static row only. A caller
        which wants to handle a damaged rule rather than let it become
        a 500 has to wrap this call, not the lookup.
        """
        return self._attributes()

    @property
    def issuer(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.issuer if attrs else None

    @property
    def bound_claims(self) -> Optional[dict[str, Any]]:
        attrs = self._attributes()
        return attrs.bound_claims if attrs else None

    @property
    def scopes(self) -> Optional[list[str]]:
        attrs = self._attributes()
        return attrs.scopes if attrs else None

    @property
    def key_ttl(self) -> Optional[int]:
        attrs = self._attributes()
        return attrs.key_ttl if attrs else None

    @property
    def key_name_prefix(self) -> Optional[str]:
        attrs = self._attributes()
        return attrs.key_name_prefix if attrs else None

    def update(self, issuer: str, bound_claims: dict[str, Any],
               scopes: list[str], key_ttl: int,
               key_name_prefix: str) -> None:
        """Replace the rule's policy.

        Validated exactly as creation is: an edit must not be able to
        reach a state a create would have refused.

        This deliberately does not touch keys already minted from the
        rule. A minted key stands alone, and its provenance records the
        claims that were actually satisfied, so the audit trail
        describes the grant as it was rather than as the rule reads
        today. Narrowing a rule's scopes does not retroactively narrow
        a live key -- delete the key if that is what you mean.
        """
        issuer = validate_issuer(issuer)
        bound_claims = validate_bound_claims(bound_claims)
        scopes = validate_scopes(scopes)
        key_ttl = validate_key_ttl(key_ttl)
        key_name_prefix = validate_key_name_prefix(key_name_prefix)

        # The mask names every field rather than passing None. A rule's
        # policy is edited as one unit -- a PUT carries the whole of it
        # -- so this genuinely does write every column, and naming them
        # keeps None reserved for creation and upgrade persistence.
        mariadb.update_mapping_rule_attributes(
            MappingRuleAttributesData(
                uuid=_as_uuid(self.uuid),
                issuer=issuer,
                bound_claims=bound_claims,
                scopes=scopes,
                key_ttl=key_ttl,
                key_name_prefix=key_name_prefix),
            fields=['issuer', 'bound_claims', 'scopes', 'key_ttl',
                    'key_name_prefix'])
        self.add_event(
            EVENT_TYPE_MUTATE, 'updated mapping rule',
            extra={'issuer': issuer, 'bound_claims': bound_claims,
                   'scopes': scopes, 'key_ttl': key_ttl,
                   'key_name_prefix': key_name_prefix})

    def external_view(self) -> dict[str, Any]:
        retval = self._external_view()

        # One read rather than five, see policy().
        #
        # A damaged rule is described rather than raised here, unlike on
        # the exchange path, because the two callers want opposite
        # things. The exchange must refuse: a rule it cannot read is a
        # rule whose bound claims it cannot check, and minting against
        # that would be authorising on a guess.
        #
        # These are the CRUD routes, where raising takes the listing
        # down with it -- one undecodable column would turn GET
        # .../rules into a 500 and hide every healthy rule in the
        # namespace -- and where delete() builds its response *after*
        # doing the work, so a damaged rule would be deleted and still
        # report failure, on the one call that would have cleaned it up.
        # The owner needs to be told which rule is broken, which means
        # answering.
        try:
            attrs = self.policy()
            unusable = False
        except exceptions.CorruptMappingRule as e:
            self.log.with_fields({'error': str(e)}).error(
                'Mapping rule attributes could not be decoded')
            attrs = None
            unusable = True

        retval.update({
            'namespace': self.namespace,
            'name': self.name,
            'issuer': attrs.issuer if attrs else None,
            'bound_claims': attrs.bound_claims if attrs else None,
            'scopes': attrs.scopes if attrs else None,
            'key_ttl': attrs.key_ttl if attrs else None,
            'key_name_prefix': attrs.key_name_prefix if attrs else None,
            # Only ever True. A missing attributes row also yields nulls
            # above, and that is a different fault with a different fix,
            # so the two are not collapsed into one flag.
            'unusable': unusable
        })
        return retval

    def delete(self) -> None:
        """Soft delete. The standard reaper hard deletes later."""
        self.state = self.STATE_DELETED

    def hard_delete(self) -> None:
        _uuid = _as_uuid(self.uuid)
        mariadb.delete_mapping_rule_attributes(_uuid)
        mariadb.delete_mapping_rule(_uuid)
        super().hard_delete()


class MappingRules(dbo_iter):
    base_object = MappingRule

    def get_iterator(self):
        # The base iterator already carries a namespace; honouring it
        # here pushes the restriction into SQL rather than listing the
        # cluster and discarding most of it. The common caller is a
        # namespace owner listing their own rules.
        # With no namespace there is nothing to push down, and the
        # unfiltered listing is the cluster admin view and the reaper's
        # sweep, both of which do want every row.
        if self.namespace is None:
            rules = mariadb.get_all_mapping_rules()  # nopushdown: no namespace to filter on
        else:
            rules = mariadb.get_mapping_rules_in_namespace(self.namespace)

        for data in rules:
            yield str(data.uuid), MappingRule._static_values_to_dict(data)

    def __iter__(self):
        for _, static_values in self.get_iterator():
            r = MappingRule(static_values)
            if not r:
                continue
            out = self.apply_filters(r)
            if out:
                yield out


def rules_in_namespace(namespace: str) -> list[MappingRule]:
    """Every rule owned by a namespace, whatever its state.

    Used by Namespace.hard_delete(), which is the last chance to remove
    them, so it deliberately does not filter on state.
    """
    return [
        MappingRule.from_static_data(data)
        for data in mariadb.get_mapping_rules_in_namespace(namespace)
    ]
