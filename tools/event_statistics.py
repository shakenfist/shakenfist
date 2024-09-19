# Emit statistics about how many events we create for CI runs.
import os
from collections import defaultdict

from shakenfist import eventlog
from shakenfist.config import config


if __name__ == '__main__':
    event_path = os.path.join(config.STORAGE_PATH, 'events')
    total_events = 0

    objtypes = []
    for ent in os.listdir(event_path):
        if ent.startswith('_'):
            continue
        objtypes.append(ent)

    for objtype in objtypes:
        event_count = 0
        by_message = defaultdict(int)

        for root, dirs, files in os.walk(os.path.join(event_path, objtype)):
            for file in files:
                el = eventlog.EventLog(objtype, file.split('.')[0])
                for event in el.read_events(limit=-1):
                    event_count += 1
                    by_message[event['message']] += 1

        print('Object type %s has %d events' % (objtype, event_count))
        for key, value in sorted(by_message.items(), key=lambda kv: kv[1],
                                 reverse=True)[:10]:
            print('    %s ... %d' % (key, value))
        print()
        total_events += event_count

    print('There were %d events in total' % total_events)
