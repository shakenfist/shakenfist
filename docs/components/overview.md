# What are the components of a Shaken Fist cluster?

Shaken Fist is composed of a series of components. There is Shaken Fist itself,
which provides the orchestration and APIs to handle compute and virtual
networks. The majority of this website discusses Shaken Fist, and if it is
not specified, then you should assume that Shaken Fist is the component
providing functionality.

* [Clingwrap](/components/clingwrap/) is a tool for collecting debug dumps
from systems. This is useful for CI test environments, but also for customer
support.

* [Kerbside](/components/kerbside/) is a SPICE protocol native VDI proxy
responsible for providing rich VDI experiences to users of Shaken Fist
and OpenStack.

* [Occy Strap](/components/occystrap/) is a Docker/OCI container image
manipulation toolkit that allows you to work with container images without
requiring Docker to be installed. It follows a flexible input -> filter ->
output pipeline pattern for processing container images.