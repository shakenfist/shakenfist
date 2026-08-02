# Copyright 2019 Michael Still and contributors
#
# Validating identity tokens from trusted issuers.
#
# This module is the part of the federated exchange that decides whether
# to believe a token at all. It is deliberately Flask-free and has no
# endpoint of its own: the exchange in
# docs/plans/PLAN-auth-federation-phase-03-exchange.md composes these
# functions in a specific order, and that order is a security property
# rather than an implementation detail.
#
# The cheap, local rejections come first. Reading the issuer out of an
# unverified token costs a base64 decode; fetching a JWKS costs an
# outbound HTTP request inside a request-handling worker. Anything that
# can be refused without the network must be refused without it, or an
# unauthenticated caller with a made-up issuer can tie up workers.

import threading
from typing import Any
from typing import Optional

import jwt
from jwt import PyJWKClient
from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.trusted_issuer import TrustedIssuer
from shakenfist.trusted_issuer import TrustedIssuers


LOG, _ = logs.setup(__name__)


# Asymmetric signatures only, pinned rather than taken from the token.
#
# This is the classic JWT failure. PyJWT will not honour "alg": "none",
# but if HS* were in this list an attacker could sign a token using the
# issuer's *public* key as the HMAC secret -- the public key being, by
# definition, public -- and we would verify it happily. A federated
# issuer signs asymmetrically, so there is no reason for a symmetric
# algorithm to ever appear here.
ALLOWED_ALGORITHMS = [
    'RS256', 'RS384', 'RS512',
    'PS256', 'PS384', 'PS512',
    'ES256', 'ES384', 'ES512',
]

# Clock skew tolerance when checking exp and nbf, in seconds.
#
# Zero, deliberately. Every second of leeway is a second in which an
# expired token still works, and federated tokens are short lived by
# design. If a deployment turns out to need tolerance here it should
# arrive as configuration with that trade-off written down, not as a
# default nobody chose.
#
# The skew that matters is between the identity provider's clock, which
# stamps these claims, and the clock of whichever API node verifies
# them. The presenting client stamps nothing, so its clock is
# irrelevant. Skew *between* API nodes does not cause failures of its
# own, but it does make these failures intermittent -- the same token
# accepted by one node and refused by another, which behind a load
# balancer is far harder to diagnose than a consistent refusal.
LEEWAY_SECONDS = 0


class JWKSCache:
    """One PyJWKClient per trusted issuer, plus a lock per issuer.

    PyJWKClient already caches the key set and already refetches once
    when it sees an unknown key id, which is what makes issuer key
    rotation work without configuration. What it does not do is
    collapse concurrent refetches: if fifty CI jobs present tokens
    signed with a freshly rotated key at the same moment, all fifty
    miss the cache and all fifty fetch the JWKS. That is a stampede
    against the identity provider, caused by us, at exactly the moment
    the provider is already doing something unusual.

    Holding a per-issuer lock across the lookup serialises those fifty:
    the first refetches and repopulates the shared key set cache, and
    the other forty-nine then find the key already there. The lock is
    per issuer so a slow or unreachable provider cannot block tokens
    from a healthy one.
    """

    def __init__(self):
        self._clients: dict[str, PyJWKClient] = {}
        self._locks: dict[str, threading.Lock] = {}
        # Guards the two dicts above, not the fetches themselves.
        self._registry_lock = threading.Lock()

    def _client_and_lock(
            self, issuer_uuid: str,
            jwks_uri: str) -> tuple[PyJWKClient, threading.Lock]:
        with self._registry_lock:
            client = self._clients.get(issuer_uuid)
            if client is None or client.uri != jwks_uri:
                # A changed jwks_uri means the operator repointed the
                # issuer, so the old client's cached keys are no longer
                # the right answer and the client is replaced.
                client = PyJWKClient(
                    jwks_uri,
                    cache_jwk_set=True,
                    lifespan=config.FEDERATION_JWKS_CACHE_SECONDS)
                self._clients[issuer_uuid] = client
                self._locks[issuer_uuid] = threading.Lock()
            return client, self._locks[issuer_uuid]

    def signing_key(self, issuer: TrustedIssuer, token: str) -> Any:
        """The key that signed this token, fetching the JWKS if needed."""
        jwks_uri = issuer.jwks_uri
        if not jwks_uri:
            raise exceptions.TokenValidationFailed(
                f'trusted issuer {issuer.name} has no jwks_uri')

        client, lock = self._client_and_lock(str(issuer.uuid), jwks_uri)
        with lock:
            try:
                return client.get_signing_key_from_jwt(token)
            except jwt.exceptions.PyJWKClientError as e:
                raise exceptions.TokenValidationFailed(
                    f'no signing key for this token from issuer '
                    f'{issuer.name}: {e}') from e
            except jwt.exceptions.DecodeError as e:
                raise exceptions.TokenValidationFailed(
                    f'token header could not be read: {e}') from e

    def forget(self, issuer_uuid: str) -> None:
        """Drop a cached client, for tests and issuer reconfiguration."""
        with self._registry_lock:
            self._clients.pop(str(issuer_uuid), None)
            self._locks.pop(str(issuer_uuid), None)


# The one cache object. Module level because the point of it is to be
# shared across requests in a worker.
JWKS_CACHE = JWKSCache()


