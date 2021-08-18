from shakenfist.external_api import base as api_base
from shakenfist import db


def metadata_putpost(meta_type, owner, key, value):
    if meta_type not in ['namespace', 'instance', 'network']:
        return api_base.error(500, 'invalid meta_type %s' % meta_type)
    if not key:
        return api_base.error(400, 'no key specified')
    if not value:
        return api_base.error(400, 'no value specified')

    with db.get_lock('metadata', meta_type, owner,
                     op='Metadata update'):
        md = db.get_metadata(meta_type, owner)
        if md is None:
            md = {}
        md[key] = value
        db.persist_metadata(meta_type, owner, md)
