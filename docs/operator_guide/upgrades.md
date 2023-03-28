# Upgrades

Shaken Fist supports online upgrades natively -- when an object is read from
etcd that is an old version, the object is upgraded silently to the newest
version. If all nodes in your cluster are running a version of Shaken Fist
which supports this newest version, the upgraded object is then written back
to etcd. If not all nodes in the cluster support the new version, the new
version is simply used in memory by the node which did the upgrade. This means
it is safe to perform a rollout across a cluster without downtime, although
you might see small transient failures such as single API requests failing
as processes restart.

You should note however that "all nodes" includes nodes in non-running states
such as ERROR and MISSING. The only state which is excluded from the check is
DELETED. Therefore, in order for online upgrades to work correctly, it is
important that you delete nodes in an ERROR or MISSING state that you are
confident will not return to the cluster. This is because nodes can return
from ERROR or MISSING at the end of planned maintenance, and might be running
and older version of Shaken Fist upon their return than other members of the
cluster.

## Upgrade process

First off, upgrade the python packages in each node's virtualenv manually. This
is explicitly a non-goal of our installer tooling as we believe different
deployments will have different strategies for performing this step. Naively,
a good first approach is simple to run this command on each node:

```
sudo /srv/shakenfist/venv/bin/pip install --upgrade shakenfist
```

Which will pull in all the relevant other python packages it requires.

Then simply re-run `getsf` as you did when you first installed and the cluster
will upgrade.