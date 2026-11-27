# Copyright 2026 Michael Still and contributors

"""The parser which plays the person in a device login, against saved pages.

``shakenfist_ci/idp_pages.py`` posts to an identity provider's sign in
and confirmation forms so that ``cluster_ci_tests/test_oidc_login.py``
can drive a real ``sf-client login --flow device``. That test runs only
against a deployed cluster, and only in a weekly lane, so a parser which
had quietly stopped selecting the right form would be discovered a week
later and reported as a login which did not happen.

So the parser is kept free of suite, ``requests`` and client imports and
is loaded here by path, exactly as ``sizing.py`` and ``retries.py`` are,
and exercised against two captures of the pinned provider version taken
from a running container: the page its verification URI answers with,
and the page a completed sign in answers with. What that pins is the
part of the parser a reader would be tempted to simplify:

* the form is chosen by the field it asks for and not by its position,
  because a provider's login page can carry a second form and "the
  first one" is a guess;
* a relative action is resolved against the page it came from, which
  the confirmation form needs and the sign in form does not, so a
  parser which dropped it would still pass a test that only signed in;
* every input the page carried goes back as it stands, buttons do not,
  and what the person types or clicks is added;
* and a page this was not written against is refused with the forms and
  fields it did see, which is the behaviour that keeps a changed page
  from reading as an unconfirmed login.

The captures are scrubbed: the loopback address is the lab's, as in the
other captures beside them, and every per-login token in them -- the
session code, the execution, the tab, the confirmation's own code -- is
an obvious placeholder. No credential is in either page; the person's
password is posted to the first one and rendered nowhere.
"""

import importlib.util
import os

from shakenfist.tests import base


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PAGES_PATH = os.path.join(
    os.path.dirname(TESTS_DIR), 'deploy', 'shakenfist_ci', 'idp_pages.py')
CAPTURES = os.path.join(TESTS_DIR, 'files', 'idp')

SIGN_IN_PAGE = os.path.join(CAPTURES, 'keycloak-device-signin-page.html')
CONFIRM_PAGE = os.path.join(CAPTURES, 'keycloak-device-confirm-page.html')

# Where each capture was served from, which is what a relative action has
# to be resolved against and what every failure message has to name.
SIGN_IN_URL = ('https://localhost:8443/realms/shakenfist/login-actions/'
               'authenticate?client_id=shakenfist&tab_id=TAB-SCRUBBED')
CONFIRM_URL = ('https://localhost:8443/realms/shakenfist/login-actions/'
               'required-action?execution=OAUTH_GRANT&client_id=shakenfist')
DONE_URL = 'https://localhost:8443/realms/shakenfist/device/status'

# The value the capture's hidden field carries, placeholder and all. The
# point of asserting on it is that it went back unaltered.
GRANT_CODE = 'GRANT-CODE-SCRUBBED'


