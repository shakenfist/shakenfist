# We maintain a small database of "local events" for the purpose of tracking
# how recently things like network interfaces changed -- this is necessary
# because linux can sometimes take a while to fully bring up an interface or
# whatever. It can also be helpful for debugging.

import json
import sqlite3
import threading

from shakenfist_utilities import logs                    # noreorder
from shakenfist_utilities.random import random_id        # noreorder


LOG, _ = logs.setup(__name__)
THREAD_LOCAL = threading.local()


def request_id():
    reqid = getattr(THREAD_LOCAL, 'privexec_request_id', None)
    if reqid:
        return reqid

    reqid = random_id()
    THREAD_LOCAL.privexec_request_id = reqid
    LOG.with_fields({
        'request_id': reqid
    }).debug('Allocated request id')
    return reqid


DBPATH = '/var/run/sf-localevents'
VERSION = 1
CREATE_EVENT_TABLE = [
    (
        'CREATE TABLE IF NOT EXISTS localevents('
        'timestamp real, primary_object_type text, primary_object_uuid text, '
        'message text, extra text, request_id text, correlation_id text);'
    ),
    'CREATE INDEX IF NOT EXISTS timestamp_idx ON localevents (timestamp);',
    (
        'CREATE INDEX IF NOT EXISTS primary_object_type_idx ON localevents '
        '(primary_object_type);'
    ),
    (
        'CREATE INDEX IF NOT EXISTS primary_object_uuid_idx ON localevents '
        '(primary_object_uuid);'
    ),
    'CREATE INDEX IF NOT EXISTS correlation_id_idx ON localevents (correlation_id);',
]
CREATE_VERSION_TABLE = """CREATE TABLE IF NOT EXISTS version(version int primary key)"""


class LocalEvents:
    def _make_connection(self):
        con = sqlite3.connect(DBPATH)
        con.row_factory = sqlite3.Row
        return con

    def __init__(self):
        con = self._make_connection()
        cur = con.cursor()

        cur.execute("SELECT count(name) FROM sqlite_master WHERE "
                    "type='table' AND name='version'")
        if cur.fetchone()['count(name)'] == 0:
            # We do not have a version table, skip to the latest version
            for statement in CREATE_EVENT_TABLE:
                con.execute(statement)
            con.execute(CREATE_VERSION_TABLE)
            con.execute('INSERT INTO version VALUES (?)', (VERSION, ))
            con.commit()

    def write_event(self, primary_object_type, primary_object_uuid,
                    message, correlation_id=None, extra=None):
        if extra:
            extra = json.dumps(extra, indent=4, sort_keys=True)
        con = self._make_connection()
        con.execute(
            'INSERT INTO localevents(timestamp, primary_object_type,'
            'primary_object_uuid, message, extra, request_id, correlation_id) '
            'VALUES (CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)',
            (
                primary_object_type, str(primary_object_uuid), message, extra,
                request_id(), correlation_id
            )
        )
        con.commit()


EVENT_DB = None
