# What are the components of a Shaken Fist cluster?

Shaken Fist is composed of a series of components. There is Shaken Fist itself, which provides the orchestration and APIs to handle compute and virtual networks. The majority of this website discusses Shaken Fist, and if it is not specified, then you should assume that Shaken Fist is the component providing functionality.

* [The Derek Zoolander Centre for Kids Who Want To Cloud Good](/components/cloudgood/) is essentially a set of working notes on cloud and distributed compute topics that I think are of interest or are important to succeed as a cloud engineer while also explaining my thinking in building out Shaken Fist. They've been collected as a response to the questions I receive from time to time, and come from a place of trying to be helpful. It is completely fine to pick and choose which parts of all this you read as your interests guide you.

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