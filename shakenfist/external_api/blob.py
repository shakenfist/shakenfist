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
from flasgger import swag_from
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


def arg_is_blob_uuid(func):
    def wrapper(*args, **kwargs):
        if 'blob_uuid' in kwargs:
            kwargs['blob_from_db'] = Blob.from_db(kwargs['blob_uuid'])

        if not kwargs.get('blob_from_db'):
            return sf_api.error(404, 'blob not found')

        return func(*args, **kwargs)
    return wrapper


class BlobEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.log_token_use
    @arg_is_blob_uuid
    def get(self, blob_uuid=None, blob_from_db=None):
        return blob_from_db.external_view()


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
    @arg_is_blob_uuid
    def get(self, blob_uuid=None, offset=0, blob_from_db=None):
        # Fast path if we have the blob locally
        blob_path = Blob.filepath(blob_uuid)
        if os.path.exists(blob_path):
            return flask.Response(
                flask.stream_with_context(_read_file(blob_path, offset)),
                mimetype='text/plain', status=200)

        # Otherwise find a node which has the blob and proxy.
        locations = blob_from_db.locations
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


class BlobMetadatasEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Fetch metadata for a blob.',
        [
            ('blob_uuid', 'body', 'string', 'The blob to add a key to.', True)
        ],
        [(200, 'Blob metadata, if any.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_blob_uuid
    @api_base.log_token_use
    def get(self, blob_uuid=None, blob_from_db=None):
        return blob_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'blobs', 'Add metadata for a blob.',
        [
            ('blob_uuid', 'body', 'string', 'The blob to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_blob_uuid
    @api_base.log_token_use
    def post(self, blob_uuid=None, key=None, value=None, blob_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        blob_from_db.add_metadata_key(key, value)


class BlobMetadataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Update a metadata key for an blob.',
        [
            ('blob_uuid', 'body', 'string', 'The blob to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_blob_uuid
    @api_base.log_token_use
    def put(self, blob_uuid=None, key=None, value=None, blob_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        blob_from_db.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'blobs', 'Delete a metadata key for an blob.',
        [
            ('blob_uuid', 'body', 'string', 'The blob to remove a key from.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_blob_uuid
    @api_base.log_token_use
    def delete(self, blob_uuid=None, key=None, value=None, blob_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        blob_from_db.remove_metadata_key(key)
