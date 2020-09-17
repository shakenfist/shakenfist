import logging
import setproctitle

from shakenfist import config
from shakenfist import logutil


DAEMON_NAMES = {
    'api': 'sf-api',
    'cleaner': 'sf-cleaner',
    'main': 'sf-main',
    'net': 'sf-net',
    'queues': 'sf-queues',
    'resources': 'sf-resources',
    'triggers': 'sf-triggers',
}


def process_name(name):
    if name not in DAEMON_NAMES:
        raise Exception('Code Error: Bad process name: %s' % name)
    return DAEMON_NAMES[name]


def set_log_level(log, name):
    # Check that id is a valid name
    process_name(name)

    # Check for configuration override
    label = 'LOGLEVEL_' + name.upper()
    level = config.parsed.get(label)
    if level:
        numeric_level = getattr(logging, level.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError('Invalid log level: %s' % level)
    else:
        numeric_level = logging.INFO

    log.setLevel(numeric_level)


class Daemon(object):
    def __init__(self, name):
        setproctitle.setproctitle(process_name(name))
        log, _ = logutil.setup(name)
        set_log_level(log, name)
