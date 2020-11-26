# CI Images

This page documents how the CI images for Shaken Fist are setup in our testing cloud account. At the moment Shaken Fist CI is not self hosting, and runs on Google Compute Engine. It would be good to change this in the future.

## The sf-privci image

Our test nodes are used for a single test each, and are based on the "sf-privci" series of images in the relevant Google Cloud project. These images need regular updates or the dist-upgrade time on startup gets out of hand. The process to do that is as follows:

Find the most recent image and boot a VM using it:

```
# IMAGE=`gcloud compute images list | grep sf-privci | cut -f 1 -d " " | sort -n | tail -1`
# gcloud compute instances create update-image \
    --zone us-central1-b --min-cpu-platform "Intel Haswell" --image $IMAGE
```

Wait for the instance to boot and then log into it:

```
# gcloud compute ssh update-image
```

And now you can do whatever updates are required. May I suggest:

```
# sudo apt-get update
# sudo apt-get dist-upgrade
# sudo sync
```

Now we can create a new image:

```
# gcloud compute instances stop update-image
# gcloud compute images create sf-privci-`date +%Y%m%d` \
    --source-disk=update-image --source-disk-zone=us-central1-b
# gcloud compute instances delete update-image
```

Finally, you need to update Jenkins to use that new disk image. Remember to occassionally clean up old images, but do that via the Google Cloud UI. That hides at https://jenkins.shakenfist.com/configureClouds/