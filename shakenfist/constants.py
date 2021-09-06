# Sometimes we hold a lock for a long time and need to refresh it. This
# is how often we do that refresh.
LOCK_REFRESH_SECONDS = 5


# How long we wait to acquire an etcd lock by default.
ETCD_ATTEMPT_TIMEOUT = 60
