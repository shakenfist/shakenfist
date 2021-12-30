import json
import os
import sqlite3

from shakenfist.config import config


CREATE_TABLE = """CREATE TABLE IF NOT EXISTS events(
                  timestamp real PRIMARY KEY,
                  message text NOT NULL, extra text)"""


class EventLog(object):
    def __init__(self, objtype, objuuid):
        self.objtype = objtype
        self.objuuid = objuuid

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
        self.con = sqlite3.connect(self.dbpath)
        self.con.row_factory = sqlite3.Row
        self.con.execute(CREATE_TABLE)

    def write_event(self, timestamp, message, extra):
        if not self.con:
            dbdir = os.path.dirname(self.dbpath)
            os.makedirs(dbdir, exist_ok=True)
            self._bootstrap()

        self.con.execute('insert into events values (?, ?, ?)',
                         (timestamp, message,
                          json.dumps(extra, indent=4, sort_keys=True)))

    def read_events(self):
        if not self.con:
            return

        cur = self.con.cursor()
        cur.execute('select * from events order by timestamp asc;')
        for row in cur.fetchall():
            extra = {}
            if row['extra']:
                extra = json.loads(row['extra'])
            yield (row['timestamp'], row['message'], extra)

    def delete(self):
        if self.con:
            self.con.close()
            self.con = None
        if os.path.exists(self.dbpath):
            os.unlink(self.dbpath)
