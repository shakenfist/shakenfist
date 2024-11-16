# Emit statistics about how many events we create for CI runs.
from collections import defaultdict
import math
import os
import sys

from shakenfist import eventlog
from shakenfist.config import config


if __name__ == '__main__':
    failures = 0

    event_path = os.path.join(config.STORAGE_PATH, 'events')
    total_events = 0

    objtypes = []
    for ent in os.listdir(event_path):
        if ent.startswith('_'):
            continue
        objtypes.append(ent)

    instance_start_times = defaultdict(list)
    for objtype in objtypes:
        event_count = 0
        by_message = defaultdict(int)

        for root, dirs, files in os.walk(os.path.join(event_path, objtype)):
            for file in files:
                objuuid = file.split('.')[0]
                instance_object_created = 0
                instance_started = 0

                el = eventlog.EventLog(objtype, objuuid)
                for event in el.read_events(limit=-1):
                    event_count += 1
                    by_message[event['message']] += 1

                    if objtype == 'instance':
                        if event['message'] == 'db record created':
                            instance_object_created = event['timestamp']
                        if event['message'] == 'instance creation complete':
                            instance_started = event['timestamp']

                if (objtype == 'instance' and instance_object_created > 0 and
                        instance_started > 0):
                    # We round the duration to the nearest 30 seconds to bucketize
                    # results
                    duration = instance_started - instance_object_created
                    rounded = math.ceil(duration / 30) * 30
                    instance_start_times[rounded].append((objuuid, duration))

        print('Object type %s has %d events' % (objtype, event_count))
        if event_count > 200000:
            print('    ... which is more than the threshold of 200,000')
            failures += 1

        for key, value in sorted(by_message.items(), key=lambda kv: kv[1],
                                 reverse=True)[:10]:
            print('    %s ... %d' % (key, value))
        print()
        total_events += event_count

    print('There were %d events in total' % total_events)
    print()

    # Instance start times
    print('Instance start times')
    for start_time in sorted(instance_start_times.keys()):
        count = len(instance_start_times[start_time])
        print(f'    {start_time:08}: {count}')
        if start_time > 900:
            failures += count

            print('        ... which is slower than our threshold of 900 seconds')
            for inst, duration in instance_start_times[start_time]:
                print(f'        ... {inst} ({duration:.02} seconds)')
    print()

    sys.exit(failures)
