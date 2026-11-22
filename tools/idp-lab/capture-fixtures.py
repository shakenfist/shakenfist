#!/usr/bin/python3
# Copyright 2026 Michael Still and contributors

"""Capture the provider documents under shakenfist/tests/files/idp/.

Run through capture-fixtures.sh, which supplies the lab's settings and
credentials from the environment file lab-up.sh generated.

For each of the two providers this drives a complete device login --
the flow sf-client uses -- against the running lab and writes two
files: the discovery document the provider served, and the decoded
claim set of the identity token that login produced.

Driving a real login rather than asking the provider what it would emit
is the point. Three of the things these captures are relied on for are
only true of a token: that the group claim is an array even for a
person in one group, that the audience is the bare client id rather
than anything resembling an API URL, and that one provider sends azp
and typ while the other sends neither.

The captures cannot be byte-identical between runs. Keys, timestamps,
session identifiers, token identifiers and the subject are per-login or
per-container. What is stable is the set of endpoint names in each
discovery document, the issuers, the set of claim names, and the shape
and value of the group claim -- which is what a reviewer comparing a
regenerated capture with the committed one should read.

No credential and no signed token is written anywhere: only the
decoded claim set, with the subject replaced. The identity is an
invented lab person, and this refuses to write a claim set which names
anybody else, because a capture which leaks a real person's
identifiers cannot be un-committed.
"""

import argparse
import base64
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


# The subject is the one member of either claim set which is generated
# rather than invented: one provider makes it a uuid and the other a
# hash of the user id, and both are stable for a person at a given
# provider. Replaced with something obviously not a real subject.
SUBJECT_PLACEHOLDER = '00000000-0000-0000-0000-000000000000'

# Every member of a claim set which could name a person. Each one must
# already hold a value derived from the invented lab person, or the
# capture is refused rather than written: see the module docstring.
IDENTIFYING_CLAIMS = ['email', 'preferred_username', 'nickname', 'name',
                      'given_name', 'family_name']

# How long to wait for a device login to be completed. The login is
# driven from here rather than by a person, so this is a guard against
# a provider which never answers, not a human-scale timeout -- and one
# provider's device codes expire in sixty seconds, which is the real
# deadline.
LOGIN_DEADLINE = 90


class CaptureFailed(Exception):
    pass


class Session:
    """One provider, one cookie jar, one TLS context.

    The lab's certificate is self signed, so verification is off here.
    Everything this talks to is on loopback and was started by the
    caller; a cluster fetching a discovery document is the thing which
    must verify, and it cannot, which is why the lab replaces one
    provider's certificate rather than trusting it.
    """

    # One provider's API enforces Django's cross-site request forgery
    # protection as soon as the request is authenticated by a session,
    # which a login is from its second half onwards. Two things are
    # then required of every write, and the failure if either is
    # missing is an HTTP 500 carrying an HTML page: a Referer naming
    # the same origin, and this header carrying the value of the
    # cookie of the same name.
    CSRF_COOKIE = 'authentik_csrf'
    CSRF_HEADER = 'X-authentik-CSRF'

    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(self.jar))

    def _csrf_headers(self):
        headers = {'Referer': self.base_url + '/'}
        for cookie in self.jar:
            if cookie.name == self.CSRF_COOKIE:
                headers[self.CSRF_HEADER] = cookie.value
        return headers

    def request(self, url, data=None, content_type=None, headers=None):
        """Fetch a URL, following redirects, and return (url, body).

        A redirect after a POST is followed as a GET, which is what a
        browser does and what both providers' flows expect: each one
        answers a completed step with a redirect back to the next
        challenge. The URL returned is the one the response finally
        came from, because one provider says which flow to drive next
        only in that URL.
        """
        if not url.startswith('http'):
            url = self.base_url + url

        request = urllib.request.Request(url, data=data,
                                         headers=headers or {})
        if content_type:
            request.add_header('Content-Type', content_type)
        if data is not None:
            for name, value in self._csrf_headers().items():
                request.add_header(name, value)

        try:
            with self.opener.open(request, timeout=30) as response:
                return response.geturl(), response.read()
        except urllib.error.HTTPError as e:
            # The body of a refusal is the whole of the diagnosis for
            # both providers, so it is read and kept rather than
            # discarded with the exception.
            raise CaptureFailed(
                '%s answered HTTP %s: %s' % (url, e.code, e.read()[:500])) from e

    def form(self, url, fields):
        return self.request(url, data=urllib.parse.urlencode(fields).encode(),
                            content_type='application/x-www-form-urlencoded')

    def json_request(self, url, document=None):
        data = None
        if document is not None:
            data = json.dumps(document).encode()
        _, body = self.request(url, data=data, content_type='application/json',
                               headers={'Accept': 'application/json'})
        return json.loads(body)


def device_code(session, endpoint, client_id, scopes):
    """Ask for a device code, which is where every device login starts."""
    _, body = session.form(endpoint, {'client_id': client_id, 'scope': scopes})
    answer = json.loads(body)
    if 'user_code' not in answer:
        raise CaptureFailed('The device endpoint issued no user code: %s' % answer)
    return answer


