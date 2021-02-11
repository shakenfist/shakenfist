# CI Images

This page documents how the CI images for Shaken Fist are setup in our testing cloud account. At the moment Shaken Fist CI is not self hosting, and runs on Google Compute Engine. It would be good to change this in the future.

## Creating a new CI image

First off, you need to create the base CI image for the Jenkins CI workers. We currently use Ubuntu 20.04 and Debian 10, and regularly update those (see below). Other distribtions require that you create an image, bless it for nested virtualization (https://cloud.google.com/compute/docs/instances/enable-nested-virtualization-vm-instances), and then install some dependancies:

```
sudo apt-get update
sudo apt-get -y dist-upgrade
sudo apt-get -y install openjdk-11-jdk git ansible tox pwgen build-essential \
    python3-dev python3-wheel python3-pip curl ansible git
ansible-galaxy install andrewrothstein.etcd-cluster andrewrothstein.terraform \
    andrewrothstein.go
```

## Keeping a CI image up to date

Our test nodes are used for a single test each, and are based on the "sf-privci" series of images in the relevant Google Cloud project. These images need regular updates or the dist-upgrade time on startup gets out of hand. The process to do that is as follows:

Find the most recent image and boot a VM using it:

```
DISTRO=debian-10
IMAGE=`gcloud compute images list | grep sf-$DISTRO | cut -f 1 -d " " | sort -n | tail -1`
gcloud compute instances create update-image \
    --zone us-central1-b --min-cpu-platform "Intel Haswell" --image $IMAGE
```

Wait for the instance to boot and then log into it:

```
# gcloud compute ssh update-image
```

And now you can do whatever updates are required. May I suggest:

```
sudo apt-get update
sudo apt-get dist-upgrade
sudo sync
```

Now we can create a new image:

```
gcloud compute instances stop update-image
gcloud compute images create sf-$DISTRO-`date +%Y%m%d` \
    --source-disk=update-image --source-disk-zone=us-central1-b
gcloud compute instances delete update-image
```

Finally, you need to update Jenkins to use that new disk image. That hides at https://jenkins.shakenfist.com/configureClouds/

Remember to occassionally clean up old images, but do that via the Google Cloud UI.