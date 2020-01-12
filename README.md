Shaken Fist: Opinionated to the point of being impolite
=======================================================

What is this?
-------------

Shaken Fist is a deliberately minimal cloud. Its also currently incomplete, so take statements here with a grain of salt. Shaken Fist came about as a reaction to the increasing complexity of OpenStack. What I really wanted was a simple API to orchestrate virtual machines, but it needed to run with minimal resource overhead and be simple to deploy. I also wanted it to always work in a predictable way.

One of the reasons OpenStack is so complicated and its behaviour varies is because it has many options to configure. The solution seemed obvious to me -- a cloud that is super opinionated. For each different functional requirement there is one option, and the simplest option is chosen where possible. Read on for some examples.

Deployment choices
------------------

libvirt is the only supported hypervisor. Instances are specified to libvirt with simple templated XML. If your local requirements are different to what's in the template, you're welcome to change the template to meet your needs.

Instances
---------

Every instance gets a config drive. Its always an ISO9660 drive. There is no metadata server. Additionally, there is no image service -- you specify the image to use by providing a URL. That URL is cached, but can be to any HTTP server anywhere. Even better, there are no flavors. You specify what resources your instance should have at boot time and that's what you get. No more being forced into a tshirt sized description of your needs.

Instances are always cattle. Any feature that made instances feel like pets has not been implemented.

Networking
----------

Virtual networks / micro segmentation is provided by VXLAN meshes betwen the instances. Every hypervisor node is joined to every mesh. DHCP services are optionally offered from a "network services" node, which is just a hypervisor node with some extra Docker containers.


Installation
============

Do the following:

* Debian (production): sudo apt-get install python3-dev default-libmysqlclient-dev

* install docker on each hypervisor node.

* install mysql somewhere, and then provide that as a sqlalchemy connection URL in CONFIG.SQL_URL.

* python3 -m venv ~/virtualenvs/shakenfist
* . ~/virtualenvs/shakenfist/bin/activate
* python3 setup.py develop

* update the database by running ```alembic upgrade head``` from the shakenfist directory.