def poll_for_token(session, endpoint, client_id, code):
    """Poll the token endpoint the way a command line client does.

    authorization_pending is the only answer worth waiting through. Any
    other error is a configuration which will not start working, and
    waiting on it hides what it said.
    """
    deadline = time.time() + LOGIN_DEADLINE
    while time.time() < deadline:
        try:
            _, body = session.form(endpoint, {
                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                'client_id': client_id,
                'device_code': code})
            answer = json.loads(body)
        except CaptureFailed as e:
            if 'authorization_pending' not in str(e):
                raise
            time.sleep(2)
            continue

        if 'id_token' in answer:
            return answer['id_token']
        if answer.get('error') != 'authorization_pending':
            raise CaptureFailed('The token endpoint refused the device code: %s' % answer)
        time.sleep(2)

    raise CaptureFailed('The device login did not complete within %ss.' % LOGIN_DEADLINE)


def html_form_action(page, marker=None):
    """The action of the first form on a page, unescaped.

    Both of Keycloak's pages in a device login are HTML forms, so this
    is a login driven the way a person drives it. A marker is a field
    name which must be present, so that a page which is not the page
    expected is reported rather than posted to.
    """
    text = page.decode('utf-8', errors='replace')
    if marker and ('name="%s"' % marker) not in text:
        raise CaptureFailed('No field named %s on the page served: %s' % (marker, text[:300]))

    match = re.search(r'action="([^"]+)"', text)
    if not match:
        raise CaptureFailed('No form on the page served: %s' % text[:300])
    return match.group(1).replace('&amp;', '&'), text


def keycloak_id_token(settings):
    """Complete a Keycloak device login and return its identity token.

    Three things about this provider's device login are worth knowing
    and are all exercised here: the complete verification URI skips the
    code entry page and lands on the login form; the login form posts
    to a one-shot URL which has to be read off the page; and there is a
    consent page afterwards which belongs to the device grant itself
    rather than to the client's consent setting, so a person who signs
    in and walks away has not finished logging in.
    """
    session = Session(settings['keycloak_url'])
    issuer = settings['keycloak_issuer']
    endpoints = issuer + '/protocol/openid-connect'

    codes = device_code(session, endpoints + '/auth/device',
                        settings['client_id'], settings['scopes'])

    _, page = session.request(codes['verification_uri_complete'])
    action, _ = html_form_action(page, marker='password')
    _, page = session.form(action, {'username': settings['person'],
                                    'password': settings['person_password'],
                                    'credentialId': ''})

    text = page.decode('utf-8', errors='replace')
    if 'name="accept"' in text:
        action, text = html_form_action(page, marker='accept')
        consent = re.search(r'name="code" value="([^"]*)"', text)
        if not consent:
            raise CaptureFailed('The consent page carried no code: %s' % text[:300])
        if action.startswith('/'):
            action = settings['keycloak_url'] + action
        session.form(action, {'code': consent.group(1), 'accept': 'Yes'})

    return poll_for_token(session, endpoints + '/token',
                          settings['client_id'], codes['device_code'])


def authentik_flow_target(url):
    """The flow slug and query string an interface URL names.

    This provider answers each step of a device login with a redirect
    to a flow interface page, and the only statement of which flow to
    drive next is that page's own URL.
    """
    match = re.search(r'/if/flow/([^/]+)/', url)
    if not match:
        return None, None

    # Unquoted, because the executor takes the interface page's query
    # string as one parameter of its own and encodes it again. Handing
    # it the string as it appears in the URL encodes the escapes
    # twice, and the provider answers that with a 500 and an HTML page.
    return match.group(1), urllib.parse.unquote(urllib.parse.urlparse(url).query)


