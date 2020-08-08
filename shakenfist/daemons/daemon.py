import setproctitle

from shakenfist import util


DAEMON_NAMES = {
    'API': 'sf-api',
    'CLEANER': 'sf-cleaner',
    'MAIN': 'sf-main',
    'NET': 'sf-net',
    'RESOURCES': 'sf-resources',
    'TRIGGERS': 'sf-triggers',
}


def process_name(id):
    if id not in DAEMON_NAMES:
        raise Exception('Code Error: Bad process name: %s' % id)
    return DAEMON_NAMES[id]


class Daemon(object):
    def __init__(self, id):
        setproctitle.setproctitle(process_name(id))
        self.log, self.handler = util.setup_logging(id)
