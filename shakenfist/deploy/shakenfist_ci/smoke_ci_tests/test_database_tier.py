# Copyright 2019 Michael Still and contributors
from shakenfist_ci import base
from shakenfist_ci import database_tier


class TestDatabaseTier(database_tier.DatabaseTierTestsMixin,
                       base.BaseNamespacedTestCase):
    """The database tier assertions which need only one sf-database.

    These are in the smoke suite because they guard a failure mode the
    single-node topology is the most exposed to -- the API and
    sf-database share a machine there, so the direct-MariaDB config is
    visible to the API process -- and because the cluster suite runs
    for the first time in the merge queue, which is too late to learn
    that a database tier assertion is broken.

    The test bodies live in DatabaseTierTestsMixin; the cluster suite
    subclasses it too, so the multi-node topologies keep their coverage.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'dbtier'
        super().__init__(*args, **kwargs)
