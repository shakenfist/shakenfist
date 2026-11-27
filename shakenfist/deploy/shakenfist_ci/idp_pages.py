# Copyright 2026 Michael Still and contributors
"""The person's half of a device login, through an identity provider's HTML.

``cluster_ci_tests/test_oidc_login.py`` drives ``sf-client login --flow
device`` against a real provider and plays the person it waits for:
open the verification URI, sign in, confirm the code. This module is the
only thing in that arrangement which knows what the provider's pages
look like.

**It is tied to the provider version the bring-up script pins**, and the
tag is pinned so that this is the only thing a provider upgrade can
break. **When it breaks, fix it: read the new pages and post what they
ask for.** Do not loosen it into something which cannot tell a changed
page from a refused login -- an approver which silently approves nothing
turns every assertion in that test into an assertion about a timeout.

The alternative is not less brittle, it is empty. A test which asked the
provider for an identity token with its direct access grant and handed
that to the cluster -- which is what the rest of that file does,
deliberately, for the things it covers -- does not run the device grant
at all: no device code, no poll loop, no interval, no expiry, and
nothing of the client under test but its last two requests. Playing the
person is the only way the flow itself is covered, and the price is
knowing three forms.

Kept free of imports from the rest of the suite, and of ``requests`` and
``shakenfist_client``, so that ``shakenfist/tests/test_ci_idp_pages.py``
can load it by path and exercise it against captured pages -- the same
arrangement, for the same reason, as ``sizing.py`` and ``retries.py``.
The cluster suite only runs against a deployed cluster and in a weekly
lane, so a parser which had quietly stopped selecting forms would
otherwise be discovered a week later.

What that costs is one argument: the HTTP session arrives from the
caller rather than being built here, exactly as ``retries.py`` takes its
poll as a callable. Anything with ``get(url, timeout=)`` and
``post(url, data=, timeout=)`` will do, where each answer has ``text``,
``url`` and ``status_code``; the suite passes a ``requests`` session
with the provider's certificate authority set on it, and the unit tests
pass captured pages.

Every refusal here names the forms and fields it did see, because the
failure this produces when the pages change has to say "the pages
changed" rather than "the login did not happen".

Nothing here asserts or fails a test: it runs in a thread beside a
login, and returns what it did so that the test's own thread does the
asserting.
"""

from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.parse import urljoin


# Each request to the provider's pages. Three of them at worst, which
# has to stay well inside the deadline the caller gives the whole login.
PAGE_TIMEOUT = 30


class PageSequenceChanged(RuntimeError):
    """A provider page is not the one this module was written against.

    A RuntimeError as well as itself, because the thread which plays the
    person records whatever it was stopped by rather than handling each
    kind -- and because the one thing that must not happen is this being
    caught and treated as a login which simply did not complete.
    """

    ...


