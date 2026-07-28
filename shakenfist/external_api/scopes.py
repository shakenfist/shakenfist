# Scope vocabulary and derivation.
#
# A scope is a "<family>.<verb>" string naming a class of operation a
# key -- and every token minted from it -- may perform. Phase 3 of the
# auth federation plan chose to derive scopes mechanically rather than
# tag each endpoint by hand, so that coverage is automatic and a new
# endpoint cannot be forgotten. See
# docs/plans/PLAN-auth-federation-phase-03-exchange.md.
#
# The operator-visible vocabulary is deliberately three verbs. Adding a
# fourth is a decision, not a convenience: the test is whether anyone
# would sensibly write a mapping rule granting it on its own.

import re


# HTTP method to verb. HEAD is a GET without a body, so it reads.
VERBS = {
    'get': 'read',
    'head': 'read',
    'post': 'write',
    'put': 'write',
    'patch': 'write',
    'delete': 'delete',
}

# The scope a token carries when it was minted from a key with no
# scopes recorded, which is every key that predates this feature.
WILDCARD = '*'

# Granted separately from everything else, because holding it means
# administering the cluster rather than using it.
ADMIN = 'admin'

_LEADING_WORD = re.compile(r'[A-Z][a-z0-9]*')


def family_for_resource(resource_class):
    """The scope family a resource class belongs to.

    Class names in external_api follow "<Family><Qualifier...>Endpoint"
    closely enough to derive this: take the leading CamelCase word,
    lowercase it, and drop a trailing plural 's'. BlobsEndpoint and
    BlobMetadataEndpoint both give 'blob'.

    Where the leading word is misleading -- ClusterOperationEndpoint
    would give 'cluster' -- the class sets a scope_family attribute
    instead. That override is deliberate, greppable, and listed in the
    developer guide.

    Returns None when no family can be derived, which callers must
    treat as default-deny for a scoped token.
    """
    override = getattr(resource_class, 'scope_family', None)
    if override:
        return override

    name = resource_class.__name__
    if name.endswith('Endpoint'):
        name = name[:-len('Endpoint')]
    if not name:
        return None

    match = _LEADING_WORD.match(name)
    if not match:
        return None

    word = match.group(0).lower()
    # Plural class names (BlobsEndpoint) name the same family as their
    # singular sibling. Only strip a trailing 's' when something is
    # left, so a hypothetical 'S' class does not become ''.
    if len(word) > 1 and word.endswith('s'):
        word = word[:-1]
    return word or None


def verb_for_method(http_method):
    """The scope verb for an HTTP method, or None if unrecognised."""
    return VERBS.get(http_method.lower())


def required_scope(resource_class, http_method, override=None):
    """The scope a request needs, or None if it cannot be derived.

    ``override`` is the value recorded by the @scope decorator: a dict
    which may carry 'family', 'verb', or a fully formed 'scope'.

    None means the caller could not work out what to require. That is
    treated as default-deny for scoped tokens rather than as
    permission, because a scope system which silently allows what it
    cannot classify is not one.
    """
    override = override or {}
    if override.get('scope'):
        return override['scope']

    family = override.get('family') or family_for_resource(resource_class)
    verb = override.get('verb') or verb_for_method(http_method)
    if not family or not verb:
        return None
    return f'{family}.{verb}'


def satisfies(held, required):
    """Does a token holding ``held`` satisfy ``required``?

    ``held`` is the token's scope list. The wildcard satisfies
    everything, which is what keeps every pre-existing key working
    unchanged. A required scope of None -- derivation failed -- is
    satisfied only by the wildcard.
    """
    if held is None:
        # No scopes claim at all. Tokens minted before this feature
        # existed are in this state, and they came from unscoped keys,
        # so they are wildcard. Refusing them would break every token
        # in flight across an upgrade.
        return True

    if WILDCARD in held:
        return True

    if required is None:
        return False

    return required in held
