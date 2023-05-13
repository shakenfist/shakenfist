# Documentation state:
#   - Has metadata calls:
#   - OpenAPI complete:
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:

import flask
from flask_jwt_extended import get_jwt_identity
import os
import random
import requests
from shakenfist_utilities import api as sf_api
from webargs import fields
from webargs.flaskparser import use_kwargs

from shakenfist import baseobject
from shakenfist.blob import Blob, Blobs
from shakenfist.config import config
from shakenfist.external_api import base as api_base
from shakenfist.namespace import get_api_token
from shakenfist.util import general as util_general


def _read_file(filename, offset):
    with open(filename, 'rb') as f:
        f.seek(offset)
        d = f.read(8192)
        while d:
            yield d
            d = f.read(8192)


def _read_remote(target, blob_uuid, offset=0):
    api_token = get_api_token(
        'http://%s:%d' % (target, config.API_PORT),
        namespace=get_jwt_identity()[0])
    url = 'http://%s:%d/blobs/%s/data?offset=%d' % (
        target, config.API_PORT, blob_uuid, offset)

    r = requests.request('GET', url, stream=True,
                         headers={'Authorization': api_token,
                                  'User-Agent': util_general.get_user_agent()})
    for chunk in r.iter_content(chunk_size=8192):
        yield chunk


class BlobEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, blob_uuid=None):
        b = Blob.from_db(blob_uuid)
        if not b:
            return sf_api.error(404, 'blob not found')

        return b.external_view()


class BlobDataEndpoint(sf_api.Resource):
    # NOTE(mikal): note that arguments from URL routes (blob_uuid for example),
    # are not included in the webargs schema because webargs doesn't appear to
    # know how to find them.
    get_args = {
        'offset': fields.Int(missing=0)
    }

    @api_base.verify_token
    @use_kwargs(get_args, location='query')
    @api_base.log_token_use
    def get(self, blob_uuid=None, offset=0):
        # Ensure the blob exists
        b = Blob.from_db(blob_uuid)
        if not b:
            return sf_api.error(404, 'blob not found')

        # Fast path if we have the blob locally
        blob_path = Blob.filepath(blob_uuid)
        if os.path.exists(blob_path):
            return flask.Response(
                flask.stream_with_context(_read_file(blob_path, offset)),
                mimetype='text/plain', status=200)

        # Otherwise find a node which has the blob and proxy.
        locations = b.locations
        if not locations:
            return sf_api.error(404, 'blob missing')

        random.shuffle(locations)
        return flask.Response(flask.stream_with_context(
            _read_remote(locations[0], blob_uuid, offset=offset)),
            mimetype='text/plain', status=200)


class BlobsEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.log_token_use
    def get(self, node=None):
        retval = []

        for b in Blobs(filters=[baseobject.active_states_filter]):
            if node and node in b.locations:
                retval.append(b.external_view())
            else:
                retval.append(b.external_view())

        return retval


class BlobChecksumsEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, hash=None):
        if not hash:
            return sf_api.error(400, 'you must specify a hash')

        for b in Blobs(filters=[baseobject.active_states_filter]):
            if b.checksums.get('sha512') == hash:
                return b.external_view()

        return None
