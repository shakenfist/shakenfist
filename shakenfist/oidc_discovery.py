# Copyright 2019 Michael Still and contributors
#
# Fetching an identity provider's OpenID Connect discovery document.
#
# This is how an issuer's configuration learns where the provider's
# endpoints are, instead of an operator having to find and paste each
# one. Like its sibling federation.py it is deliberately Flask-free and
# has no endpoint of its own: the issuer endpoints compose these two
# functions, and the order in which this module refuses a document is a
# security property rather than an implementation detail.
#
# Two things are worth understanding before changing anything here.
#
# The first is that a discovery document is attacker controlled input
# until proven otherwise. It names the URLs a person's browser and
# authorisation code will later be sent to, so a document which names
# somebody else's endpoints is a complete compromise of human login,
# and nothing later in the login flow would notice. One comparison
# stops that: the document's own issuer member must be exactly the
# issuer URL the operator configured. Everything else in this module is
# hygiene around that single check.
#
# The second is that the refusals are ordered cheapest first, as
# federation.py's are. The scheme check costs a parse and happens before
# anything is sent; the status and size checks cost nothing beyond what
# has already arrived; JSON parsing is the first step whose cost is
# proportional to what the far end chose to send, so it happens last of
# the mechanical checks and only on a body we have already bounded.
# Discovery runs only when an administrator asks -- never on a request
# path and never on a timer -- so the hazard here is a provider which is
# hostile or broken rather than a flood of anonymous callers. The
# ordering still costs nothing, and the bound on the body is the part
# that would be missed.

import json
import ssl
import time
import urllib.parse
from typing import Any
from typing import Optional

import requests
from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import federation
from shakenfist.config import config
from shakenfist.schema.sqlalchemy import stored_max_length
from shakenfist.schema.trusted_issuer_attributes import \
    TrustedIssuerAttributesData


LOG, _ = logs.setup(__name__)


# The path an OpenID Connect provider publishes its configuration at.
#
# It is appended to the issuer's URL, path and all, which is what OpenID
# Connect Discovery 1.0 section 4.1 specifies. RFC 8414 defines the
# other arrangement for OAuth 2.0 authorisation server metadata --
# insert the well known path between the host and the issuer's path --
# and the two agree only for an issuer whose path is empty. Both of the
# providers we care about serve the appended form: Keycloak answers
# https://host/realms/<realm>/.well-known/openid-configuration for the
# issuer https://host/realms/<realm>, and Authentik answers
# https://host/application/o/<slug>/.well-known/openid-configuration for
# the issuer https://host/application/o/<slug>/. Append is what works
# against real providers, so append is what we do.
WELL_KNOWN_PATH = '/.well-known/openid-configuration'

# The document members which describe an endpoint a client flow uses,
# and which a provider may legitimately not publish at all.
#
# A provider which supports the device code grant publishes
# device_authorization_endpoint (RFC 8628 section 4), so the presence of
# that member is the signal a client reads to decide whether the device
# flow is available. grant_types_supported says much the same thing and
# is deliberately not stored: it would be a second source of truth about
# one question, and a decision about what to do when the two disagree.
OPTIONAL_ENDPOINTS = [
    'authorization_endpoint',
    'token_endpoint',
    'device_authorization_endpoint',
]

# How much of the body to read at a time. Small enough that a document
# well over the cap is refused after a few reads rather than after the
# provider has finished sending it.
READ_CHUNK_BYTES = 8192

# How much of a document-supplied string a refusal message may quote.
# See _quoted() for why there is a limit at all.
_MESSAGE_VALUE_LIMIT = 120


