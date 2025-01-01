# Threading

Shaken Fist adopted a threading model for concurrency in v0.8, instead of the
previous process-centric approach. This was mainly done as we moved to using
more and more gRPC APIs, and experienced issues with reliability with gRPC and
our forking heavy model.

At that time, logging was expanded to include the thread id of the relevant
thread. For processes with a single thread, this id will be the same as the
process id itself. For other threads, it is an effectively random number
*which the linux kernel reserves the right to recycle*. This can be annoying
when processing logs, but is outside our control. Unfortunately, Python thread
ids also do not map the the ones that Linux shows you in `ps` and `top`.