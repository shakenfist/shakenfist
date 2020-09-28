---
title: Installation
---
# Installing Shaken Fist

This guide will assume that you want to install Shaken Fist on a single local machine (that is, the one you're going to run ansible on). This is by no means the only installation option, but is the most simple to get people started. There is additional detail in the README.md file of the deploy repository if you need more help.

Shaken Fist only supports Ubuntu 18.04 or later, so if you're running on localhost that implies that you must be running a recent Ubuntu on your development machine. Note as well that the deployer installs software and changes the configuration of your networking, so be careful when running it on machines you are fond of.

Create a directory for Shaken Fist, and then checkout the deployer git repository there:

```bash
mkdir shakenfist
cd shakenfist
git clone https://github.com/shakenfist/deploy
cd deploy/ansible
```

And install some dependancies:

```bash
sudo apt-get update
sudo apt-get -y dist-upgrade
sudo apt-get -y install ansible tox pwgen build-essential python3-dev python3-wheel curl
ansible-galaxy install andrewrothstein.etcd-cluster andrewrothstein.terraform andrewrothstein.go
```

## Local Development

Shaken Fist uses ansible as its installer, with terraform to bring up cloud resources. Because we're going to install Shaken Fist on localhost, there isn't much terraform in this example.Installation is run by a simple wrapper called "deployandtest.sh". This wrapper checks out required git repositories, runs ansible plays, and then performs CI testing of the installation.
!!! warning
    CI testing is currently destructive (don't run it against production!), and not supported for installs with fewer than three machines. It is skipped for a localhost install.

We also make the assumption that developer laptops move around more than servers. In a traditional install we detect the primary NIC of the machine and then use that to build VXLAN meshes. For localhost single node deploys we instead create a bridge called "brsf" and then use that as our primary NIC. This means your machine can move around and have different routes to the internet over time, but it also means its fiddly to convert a localhost install into a real production cluster. Please only use localhost installs for development purposes.

```bash 
CLOUD=localhost ./deployandtest.sh
```

<aside>The deployer clones a number of git repositories that it needs to build a working Shaken Fist installation. As a developer, you might want to move these out of shakenfist/deploy/gitrepos to somewhere more obvious once the installer has finished running. You can just symlink the repositories to the location that the deployer users and things will work as expected. Note that the deloyer does not clone all repositories, just those it needs, so you might still need to clone other repositories.</aside>

If you want to install a specific release, you can set the RELEASE environment variable. Possible options are:

* Any [valid pypi release](https://pypi.org/project/shakenfist/#history) version number.
* "git:master" for the current master branch of each repository.
* "git:branch" for a specific branch. If that branch does not exist in a given repository, master is used instead.

## Your first instance

Before you can start your first instance you'll need to authenticate to Shaken Fist, and create a network. Shaken Fist's python api client (as used by the command line client) looks for authentication details in the following locations:

* Command line flags
* Environment variables (prefixed with **SHAKENFIST_**)
* **~/.shakenfist**, a JSON formatted configuration file
* **/etc/sf/shakenfist.json**, the same file as above, but global

By default the installer creates **/etc/sf/sfrc**, which sets the required environment variables to authenticate.  
It is customized per installation, setting the following variables:

* **SHAKENFIST_NAMESPACE**, the namespace to create resources in
* **SHAKENFIST_KEY**, an authentication key for that namespace
* **SHAKENFIST_API_URL**, a URL to the Shaken Fist API server

Before interacting with Shaken Fist, we need to source the rc file.  

```bash
. /etc/sf/sfrc
```

Instances must be launched attached to a network.

Create your first network:
```bash
sf-client network create 192.168.42.0/24 mynet
```

You can get help for the command line client by running ```sf-client --help``. The above command creates a new network called "mynet", with the IP block 192.168.42.0/24. You will receive some descriptive output back:

```bash
$ sf-client network create 192.168.42.0/24 mynet
uuid            : 16baa325-5adf-473f-8e7a-75710a822d45
name            : mynet
vxlan id        : 2
netblock        : 192.168.42.0/24
provide dhcp    : True
provide nat     : True
floating gateway: None
namespace       : system
state           : initial

Metadata:
```

The UUID is important, as that is how we will refer to the network elsewhere. Let's now create a simple first instance (you'll need to change this to use your actual network UUID):

```bash
$ sf-client instance create myvm 1 1024 -d 8@cirros -n 16baa325-5adf-473f-8e7a-75710a822d45
uuid        : c6c4ba94-ed34-497d-8964-c223489dee3e
name        : myvm
namespace   : system
cpus        : 1
memory      : 1024
disk spec   : type=disk   bus=None  size=8   base=cirros  
video       : model=cirrus  memory=16384  
node        : marvin
power state : on
state       : created
console port: 31839
vdi port    : 34442

ssh key     : None
user data   : None

Metadata:

Interfaces:

    uuid    : e56b3c7b-8056-4645-b5b5-1779721ff21d
    network : 16baa325-5adf-473f-8e7a-75710a822d45
    macaddr : ae:15:4d:9c:d8:c0
    order   : 0
    ipv4    : 192.168.42.76
    floating: None
    model   : virtio
```

Probably the easiest way to interact with this instance is to connect to its console port, which is the serial console of the instance over telnet. In the case above, that is available on port 31829 on localhost (my laptop is called marvin).