def discovery_url(issuer_url: str) -> str:
    """Where to fetch this issuer's discovery document from.

    Exactly one trailing slash is stripped before the well known path is
    appended, and the issuer URL is otherwise untouched.

    One slash, because the slash is load bearing on both sides.
    Authentik's issuer URL carries a trailing slash and its iss claim
    carries it too, so an operator configures it with the slash and is
    right to -- fetch() compares the document's issuer member against
    that exact string. Concatenating naively would then request
    .../<slug>//.well-known/openid-configuration, which many servers
    answer with a 404 that reads exactly like "this provider does not
    support discovery".

    This function is the only place in this module which is allowed to
    be forgiving about a trailing slash, and that is the point: the
    comparison in fetch() must stay exact, so the one plausible reason
    to loosen it is handled here instead, in the construction of the URL
    we request. Stripping more than one slash would be a normalisation,
    which is the thing being kept out of the comparison.
    """
    base = issuer_url[:-1] if issuer_url.endswith('/') else issuer_url
    return base + WELL_KNOWN_PATH


class _TrustAnchorAdapter(requests.adapters.HTTPAdapter):
    """A requests adapter which verifies TLS the way the JWKS fetch does.

    The only job of this class is to get federation.jwks_ssl_context()
    into urllib3's pool manager, because the obvious way of pointing
    requests at a CA bundle -- handing its request methods a `verify`
    argument naming the bundle file -- *replaces* the system trust store
    rather than adding to it. A deployment with a private Keycloak
    behind its own CA and a public issuer like GitHub needs both sets of
    anchors, so that argument would fix one provider and silently break
    the other. That is the identical trap
    jwks_ssl_context() exists to document in ssl.create_default_context,
    and its docstring explains it; this class is how requests is told to
    use the union that function already builds.

    Reusing that context rather than building a second one is also why
    there is no separate CA bundle setting for discovery. One setting
    covers both fetches, and the gunicorn worker start already calls
    jwks_ssl_context() so that an unloadable bundle path is a log line at
    deploy time rather than a failed login weeks later.
    """

    def __init__(self, ssl_context: Optional[ssl.SSLContext]) -> None:
        # Set before the base class is constructed, because
        # HTTPAdapter.__init__ builds the pool manager and so calls
        # init_poolmanager() below while it runs.
        self._ssl_context = ssl_context
        super().__init__()

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> Any:
        # None is the ordinary case -- no extra CA bundle configured --
        # and is also urllib3's default, so it is passed through rather
        # than special cased.
        kwargs['ssl_context'] = self._ssl_context
        return super().init_poolmanager(*args, **kwargs)


def _discovery_session() -> requests.Session:
    """A session which verifies TLS the way the JWKS fetch does.

    Raises JWKSTrustAnchorUnusable if the configured CA bundle cannot be
    loaded, which is jwks_ssl_context()'s contract and is deliberately
    not caught here: that failure is our own misconfiguration rather
    than anything about the provider, and reporting it as a discovery
    failure would send an operator to look for a fault at their identity
    provider which is in fact in their config file.
    """
    session = requests.Session()

    # Only https is mounted over. fetch() refuses a non-https issuer URL
    # before it builds a session at all, and redirects are not followed,
    # so nothing here can reach a plaintext URL; mounting this adapter
    # on http:// as well would only make it look as though something
    # might.
    session.mount('https://', _TrustAnchorAdapter(
        federation.jwks_ssl_context()))
    return session


def _quoted(value: str) -> str:
    """A document-supplied string, quoted and bounded, for a message.

    Every refusal below names the value it refused so an operator can
    see what their provider actually said. Those values come out of the
    document, which is bounded only by FEDERATION_DISCOVERY_MAX_BYTES --
    so a provider which is hostile or merely broken can put the whole of
    that budget into one of them, and it would land both in the response
    to the administrator and in the log line this module writes. A
    sixty kilobyte refusal is not a vulnerability but it is a poor thing
    to put in a log aggregator, and truncating costs nothing. The real
    length is kept, because "it is enormous" is itself the useful
    diagnosis when a provider returns something absurd.
    """
    if len(value) <= _MESSAGE_VALUE_LIMIT:
        return f'"{value}"'
    return (f'"{value[:_MESSAGE_VALUE_LIMIT]}..." ({len(value)} characters '
            f'in total)')


