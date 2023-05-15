# Instances (/instances/)

Note that the amount of information visible in an instance response will change
over the lifecycle of the instance -- for example when you first request the
instance be created versus when the instance has had its disk specification
calculated.

GET instance

import json
from shakenfist_client import apiclient

sf_client = apiclient.Client()
i = sf_client.get_instance('317e9b70-8e26-46af-a1c4-76931c0da5a9')
print(json.dumps(i, indent=4, sort_keys=True))



CREATE and then DELETE instance

import json
from shakenfist_client import apiclient
import time

sf_client = apiclient.Client()
i = sf_client.create_instance(
    'example', 1, 1024, None,
    [{
        'size': 20,
        'base': 'debian:11',
        'bus': None,
        'type': 'disk'
    }],
    None, None)

time.sleep(30)

i = sf_client.delete_instance(i['uuid'])



GET all instances

import json
from shakenfist_client import apiclient

sf_client = apiclient.Client()
instances = sf_client.get_instances()
print(json.dumps(instances, indent=4, sort_keys=True))