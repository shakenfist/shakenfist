So you want to run a CI test locally against your own cluster
=============================================================

Ensure you have a configuration at /etc/sf/shakenfist.json or ~/.shakenfist. You can't use environment variables because of annoying things tox does to them. The general format of the JSON file is:

```
{
    "namespace": "...",
    "key": "...",
    "apiurl": "..."
}
```

Then change into this directory if you haven't already. Then simply run:

```
SHAKENFIST_CI_CONCURRENCY=1 tox -epy3 shakenfist_ci.tests.test_disk_specs
```

Or whatever.