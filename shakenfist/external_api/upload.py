# Documentation state:
#   - Has metadata calls:
#   - OpenAPI complete:
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:

import flask
from flasgger import swag_from
import os
from shakenfist_utilities import api as sf_api, logs
import uuid

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.config import config
from shakenfist.upload import Upload


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


upload_create_example = """{
    "uuid": "e0e0e655-072b-4395-9f64-98102b379ea9",
    "node": "sf-2",
    "created_at": 1669750946.1144981
}"""


class UploadCreateEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'upload', 'Create a new upload.', [],
        [(200, 'Information about the upload.', upload_create_example)]))
    @api_base.verify_token
    @api_base.log_token_use
    def post(self):
        u = Upload.new(str(uuid.uuid4()), config.NODE_NAME)
        u.add_event(EVENT_TYPE_AUDIT, 'create request from REST API')
        return u.external_view()


class UploadDataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'upload', 'Append data to an upload.',
        [('upload_uuid', 'query', 'uuid', 'The upload UUID.', True),
         ('binary data', 'body', 'binary', 'Binary data to append to the upload.', True)],
        [(200, 'The new size of the uploaded object.', '1500')]))
    @api_base.verify_token
    @api_base.arg_is_upload_uuid
    @api_base.redirect_upload_request
    @api_base.log_token_use
    def post(self, upload_uuid=None, upload_from_db=None):
        # NOTE(mikal): deliberately not audit logged because of the volume of
        # events it would create.
        upload_dir = os.path.join(config.STORAGE_PATH, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        upload_path = os.path.join(upload_dir, upload_from_db.uuid)
        with open(upload_path, 'ab') as f:
            f.write(flask.request.get_data(cache=False, as_text=False,
                                           parse_form_data=False))

        st = os.stat(upload_path)
        return st.st_size


class UploadTruncateEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'upload', 'Truncate an upload object to a specified size.',
        [('upload_uuid', 'query', 'uuid', 'The upload UUID.', True),
         ('offset', 'query', 'integer', 'The new length of the object.', True)],
        [(200, 'No return value', '')]))
    @api_base.verify_token
    @api_base.arg_is_upload_uuid
    @api_base.redirect_upload_request
    @api_base.log_token_use
    def post(self, upload_uuid=None, offset=None, upload_from_db=None):
        upload_from_db.add_event(EVENT_TYPE_AUDIT, 'truncate request from REST API')
        upload_dir = os.path.join(config.STORAGE_PATH, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        upload_path = os.path.join(upload_dir, upload_from_db.uuid)
        os.truncate(upload_path, int(offset))