class ProviderLoginPages(HTMLParser):
    """Sign in and confirm a device code, through the pages a provider serves.

    Three pages, read from a running container of the pinned image
    rather than from documentation:

    1. the verification URI the client printed answers with a sign in
       form, the one carrying a field named ``password``,
    2. posting the person's credentials to it answers with the device
       grant's confirmation form, the one carrying a hidden ``code``,
    3. posting that back with ``accept`` finishes the person's half, and
       the client's next poll gets a token.

    The module docstring is where the brittleness of that is argued.
    """

    def __init__(self, session, timeout=PAGE_TIMEOUT):
        super().__init__(convert_charrefs=True)
        self.session = session
        self.timeout = timeout
        self._forms = []

    # -- reading a page ------------------------------------------------

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self._forms.append({'id': attrs.get('id'),
                                'action': attrs.get('action'),
                                'fields': []})
        elif tag == 'input' and self._forms:
            # Inputs only. A button is sent because the caller named it
            # and never because the page carried it: a browser sends the
            # one submit the person clicked, and the confirmation form
            # carries both "accept" and "cancel", so sending the page's
            # buttons back would be asking the provider to guess which
            # one was pressed.
            self._forms[-1]['fields'].append(
                {'name': attrs.get('name'), 'value': attrs.get('value')})

    def forms_of(self, page):
        """Every form on a page, in order, with its action and its inputs."""
        self.reset()
        self._forms = []
        self.feed(page.text)
        return self._forms

    def form_asking_for(self, page, field):
        """The one form on this page which carries a field of this name.

        Chosen by what the form asks for rather than by its position or
        its identifier, because that is the part of a page which cannot
        change without the login changing too: a sign in form without a
        password field is not a sign in form. A provider's login page
        can carry a second form -- another authentication mechanism, an
        account recovery link -- so "the first one" is a guess, and a
        page carrying two of the form wanted is a page nobody has read
        yet.
        """
        forms = self.forms_of(page)
        wanted = [form for form in forms
                  if field in [f['name'] for f in form['fields']]]
        if len(wanted) != 1:
            raise PageSequenceChanged(
                'The provider answered %s with %d forms carrying a field '
                'named %r, and this expected exactly one. The page sequence '
                'this test drives has changed: what it got was %s. Read the '
                'new pages and post what they ask for.'
                % (page.url, len(wanted), field,
                   [{'id': form['id'], 'action': form['action'],
                     'fields': [f['name'] for f in form['fields']]}
                    for form in forms]))
        return wanted[0]

    def page_asks_again(self, page, field):
        """Is this answer another copy of the form which was just sent?

        Which is how this provider says no: a refused sign in and an
        unconfirmed confirmation are both answered with the form again
        rather than with a status code.
        """
        return bool([form for form in self.forms_of(page)
                     if field in [f['name'] for f in form['fields']]])

    def submit(self, page, form, typed):
        """Send a form back, with what the person typed or clicked added.

        Every input the page carried goes back as it stands, which is
        what a browser does with the hidden fields a login flow threads
        through its pages -- the session code, the execution and the
        confirmation's own code. An action may be relative, as this
        provider's second one is, so it is resolved against the page it
        came from; an absolute one is left alone by the same call.
        """
        body = {f['name']: f['value'] or ''
                for f in form['fields'] if f['name']}
        body.update(typed)
        return self.session.post(urljoin(page.url, form['action']),
                                 data=body, timeout=self.timeout)

    # -- being the person ---------------------------------------------

    def approve(self, verification_uri, user_code, username, password):
        """Sign in and confirm the code, and say what happened on the way.

        The URI is the one the client printed, whichever of the two it
        printed: a provider which sent a verification URI with the code
        already in it gets used as it stands, and one which did not has
        the code added as the query parameter RFC 8628 section 3.3.1
        names. That is the whole difference between them, and it is not
        HTML.
        """
        report = {'user_code': user_code, 'pages': [], 'approved': False}

        if 'user_code=' not in verification_uri:
            verification_uri = '%s%s%s' % (
                verification_uri, '&' if '?' in verification_uri else '?',
                urlencode({'user_code': user_code}))
            report['added_the_code_to_the_uri'] = True
        report['uri'] = verification_uri

        page = self.session.get(verification_uri, timeout=self.timeout)
        report['pages'].append(('sign in', page.status_code, page.url))
        form = self.form_asking_for(page, 'password')

        signed_in = self.submit(page, form, {'username': username,
                                             'password': password})
        report['pages'].append(
            ('confirm', signed_in.status_code, signed_in.url))

        # A provider which did not accept the credentials serves the
        # sign in form again, with its own error on it, and that is a
        # different thing from a page sequence which has changed. Said
        # here because it is a fact about this page, and because the
        # test which gets it needs to be told that the descriptor's
        # password is wrong rather than sent to read the HTML.
        if self.page_asks_again(signed_in, 'password'):
            raise PageSequenceChanged(
                'The provider did not accept %s\'s password: it answered the '
                'sign in with the sign in form again, at %s. The password in '
                'the provider descriptor is not the one this provider was '
                'configured with.' % (username, signed_in.url))

        form = self.form_asking_for(signed_in, 'code')

        done = self.submit(signed_in, form, {'accept': ''})
        report['pages'].append(('approved', done.status_code, done.url))

        # The provider's answer to the confirmation, checked only for
        # the two things which mean the person's half did not finish: a
        # refusal, or the same form again. Whether the login actually
        # completed is the client's poll to discover and the test's to
        # assert, which is why nothing here reads the page for a phrase
        # -- a phrase is a translation away from being wrong.
        if done.status_code != 200:
            raise PageSequenceChanged(
                'The provider answered the confirmation of the device login '
                'with HTTP %d at %s' % (done.status_code, done.url))
        if self.page_asks_again(done, 'code'):
            raise PageSequenceChanged(
                'The provider answered the confirmation of the device login '
                'with the confirmation form again, at %s, so the login was '
                'not confirmed' % done.url)

        report['approved'] = True
        return report