def authentik_id_token(settings):
    """Complete an Authentik device login and return its identity token.

    This provider has no HTML forms to post: its own interface drives
    the flow executor API, and so does this. The loop below is the
    executor's contract -- fetch a challenge, answer the component it
    names, repeat -- and it ends when the flow answers a final
    redirect.

    Two stages are answered with nothing at all. The authenticator
    validation stage skips itself when the person has enrolled no
    second factor, and the device code finish stage is a single button
    the person must press, which appears even though the provider's
    authorization flow is the implicit consent one.
    """
    session = Session(settings['authentik_url'])
    codes = device_code(session, '/application/o/device/',
                        settings['client_id'], settings['scopes'])

    url, _ = session.request(codes['verification_uri_complete'])
    slug, query = authentik_flow_target(url)
    if not slug:
        # This is the failure a stock install produces: the brand has
        # no device code flow, the verification page answers a bare 404
        # with an empty body, and the reason is only in the server's
        # log.
        raise CaptureFailed(
            'The verification URI did not lead to a flow: %s. Check the brand '
            'has a device code flow.' % url)

    def executor():
        return '/api/v3/flows/executor/%s/?%s' % (
            slug, urllib.parse.urlencode({'query': query}))

    # Each answer's own response carries the next challenge, so the
    # loop posts and reads rather than posting and re-fetching: a
    # stage answered twice is a flow which never advances.
    challenge = session.json_request(executor())
    for _ in range(12):
        component = challenge.get('component')
        print('  stage %s' % component)

        if component == 'xak-flow-redirect':
            url, _ = session.request(challenge['to'])
            slug, query = authentik_flow_target(url)
            if not slug:
                break
            challenge = session.json_request(executor())
            continue

        if component == 'ak-stage-identification':
            answer = {'uid_field': settings['person']}
        elif component == 'ak-stage-password':
            answer = {'password': settings['person_password']}
        elif component == 'ak-provider-oauth2-device-code-finish':
            answer = {}
        else:
            raise CaptureFailed(
                'Unexpected flow stage %s: %s' % (component, json.dumps(challenge)[:400]))

        challenge = session.json_request(executor(), answer)
    else:
        raise CaptureFailed('The login flow did not finish in twelve steps.')

    return poll_for_token(session, '/application/o/token/',
                          settings['client_id'], codes['device_code'])


def id_token_claims(token):
    """The decoded payload of a JWT, with no signature verification.

    Nothing is being trusted here: the token was just issued to this
    process by the provider it was asked of, over a connection to
    loopback, and what is wanted is what it says rather than whether it
    is true.
    """
    payload = token.split('.')[1]
    payload += '=' * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def redact(claims, settings):
    """Replace the subject, and refuse a claim set naming anybody real.

    The guard matters more than the replacement. A lab pointed at a
    directory of real people -- which is a reasonable thing to do with
    these scripts -- would otherwise write somebody's name, address and
    group memberships into a file destined for a public repository.
    """
    expected = {settings['person'], settings['person_email'],
                settings['person_name']}
    expected |= set(settings['person_name'].split())

    for claim in IDENTIFYING_CLAIMS:
        if claim in claims and claims[claim] not in expected:
            raise CaptureFailed(
                'The %s claim is %r, which is not the lab person. Refusing to write a '
                'capture which names somebody real.' % (claim, claims[claim]))

    redacted = dict(claims)
    redacted['sub'] = SUBJECT_PLACEHOLDER
    return redacted


def write_json(path, document):
    with open(path, 'w') as f:
        json.dump(document, f, indent=2)
        f.write('\n')
    print('wrote %s' % path)


def capture(provider, settings, output_dir):
    """Both files for one provider, and what is worth saying about them."""
    issuer = settings['%s_issuer' % provider]
    session = Session(settings['%s_url' % provider])

    # The discovery URL is the issuer with exactly one slash before the
    # well known path, which is what the cluster builds and is the
    # reason one provider's trailing slash has to be got right.
    _, body = session.request(
        issuer.rstrip('/') + '/.well-known/openid-configuration')
    document = json.loads(body)
    if document['issuer'] != issuer:
        raise CaptureFailed(
            '%s calls itself %s, not %s.' % (provider, document['issuer'], issuer))
    write_json(os.path.join(output_dir, '%s-discovery.json' % provider), document)

    if provider == 'keycloak':
        token = keycloak_id_token(settings)
    else:
        token = authentik_id_token(settings)

    claims = redact(id_token_claims(token), settings)
    if not isinstance(claims.get('groups'), list) or not claims['groups']:
        raise CaptureFailed(
            'The identity token carries no group claim: %s. Check the scopes the '
            'login asked for.' % json.dumps(claims))
    write_json(os.path.join(output_dir, '%s-id-token-claims.json' % provider), claims)

    print('  issuer      %s' % document['issuer'])
    print('  endpoints   %s' % ', '.join(
        sorted(k for k in document if k.endswith('_endpoint') or k == 'jwks_uri')))
    print('  claims      %s' % ', '.join(sorted(claims)))
    print('  groups      %s' % json.dumps(claims['groups']))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output-dir', required=True,
                        help='where the four captures are written')
    parser.add_argument('--provider', action='append', choices=['keycloak', 'authentik'],
                        help='capture one provider rather than both; repeatable')
    args = parser.parse_args()

    settings = {}
    for name in ['keycloak_url', 'keycloak_issuer', 'authentik_url',
                 'authentik_issuer', 'client_id', 'scopes', 'person',
                 'person_password', 'person_email', 'person_name']:
        value = os.environ.get('IDP_LAB_CAPTURE_%s' % name.upper())
        if not value:
            print('IDP_LAB_CAPTURE_%s is not set. Run capture-fixtures.sh '
                  'rather than this script.' % name.upper(), file=sys.stderr)
            return 1
        settings[name] = value

    for provider in args.provider or ['keycloak', 'authentik']:
        print('Capturing %s.' % provider)
        try:
            capture(provider, settings, args.output_dir)
        except CaptureFailed as e:
            print('%s: %s' % (provider, e), file=sys.stderr)
            return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
