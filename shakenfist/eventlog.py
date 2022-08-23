import json
from oslo_concurrency import lockutils
import os
import sqlite3
import time

from shakenfist.config import config
from shakenfist import etcd
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


VERSION = 2
CREATE_EVENT_TABLE = """CREATE TABLE IF NOT EXISTS events(
    timestamp real PRIMARY KEY, fqdn text,
    operation text, phase text, duration float, message text,
    extra text)"""
CREATE_VERSION_TABLE = """CREATE TABLE IF NOT EXISTS version(version int primary key)"""


def add_event(object_type, object_uuid, operation, phase, duration, message,
              extra=None, suppress_event_logging=False):
    timestamp = time.time()

    if not suppress_event_logging:
        log = LOG
        if extra:
            log = log.with_fields(extra)
        log.with_fields(
            {
                object_type: object_uuid,
                'fqdn': config.NODE_NAME,
                'operation': operation,
                'phase': phase,
                'duration': duration,
                'message': message
            }).info('Added event')

    if config.NODE_MESH_IP == config.EVENTLOG_NODE_IP:
        with EventLog(object_type, object_uuid) as eventdb:
            eventdb.write_event(timestamp, config.NODE_NAME, operation, phase,
                                duration, message, extra=extra)
    else:
        # We use the old eventlog mechanism as a queueing system to get the logs
        # to the eventlog node.
        etcd.put('event/%s' % object_type, object_uuid, timestamp,
                 {
                     'timestamp': timestamp,
                     'object_type': object_type,
                     'object_uuid': object_uuid,
                     'fqdn': config.NODE_NAME,
                     'operation': operation,
                     'phase': phase,
                     'duration': duration,
                     'message': message,
                     'extra': extra
                 })


# Shim to track what hasn't been converted to the new style yet
def add_event2(object_type, object_uuid, message, duration=None, extra=None,
               suppress_event_logging=False):
    add_event(object_type, object_uuid, None, None, duration, message, extra=extra,
              suppress_event_logging=suppress_event_logging)


class EventLog(object):
    def __init__(self, objtype, objuuid):
        self.objtype = objtype
        self.objuuid = objuuid
        self.lock = lockutils.external_lock(
            '%s.lock' % self.objuuid,
            lock_path='/srv/shakenfist/events/%s' % self.objtype)

    def __enter__(self):
        self.dbpath = os.path.join(config.STORAGE_PATH, 'events', self.objtype,
                                   self.objuuid)
        if not os.path.exists(self.dbpath):
            self.con = None
        else:
            self._bootstrap()

        return self

    def __exit__(self, *args):
        if self.con:
            self.con.close()
            self.con = None

    def _bootstrap(self):
        with self.lock:
            if not os.path.exists(self.dbpath):
                LOG.with_field(self.objtype, self.objuuid).info(
                    'Creating event log')

            self.con = sqlite3.connect(self.dbpath)
            self.con.row_factory = sqlite3.Row
            cur = self.con.cursor()

            # Check if we already have a version table with data
            cur.execute("SELECT count(name) FROM sqlite_master WHERE "
                        "type='table' AND name='version'")
            if cur.fetchone()['count(name)'] == 0:
                # We do not have a version table, skip to the latest version
                self.con.execute(CREATE_EVENT_TABLE)
                self.con.execute(CREATE_VERSION_TABLE)
                self.con.execute('INSERT INTO version VALUES (?)', (VERSION, ))
                self.con.commit()

            else:
                # Upgrade
                cur.execute('SELECT * FROM version;')
                ver = cur.fetchone()['version']

                if ver == 1:
                    ver = 2
                    self.con.execute(
                        'ALTER TABLE events ADD COLUMN extra text')
                    self.con.execute(
                        'UPDATE version SET version = ?', (ver,))
                    self.con.commit()

    def write_event(self, timestamp, fqdn, operation, phase, duration, message,
                    extra=None):
        if not self.con:
            dbdir = os.path.dirname(self.dbpath)
            os.makedirs(dbdir, exist_ok=True)
            self._bootstrap()

        attempts = 0
        while attempts < 3:
            try:
                self.con.execute(
                    'INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (timestamp, fqdn, operation, phase, duration, message,
                     json.dumps(extra, cls=etcd.JSONEncoderCustomTypes)))
                self.con.commit()
                return
            except sqlite3.IntegrityError:
                timestamp += 0.00001
                attempts += 1

        LOG.with_fields({
            self.objtype: self.objuuid,
            'timestamp': timestamp,
            'fqdn': fqdn,
            'operation': operation,
            'phase': phase,
            'duration': duration,
            'message': message,
            'extra': extra
        }).error('Failed to record event after 3 retries')

    def read_events(self):
        if not self.con:
            return

        cur = self.con.cursor()
        cur.execute('SELECT * FROM events ORDER BY TIMESTAMP ASC;')
        for row in cur.fetchall():
            yield dict(row)

    def delete(self):
        if self.con:
            self.con.close()
            self.con = None
        if os.path.exists(self.dbpath):
            os.unlink(self.dbpath)

        LOG.with_field(self.objtype, self.objuuid).info('Removed event log')