def _require_fits(value: str, what: str) -> None:
    """Refuse a document value too long for the column which stores it.

    Every value this module returns is written to trusted_issuer_
    attributes, and until this phase each of those columns held something
    an operator had typed and the API had already length checked. A
    discovery document is the first third party input to reach them, and
    it is bounded only by FEDERATION_DISCOVERY_MAX_BYTES, so a provider
    publishing a 2000 character endpoint would otherwise reach the
    database layer unchecked.

    Refusing here rather than there is what makes the two write paths
    agree. The create path builds TrustedIssuerAttributesData in its
    constructor, where Pydantic would raise ValidationError and the
    administrator would get a 500 instead of the documented 502; the
    update path sets the same fields with setattr, which skips validation
    entirely because the model is not configured with
    validate_assignment. So one path failed obscurely, the other not at
    all, and jwks_uri -- whose column is smaller than the model admits --
    could be truncated on its way in. A truncated key set URL is a
    signature check against the wrong place, which is the single thing
    this module exists to prevent.

    The limit comes from stored_max_length() rather than from the
    model's declared max_length, because those two numbers are not the
    same and only one of them is true of the column.
    """
    limit = stored_max_length(TrustedIssuerAttributesData, what)
    if limit is None or len(value) <= limit:
        return
    raise exceptions.OIDCDiscoveryFailed(
        f'{what} {_quoted(value)} is longer than the {limit} characters '
        f'this cluster can store for it, so it would be refused or '
        f'truncated on the way into the database')


def _require_https(url: str, what: str) -> None:
    """Refuse a URL which is not https, naming which member it came from.

    Parsed rather than prefix matched. urllib.parse lower cases the
    scheme, so an issuer which publishes HTTPS:// is accepted and no
    caller has to think about case; a prefix test would also accept a
    string which is not a URL at all.
    """
    try:
        scheme = urllib.parse.urlparse(url).scheme
    except ValueError as e:
        raise exceptions.OIDCDiscoveryFailed(
            f'{what} {_quoted(url)} is not a usable URL: {e}') from e

    if scheme != 'https':
        raise exceptions.OIDCDiscoveryFailed(
            f'{what} {_quoted(url)} is not https. Discovery refuses '
            f'plaintext because anything fetched or published over it '
            f'can be substituted by anyone on the network path, which '
            f'would make every other check here theatre')