def unverified_issuer(token: str) -> Optional[str]:
    """The iss claim of a token we have not verified yet.

    Reading claims from an unverified token is normally a mistake, and
    it is safe here only because the value is used for one thing: to
    look up which issuer's key should verify the signature. Nothing is
    believed until that verification succeeds. In particular the token
    is never asked where its own keys live -- the jwks_uri comes from
    the TrustedIssuer we configured, never from the token.
    """
    try:
        claims = jwt.decode(token, options={'verify_signature': False})
    except jwt.exceptions.PyJWTError as e:
        LOG.debug(f'Identity token could not be parsed: {e}')
        return None

    issuer = claims.get('iss')
    if not isinstance(issuer, str) or not issuer:
        return None
    return issuer


def issuer_for_token(token: str) -> TrustedIssuer:
    """The trusted issuer this token claims to come from.

    Raises UntrustedIssuer if the token names an issuer this cluster
    has not been told to believe, which is the cheap rejection that
    keeps a made-up iss from reaching the network.
    """
    claimed = unverified_issuer(token)
    if not claimed:
        raise exceptions.UntrustedIssuer('token carries no readable iss claim')

    # Issuers are cluster level and there is one per identity provider,
    # so the listing is small. Comparison is exact: no normalisation and
    # no trailing slash tolerance, because a loose comparison here is a
    # way to accept tokens from somewhere else entirely.
    for issuer in TrustedIssuers([]):
        if issuer.state.value != TrustedIssuer.STATE_DELETED and (
                issuer.issuer_url == claimed):
            return issuer

    raise exceptions.UntrustedIssuer(
        f'no trusted issuer configured for {claimed}')


def validate_token(token: str, issuer: TrustedIssuer) -> dict[str, Any]:
    """Verify a token against an issuer, returning its claims.

    Checks the signature against the issuer's published keys, then the
    audience, the issuer, and the lifetime. Raises
    TokenValidationFailed for every failure, with a message intended
    for the audit log rather than for the caller.
    """
    signing_key = JWKS_CACHE.signing_key(issuer, token)

    audience = issuer.audience
    issuer_url = issuer.issuer_url
    if not audience or not issuer_url:
        raise exceptions.TokenValidationFailed(
            f'trusted issuer {issuer.name} is incompletely configured')

    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=audience,
            issuer=issuer_url,
            leeway=LEEWAY_SECONDS,
            options={
                'verify_signature': True,
                'verify_exp': True,
                'verify_nbf': True,
                # iat is deliberately not verified. PyJWT reads it as
                # "not valid before", refusing any token whose iat is
                # ahead of our clock by more than the leeway -- so with
                # leeway at zero, an identity provider running one
                # second fast would have every token it issues refused
                # the instant it is issued. That is the likeliest
                # direction for skew to bite, and iat buys nothing to
                # pay for it: exp is what bounds the token's life, and
                # nbf is what expresses a deliberately postdated one.
                'verify_iat': False,
                'verify_aud': True,
                'verify_iss': True,
                # An identity token with no expiry is not a credential,
                # it is a permanent grant wearing one's clothes.
                'require': ['exp', 'iss', 'aud'],
            })
    except jwt.exceptions.PyJWTError as e:
        raise exceptions.TokenValidationFailed(
            f'token from {issuer.name} failed verification: '
            f'{type(e).__name__}: {e}') from e


def claim_matches(value: Any, matcher: Any) -> bool:
    """Does one claim value satisfy one matcher?

    Exact string comparison, or membership of a list of exact strings.
    No globbing, no regular expressions, no prefix matching, and no
    coercion: a claim of 1 does not match a matcher of "1", because a
    federation that compares loosely is a federation that eventually
    accepts the wrong repository.
    """
    if not isinstance(value, str):
        return False
    if isinstance(matcher, str):
        return value == matcher
    if isinstance(matcher, list):
        return any(value == alternative for alternative in matcher
                   if isinstance(alternative, str))
    return False


def match_claims(claims: dict[str, Any],
                 bound_claims: dict[str, Any]) -> dict[str, Any]:
    """Check a token's claims against a rule's, or raise ClaimMismatch.

    Returns the satisfied claims, which the exchange records in the
    minted key's provenance so an audit describes the grant as it was
    actually made rather than as the rule reads later.

    An empty bound_claims set raises rather than matching everything.
    Rule creation refuses to write one, so reaching here with one means
    a damaged row, and the safe reading of a damaged rule is that it
    grants nothing.
    """
    if not bound_claims:
        raise exceptions.ClaimMismatch(
            'rule binds no claims, refusing to treat that as matching '
            'every identity')

    satisfied = {}
    for claim, matcher in bound_claims.items():
        if claim not in claims:
            raise exceptions.ClaimMismatch(
                f'token does not carry the bound claim "{claim}"')
        if not claim_matches(claims[claim], matcher):
            raise exceptions.ClaimMismatch(
                f'claim "{claim}" does not match what the rule requires')
        satisfied[claim] = claims[claim]

    return satisfied


def validate_for_rule(token: str, rule: Any) -> tuple[dict[str, Any],
                                                      dict[str, Any]]:
    """Validate a token and check it against a rule.

    Returns (claims, satisfied_claims). Raises UntrustedIssuer,
    TokenValidationFailed or ClaimMismatch. The rule's own issuer is
    checked against the token's, so a token from issuer A cannot be
    exchanged through a rule that trusts issuer B.
    """
    issuer = issuer_for_token(token)

    if rule.issuer != issuer.name:
        raise exceptions.UntrustedIssuer(
            f'rule {rule.namespace}/{rule.name} accepts tokens from '
            f'{rule.issuer}, not {issuer.name}')

    claims = validate_token(token, issuer)
    satisfied = match_claims(claims, rule.bound_claims or {})
    return claims, satisfied