def _load_idp_pages():
    """The parser, loaded by path for the reason the module docstring gives."""
    spec = importlib.util.spec_from_file_location(
        'shakenfist_ci_idp_pages_under_test', PAGES_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


idp_pages = _load_idp_pages()


def _capture(path):
    with open(path) as f:
        return f.read()


class _Page:
    """An answer, as much of one as the parser reads."""

    def __init__(self, text, url, status_code=200):
        self.text = text
        self.url = url
        self.status_code = status_code


class _Session:
    """The session the parser is given, with the pages queued in order.

    Records every request so that a test can assert where a form was
    sent as well as what was in it -- the URL a relative action resolves
    to is the assertion, and it is not visible in the body.
    """

    def __init__(self, *pages):
        self.pages = list(pages)
        self.requests = []

    def _next(self, method, url, data=None):
        self.requests.append({'method': method, 'url': url, 'data': data})
        if not self.pages:
            raise AssertionError(
                'the parser made a %s to %s and nothing was queued for it'
                % (method, url))
        return self.pages.pop(0)

    def get(self, url, timeout=None):
        return self._next('GET', url)

    def post(self, url, data=None, timeout=None):
        return self._next('POST', url, data)


class IdpPagesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in = _capture(SIGN_IN_PAGE)
        self.confirm = _capture(CONFIRM_PAGE)

    def _pages(self, *queued):
        return idp_pages.ProviderLoginPages(_Session(*queued))

    # -- choosing a form -----------------------------------------------

    def test_the_sign_in_form_is_the_one_which_asks_for_a_password(self):
        pages = self._pages()
        page = _Page(self.sign_in, SIGN_IN_URL)

        form = pages.form_asking_for(page, 'password')
        self.assertEqual(
            sorted(['username', 'password', 'credentialId']),
            sorted(f['name'] for f in form['fields'] if f['name']),
            'the inputs read off the sign in form are not the ones it has')
        self.assertTrue(form['action'].startswith(
            'https://localhost:8443/realms/shakenfist/login-actions/'
            'authenticate?'), form['action'])

        # The submit button is not a field. A browser sends the one the
        # person clicked, so the parser adds the one it means to click
        # and never echoes what the page offered.
        self.assertNotIn('login', [f['name'] for f in form['fields']])

    def test_the_form_is_chosen_by_its_field_and_not_by_its_position(self):
        # A provider can serve more than one form on a login page -- a
        # second authentication mechanism, a recovery link -- and the
        # pinned version here serves one, so the alternative is
        # constructed rather than captured. The rule under test is that
        # the form carrying the field wins wherever it sits.
        decoy = ('<form id="kc-select-try-another-way-form" action="/other">'
                 '<input type="hidden" name="tryAnotherWay" value="on">'
                 '</form>')
        page = _Page(decoy + self.sign_in, SIGN_IN_URL)

        form = self._pages().form_asking_for(page, 'password')
        self.assertEqual('kc-form-login', form['id'],
                         'the first form on the page was taken rather than '
                         'the one which asks for a password')

    def test_an_input_after_a_form_is_not_a_field_of_that_form(self):
        # An input outside any form but following one: a locale picker,
        # an anti-forgery field beside the form rather than in it. Both
        # providers' templates emit such things on some screens, and the
        # committed captures do not, so this is constructed. Attributing
        # it to the form above it is wrong in both directions -- it can
        # make a form appear to ask for a field it does not, and it can
        # make a page with exactly one password form read as two.
        stray = '<input type="hidden" name="password" value="decoy">'
        page = _Page(self.confirm + stray, CONFIRM_URL)

        forms = self._pages().forms_of(page)
        self.assertEqual(
            [['code']], [[f['name'] for f in form['fields']]
                         for form in forms],
            'an input after the form it follows was read as one of its '
            'fields, so which form asks for what is decided by position '
            'rather than by the markup')

        # And so the page still reads as a page carrying no password
        # form at all, rather than as one whose confirmation form asks
        # for a password.
        e = self.assertRaises(idp_pages.PageSequenceChanged,
                              self._pages().form_asking_for, page,
                              'password')
        self.assertIn('0 forms carrying a field', str(e))

    def test_a_page_cut_off_mid_form_is_refused_and_not_half_posted(self):
        # A page truncated part way through a tag -- a short read, a
        # provider which closed the connection -- loses that tag
        # entirely. close() says the document has ended but does not
        # recover an unterminated one, which is measured here rather
        # than assumed, because the thing worth pinning is what the
        # caller does about it: the form comes back a field short, and
        # the field it is short of is the one it is selected by, so the
        # page is refused by name. The failure mode this rules out is
        # posting a sign in form with no password in it and reading the
        # provider's complaint as a refused credential.
        page = _Page('<form id="kc-form-login"><input name="username">'
                     '<input name="password"', SIGN_IN_URL)

        self.assertEqual(
            [['username']],
            [[f['name'] for f in form['fields']]
             for form in self._pages().forms_of(page)])

        e = self.assertRaises(idp_pages.PageSequenceChanged,
                              self._pages().form_asking_for, page,
                              'password')
        self.assertIn('0 forms carrying a field', str(e))
        self.assertIn("'username'", str(e),
                      'the refusal does not say what the truncated page did '
                      'carry, which is what tells a reader it was cut off '
                      'rather than changed')

    def test_the_confirmation_form_is_the_one_carrying_the_grant_code(self):
        page = _Page(self.confirm, CONFIRM_URL)

        form = self._pages().form_asking_for(page, 'code')
        self.assertEqual([{'name': 'code', 'value': GRANT_CODE}],
                         form['fields'])

    def test_a_page_this_was_not_written_against_says_what_it_saw(self):
        page = _Page(self.confirm, CONFIRM_URL)

        e = self.assertRaises(idp_pages.PageSequenceChanged,
                              self._pages().form_asking_for, page, 'password')
        message = str(e)
        self.assertIn(CONFIRM_URL, message,
                      'the refusal does not name the page it read')
        self.assertIn('0 forms carrying a field', message)
        self.assertIn("'code'", message,
                      'the refusal does not say which fields it did see, '
                      'which is the whole of what makes it actionable')
        self.assertIn('Read the new pages', message)

    def test_two_of_the_same_form_is_also_a_page_nobody_has_read(self):
        page = _Page(self.confirm + self.confirm, CONFIRM_URL)

        e = self.assertRaises(idp_pages.PageSequenceChanged,
                              self._pages().form_asking_for, page, 'code')
        self.assertIn('2 forms carrying a field', str(e))

    # -- sending one back ----------------------------------------------

    def test_a_relative_action_is_resolved_against_the_page_it_came_from(self):
        # The confirmation form's action is relative and the sign in
        # form's is absolute, so a parser which stopped resolving would
        # still complete a sign in and then post the confirmation to a
        # URL with no host at all.
        pages = self._pages(_Page('', DONE_URL))
        page = _Page(self.confirm, CONFIRM_URL)

        pages.submit(page, pages.form_asking_for(page, 'code'),
                     {'accept': ''})
        sent = pages.session.requests[-1]
        self.assertEqual(
            'https://localhost:8443/realms/shakenfist/login-actions/consent'
            '?client_id=shakenfist&tab_id=TAB-SCRUBBED&client_data=e30',
            sent['url'],
            'the relative action was not resolved against the page it came '
            'from')

    def test_an_absolute_action_is_left_as_it_stands(self):
        pages = self._pages(_Page('', CONFIRM_URL))
        page = _Page(self.sign_in, SIGN_IN_URL)
        form = pages.form_asking_for(page, 'password')

        pages.submit(page, form, {'username': 'person', 'password': 'secret'})
        self.assertEqual(form['action'], pages.session.requests[-1]['url'])

    def test_what_the_page_carried_goes_back_and_the_buttons_do_not(self):
        pages = self._pages(_Page('', DONE_URL))
        page = _Page(self.confirm, CONFIRM_URL)

        pages.submit(page, pages.form_asking_for(page, 'code'),
                     {'accept': ''})
        self.assertEqual({'code': GRANT_CODE, 'accept': ''},
                         pages.session.requests[-1]['data'],
                         'the hidden field the provider threaded through this '
                         'page did not go back as it stands, or the form\'s '
                         'own cancel button was sent with its accept')

    def test_an_empty_input_is_sent_as_empty_rather_than_dropped(self):
        pages = self._pages(_Page('', CONFIRM_URL))
        page = _Page(self.sign_in, SIGN_IN_URL)

        pages.submit(page, pages.form_asking_for(page, 'password'),
                     {'username': 'person', 'password': 'secret'})
        self.assertEqual({'username': 'person', 'password': 'secret',
                          'credentialId': ''},
                         pages.session.requests[-1]['data'])

    # -- the whole of the person's half --------------------------------

    def test_the_approval_is_three_requests_and_reports_each_page(self):
        pages = self._pages(_Page(self.sign_in, SIGN_IN_URL),
                            _Page(self.confirm, CONFIRM_URL),
                            _Page('<html>done</html>', DONE_URL))

        report = pages.approve(
            'https://localhost:8443/realms/shakenfist/device?'
            'user_code=WDJB-MJHT', 'WDJB-MJHT', 'person', 'secret')

        self.assertTrue(report['approved'])
        self.assertEqual(['sign in', 'confirm', 'approved'],
                         [page[0] for page in report['pages']])
        self.assertEqual(['GET', 'POST', 'POST'],
                         [r['method'] for r in pages.session.requests])
        self.assertEqual('person',
                         pages.session.requests[1]['data']['username'])
        self.assertEqual('', pages.session.requests[2]['data']['accept'])
        self.assertNotIn('added_the_code_to_the_uri', report)

    def test_a_verification_uri_without_the_code_has_it_added(self):
        pages = self._pages(_Page(self.sign_in, SIGN_IN_URL),
                            _Page(self.confirm, CONFIRM_URL),
                            _Page('<html>done</html>', DONE_URL))

        report = pages.approve(
            'https://localhost:8443/realms/shakenfist/device',
            'WDJB-MJHT', 'person', 'secret')

        self.assertTrue(report['added_the_code_to_the_uri'])
        self.assertEqual(
            'https://localhost:8443/realms/shakenfist/device?'
            'user_code=WDJB-MJHT',
            pages.session.requests[0]['url'])

    def test_a_refused_sign_in_is_reported_as_a_refused_password(self):
        # The provider answers a wrong password with the sign in form
        # again rather than with a status code, and that has to read as
        # a password which is wrong rather than as a page which changed.
        pages = self._pages(_Page(self.sign_in, SIGN_IN_URL),
                            _Page(self.sign_in, SIGN_IN_URL))

        e = self.assertRaises(
            idp_pages.PageSequenceChanged, pages.approve,
            'https://localhost:8443/realms/shakenfist/device?'
            'user_code=WDJB-MJHT', 'WDJB-MJHT', 'person', 'wrong')
        self.assertIn('did not accept person\'s password', str(e))

    def test_an_unconfirmed_confirmation_is_not_reported_as_approved(self):
        pages = self._pages(_Page(self.sign_in, SIGN_IN_URL),
                            _Page(self.confirm, CONFIRM_URL),
                            _Page(self.confirm, CONFIRM_URL))

        e = self.assertRaises(
            idp_pages.PageSequenceChanged, pages.approve,
            'https://localhost:8443/realms/shakenfist/device?'
            'user_code=WDJB-MJHT', 'WDJB-MJHT', 'person', 'secret')
        self.assertIn('was not confirmed', str(e))

    def test_a_refusal_from_the_provider_is_not_reported_as_approved(self):
        pages = self._pages(_Page(self.sign_in, SIGN_IN_URL),
                            _Page(self.confirm, CONFIRM_URL),
                            _Page('<html>no</html>', DONE_URL, 400))

        e = self.assertRaises(
            idp_pages.PageSequenceChanged, pages.approve,
            'https://localhost:8443/realms/shakenfist/device?'
            'user_code=WDJB-MJHT', 'WDJB-MJHT', 'person', 'secret')
        self.assertIn('HTTP 400', str(e))