def fetch(issuer_url: str) -> dict[str, Any]:
    """This issuer's discovery document, validated, or raise.

    Returns only the members we store -- issuer, jwks_uri, and whichever
    of the optional endpoints the provider published -- rather than the
    whole document, so that a member nobody has considered cannot reach
    an issuer's configuration or, through it, a client.

    Raises OIDCDiscoveryFailed for every way the provider or the document
    can be wrong, with a message naming what failed. The callers are
    administrative endpoints, and the operator reading the message is
    choosing between a dozen plausible mistakes -- a URL with the wrong
    realm in it, a provider which publishes no document, a trailing slash
    which does or does not belong.

    JWKSTrustAnchorUnusable propagates instead of being wrapped. It says
    our CA bundle is unloadable rather than anything about this provider,
    and the endpoints already tell those two apart.
    """
    # Cheapest refusal first, and before anything is sent: the URL we are
    # about to fetch must be https. Everything below checks a document
    # against what we believe about the issuer, and a document fetched
    # over plaintext can be rewritten in flight by anyone on the path, so
    # none of those checks would mean anything. The stored issuer_url
    # field is not itself constrained to https, so this is the check
    # which keeps discovery from ever requesting a plaintext URL.
    _require_https(issuer_url, 'issuer_url')

    url = discovery_url(issuer_url)

    # One deadline over the whole exchange, started before the connect.
    # The scalar passed to requests below is a per-operation timeout and
    # is kept for what it is good at -- failing a dead host fast -- but
    # it says nothing about how long the transfer as a whole may take.
    timeout = config.FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout

    session = _discovery_session()
    try:
        try:
            # One try block covers the request and the body read, because
            # a connection which fails part way through the body raises
            # the same family of exceptions as one which never
            # established -- a reset by a provider behind an overloaded
            # proxy is an ordinary way for this to fail, and it would
            # otherwise reach an administrative endpoint as an unhandled
            # library exception rather than as a discovery failure.
            response = session.get(
                url,
                # Not followed, deliberately. The JWKS fetch refuses
                # redirects too -- PyJWT builds its opener that way --
                # and the operator guide tells operators that a
                # provider's URL must be the final one, so behaving
                # differently here would surprise somebody who had read
                # it. The reason to refuse is that a redirect moves the
                # decision about where we fetch from out of the
                # configuration and into whatever answered.
                allow_redirects=False,
                # Streamed so that the body can be bounded as it arrives,
                # rather than after requests has already buffered
                # whatever the far end decided to send.
                stream=True,
                timeout=timeout)

            try:
                # A non-2xx answer, with a redirect called out
                # separately. The symptom of a redirect we did not
                # follow is otherwise indistinguishable from a provider
                # which does not publish a document at all, which is a
                # diagnosis an operator should not have to make from a
                # packet capture.
                if response.status_code < 200 or response.status_code > 299:
                    if 300 <= response.status_code <= 399:
                        location = response.headers.get('Location', '')
                        raise exceptions.OIDCDiscoveryFailed(
                            f'{url} answered with a {response.status_code} '
                            f'redirect to "{location}". Discovery does not '
                            f'follow redirects, so an issuer URL must be '
                            f'the one which answers')
                    raise exceptions.OIDCDiscoveryFailed(
                        f'{url} answered with status {response.status_code}')

                # Read to a cap rather than trusting Content-Length,
                # which is what the far end claims rather than what it
                # sends. Nothing else bounds a response which is not a
                # discovery document at all -- an HTML login page, or a
                # tarball behind a misconfigured reverse proxy are both
                # things providers have served at this path -- and a real
                # document is a few kilobytes. The chunks arrive already
                # decompressed, so a response which only claims to be
                # small is refused at the same point as one which is
                # honestly large.
                cap = config.FEDERATION_DISCOVERY_MAX_BYTES
                body = b''
                for chunk in response.iter_content(
                        chunk_size=READ_CHUNK_BYTES):
                    body += chunk
                    if len(body) > cap:
                        raise exceptions.OIDCDiscoveryFailed(
                            f'{url} returned more than '
                            f'FEDERATION_DISCOVERY_MAX_BYTES ({cap}) bytes. '
                            f'A discovery document is a few kilobytes, so '
                            f'this is unlikely to be one')
                    # requests' timeout bounds each connect and each
                    # socket read, never the transfer, so a provider
                    # returning one byte just inside the read timeout
                    # never trips it: the ceiling without this check is
                    # the byte cap times the timeout, which on the
                    # defaults is days rather than seconds. The deadline
                    # is monotonic because a clock step backwards during
                    # a slow fetch would otherwise extend it.
                    if time.monotonic() > deadline:
                        raise exceptions.OIDCDiscoveryFailed(
                            f'{url} took longer than '
                            f'FEDERATION_DISCOVERY_FETCH_TIMEOUT_SECONDS '
                            f'({timeout}s) to send {len(body)} bytes, so it '
                            f'is being dripped rather than served')
            finally:
                # Closing a streamed response is what returns the
                # connection, and on the oversized path above it is also
                # what stops the provider sending the rest of whatever it
                # was sending.
                response.close()
        except requests.exceptions.RequestException as e:
            # The type name is included because the distinction matters
            # to whoever has to fix it: a connect timeout is usually a
            # firewall, an SSL error is usually the CA bundle, and a DNS
            # failure is usually a typo in the issuer URL.
            raise exceptions.OIDCDiscoveryFailed(
                f'could not fetch {url}: {type(e).__name__}: {e}') from e
    finally:
        session.close()

    try:
        document = json.loads(body)
    except ValueError as e:
        # UnicodeDecodeError is a ValueError, so a body which is not even
        # UTF-8 lands here too.
        raise exceptions.OIDCDiscoveryFailed(
            f'{url} did not return JSON: {e}') from e

    if not isinstance(document, dict):
        raise exceptions.OIDCDiscoveryFailed(
            f'{url} returned JSON which is not an object')

    # This is the security property of the whole module, and the reason
    # it exists in a file of its own with this much comment in it.
    #
    # The document just fetched names the endpoints a person's browser
    # and authorisation code will be sent to. A document which names
    # somebody else's endpoints is therefore a complete compromise of
    # human login, and the single thing standing between us and one is
    # that the document says it belongs to the issuer we asked about.
    #
    # The comparison is exact: byte for byte, against the issuer_url this
    # function was passed, and not against the slash-stripped URL it
    # fetched. No normalisation, no case folding, no trailing slash
    # tolerance, no prefix match. It is the mirror of the equally exact
    # comparison the exchange makes against a token's iss claim, and for
    # the same reason -- a loose comparison here is a way to accept
    # endpoints belonging to somewhere else entirely.
    #
    # The trailing slash which tempts people to loosen it is handled in
    # discovery_url() above, which is where it belongs. An operator who
    # configures Authentik without the slash which Authentik puts in its
    # own iss claim gets a refusal naming both strings, and that is the
    # correct outcome rather than a nuisance: their token exchange was
    # going to fail on that same difference later, and discovery finding
    # it while they are configuring the issuer is the whole benefit.
    advertised = document.get('issuer')
    if not isinstance(advertised, str):
        raise exceptions.OIDCDiscoveryFailed(
            f'{url} returned a document with no issuer member, so there is '
            f'nothing to check the endpoints in it against')
    issuer_matches = advertised == issuer_url
    if not issuer_matches:
        raise exceptions.OIDCDiscoveryFailed(
            f'{url} returned a document for issuer {_quoted(advertised)}, '
            f'but this issuer is configured as {_quoted(issuer_url)}. The two '
            f'must match exactly, including any trailing slash')

    # jwks_uri is the one member a conforming provider must publish and
    # the one we cannot do without: an issuer with no key set cannot
    # verify a signature at all. It is also the member an operator may
    # legitimately override, so this validates what the provider said
    # without deciding what gets written.
    jwks_uri = document.get('jwks_uri')
    if not isinstance(jwks_uri, str) or not jwks_uri:
        raise exceptions.OIDCDiscoveryFailed(
            f'{url} returned a document with no jwks_uri, so it says nothing '
            f'about where this issuer publishes its signing keys')
    _require_https(jwks_uri, 'jwks_uri')
    _require_fits(jwks_uri, 'jwks_uri')

    discovered: dict[str, Any] = {
        'issuer': advertised,
        'jwks_uri': jwks_uri,
    }
    for member in OPTIONAL_ENDPOINTS:
        value = document.get(member)
        if value is None:
            # Absent is a legitimate answer and means the provider does
            # not offer the flow that endpoint belongs to.
            continue
        if not isinstance(value, str) or not value:
            raise exceptions.OIDCDiscoveryFailed(
                f'{url} returned a document whose {member} is not a URL')
        # Published rather than fetched, but an endpoint we would send a
        # browser to over plaintext is worth refusing for the same reason
        # the fetch is: it is the credential-carrying half of the flow.
        _require_https(value, member)
        _require_fits(value, member)
        discovered[member] = value

    LOG.with_fields({
        'issuer_url': issuer_url, 'discovery_url': url,
        'endpoints': sorted(discovered.keys())
    }).info('Fetched an OIDC discovery document')
    return discovered
