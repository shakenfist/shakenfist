# Documentation state:
#   - Has metadata calls:
#   - OpenAPI complete:
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:
import os
import uuid

import flask
from flasgger import swag_from
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.upload import Upload


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


upload_create_example = """{
    "uuid": "e0e0e655-072b-4395-9f64-98102b379ea9",
    "node": "sf-2",
    "created_at": 1669750946.1144981
}"""


class UploadCreateEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'upload', 'Create a new upload.', [],
        [(200, 'Information about the upload.', upload_create_example)]))
    @api_base.log_token_use
    def post(self):
        u = Upload.new(str(uuid.uuid4()), config.NODE_NAME)
        u.add_event(EVENT_TYPE_AUDIT, 'create request from REST API')
        return u.external_view()


class UploadDataEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'upload', 'Append data to an upload.',
        [('upload_uuid', 'query', 'uuid', 'The upload UUID.', True),
         ('binary data', 'body', 'binary', 'Binary data to append to the upload.', True)],
        [(200, 'The new size of the uploaded object.', '1500')]))
    @api_base.arg_is_upload_uuid
    @api_base.redirect_upload_request
    @api_base.log_token_use
    def post(self, upload_uuid=None, upload_from_db=None):
        # NOTE(mikal): deliberately not audit logged because of the volume of
        # events it would create.
        upload_dir = os.path.join(config.STORAGE_PATH, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        upload_path = os.path.join(upload_dir, str(upload_from_db.uuid))
        with open(upload_path, 'ab') as f:
            f.write(flask.request.get_data(cache=False, as_text=False,
                                           parse_form_data=False))

        st = os.stat(upload_path)
        return st.st_size


class UploadTruncateEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'upload', 'Truncate an upload object to a specified size.',
        [('upload_uuid', 'query', 'uuid', 'The upload UUID.', True),
         ('offset', 'query', 'integer', 'The new length of the object.', True)],
        [(200, 'No return value', ''),
         (400, 'The offset must be an integer within the upload.', None),
         (404, 'The upload has no data to truncate.', None)]))
    @api_base.arg_is_upload_uuid
    @api_base.redirect_upload_request
    @api_base.log_token_use
    def post(self, upload_uuid=None, offset=None, upload_from_db=None):
        # Logged before validation so that rejected attempts are
        # audited too, with each rejection recording why.
        upload_from_db.add_event(EVENT_TYPE_AUDIT, 'truncate request from REST API',
                                 extra={'offset': str(offset)})

        def _reject(status, message):
            # str() because offset is whatever the client sent, and an
            # infinity or a nested structure has to reach the event
            # row's JSON intact.
            upload_from_db.add_event(
                EVENT_TYPE_AUDIT, 'truncate request rejected',
                extra={'offset': str(offset), 'reason': message})
            return sf_api.error(status, message, suppress_traceback=True)

        truncate_to = api_base.coerce_int(offset)
        if truncate_to is None:
            return _reject(400, 'offset is not an integer')
        if truncate_to < 0:
            return _reject(400, 'offset must not be negative')

        upload_dir = os.path.join(config.STORAGE_PATH, 'uploads')
        upload_path = os.path.join(upload_dir, str(upload_from_db.uuid))

        try:
            current_size = os.stat(upload_path).st_size
        except FileNotFoundError:
            # Nothing sent yet, so os.truncate would raise
            # FileNotFoundError and become a 500. Truncating to zero is
            # a no-op rather than an error, so a client which resets
            # before writing does not have to special case it.
            if truncate_to == 0:
                return
            return _reject(404, 'upload has no data')

        # Bounded above too. os.truncate() past the end of the file
        # raises OverflowError beyond a C long and OSError(EFBIG)
        # beyond the filesystem maximum -- and between the two it
        # succeeds, growing the upload into a large sparse file.
        if truncate_to > current_size:
            return _reject(400, 'offset is beyond the end of the upload')

        # No makedirs: a successful stat proves the directory exists.
        os.truncate(upload_path, truncate_to)
