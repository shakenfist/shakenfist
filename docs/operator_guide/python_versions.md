# Supported python versions

The versions of python we support are driven by the versions packaged in our
supported operating systems. For Shaken Fist itself, we support:

[//]: # (Note that if you change the list of supported operating systems you must also update installation.md in this directory)

* Ubuntu 20.04: 3.8
* Ubuntu 22.04: 3.10
* Debian 11: 3.9
* Debian 12: 3.11

[//]: # (Note that if you change this version, you must also update the minimum python version in setup.cfg)

We therefore support Python 3.8 and above for server side software.

For client side software we are significantly more liberal. At the moment we
build guest images for the following Linux distributions:

* CentOS 7: 3.6
* CentOS 8-stream: 3.9
* CentOS 9-stream: 3.11
* Debian 11: 3.9
* Debian 12: 3.11
* Fedora 38: 3.12
* Fedora 39: 3.12
* Ubuntu 20.04: 3.8
* Ubuntu 22.04: 3.10
* Ubuntu 24.04: 3.12

We therefore support Python 3.6 and above in client code such as the Shaken Fist
client and in-guest agent.