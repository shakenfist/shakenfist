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

Build an acceptable hypervisor node, noting that only Debian is supported. On Google Cloud, this looks like this:

```bash
# Create an image with nested virt enabled (only once)
gcloud compute disks create disk1 --image-project debian-cloud \
    --image-family debian-9 --zone us-central1-b
gcloud compute images create nested-vm-image \
  --source-disk disk1 --source-disk-zone us-central1-b \
  --licenses "https://compute.googleapis.com/compute/v1/projects/vm-options/global/licenses/enable-vmx"

# Start our hypervisor node VM
gcloud compute instances create sf-1 --zone us-central1-b \
    --min-cpu-platform "Intel Haswell" --image nested-vm-image
```

Now on the hypervisor node:

```bash
sudo apt-get install ansible
git clone https://github.com/mikalstill/shakenfist
cd shakenfist
ansible-playbook -i ansible/hosts ansible/deploy.yml
```

Create the storage directory structure:

```bash
sudo mkdir -p /srv/shakenfist/mariadb
sudo cp templates/libvirt.debian.tmpl /srv/shakenfist/libvirt.tmpl
sudo cp templates/dhcp.tmpl /srv/shakenfist/
```

Run mysql in a docker container:

```bash
ROOT_PASSWORD=`pwgen -N 1`
DB_PASSWORD=`pwgen -N 1`
NODE_IP=`dig @resolver1.opendns.com ANY myip.opendns.com +short`

echo "export SHAKENFIST_NODE_IP=$NODE_IP" >> ~/sf.sh
echo "export SHAKENFIST_DB_ROOT_PASSWORD=$ROOT_PASSWORD" >> ~/sf.sh
echo "export SHAKENFIST_DB_PASSWORD=$DB_PASSWORD" >> ~/sf.sh
echo "export SHAKENFIST_SQL_URL=mysql://sf:$DB_PASSWORD@$NODE_IP/sf" >> ~/sf.sh
. ~/sf.sh

sudo docker run --name sfdb -d -e MYSQL_ROOT_PASSWORD="$ROOT_PASSWORD" \
    -e MYSQL_DATABASE=sf -e MYSQL_USER=sf -e MYSQL_PASSWORD="$DB_PASSWORD" \
    -v /srv/shakenfist/mysql:/var/lib/mysql -p 3306:3306 \
    mariadb
```

It may take a couple of minutes for the database to be ready for queries. You can confirm with `docker logs sfdb`. Then confirm you can acccess the database:

```bash
mysql -h $SHAKENFIST_NODE_IP -u sf --password=$SHAKENFIST_DB_PASSWORD sf -e "show tables;"
```

You should get back no output (including no error messages). Now upgrade the MySQL database to the latest schema version:

```bash
cd shakenfist
alembic upgrade head
```