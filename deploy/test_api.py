import requests
from shakenfist_ci import base


class TestOpenAPIJS(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'openapijs'
        super().__init__(*args, **kwargs)

    def test_ui_js(self):
        # Ensure we can fetch the UI Javascript
        r = requests.get(
            f'{self.test_client.base_url}/flasgger_static/swagger-ui.css')
        self.assertEqual(200, r.status_code)
