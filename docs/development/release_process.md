Shaken Fist's release process
=============================

Shaken Fist is now split across a number of repositories to simplify development and usage.
Unfortunately, that complicated the release process. This page documents the current release
process.

## Step 1: Testing

Before release, you should ensure that all of the clouds currently supported in the deploy
repository work. These are important to new users and testers, so we should keep them working.
For reference, they are at ```deploy/ansible/terraform```. At the time of writing those are:

* aws
* aws-single node
* gcp
* metal
* openstack

Note that to test these you need access to all those clouds, as well as needing to know the
cloud specific values for each cloud. Unfortunately I can't tell you those because they vary
with your specific cloud accounts.

## Step 2: ```shakenfist/shakenfist```

Test that pypi will accept Markdown formatted README.

In the shakenfist/shakenfist repo:

```
pip install --upgrade readme-renderer
pip install --upgrade twine
rm -f dist/*
git tag -s v0.1 -m "Release v0.1"
python3 setup.py sdist bdist_wheel
twine check dist/*
git push origin v0.1
twine upload dist/*
```

Create the branch for cherry picks and minor releases:

```
git checkout -b v0.1-devel
git push origin v0.1-devel
```

## Step 3: ```shakenfist/client-python```

Test that pypi will accept Markdown formatted README.

In the shakenfist/client-python repo:

```
pip install --upgrade readme-renderer
pip install --upgrade twine
rm -f dist/*
git tag -s v0.1 -m "Release v0.1"
python3 setup.py sdist bdist_wheel
twine check dist/*
git push origin v0.1
twine upload dist/*
```

## Step 4: ```shakenfist/client-go```
Tag and release as per shakenfist/shakenfist - include the patch number eg. v0.1.0

Golang modules require an "annotated git tag" (not a lightweight git tag). Therefore use the sign option (```-s```) as above, or use the annotate option (```-a```) of ```git tag```.

<b>Note</b> that the Github website interface will create lightweight tags. Therefore tag locally and push to Github.

<b>IMPORTANT:</b> Golang modules require the full X.Y.Z sermver version eg. v0.2.0 (Otherwise go will attach the wrong version numbers to fetches and update go.mod incorrectly.)

## Step 5: ```shakenfist/deploy```

Tag and release as per shakenfist/shakenfist.

## Step 6: ```shakenfist/terraform-provider-shakenfist```

* Bump dependency in go.mod
* Tag and release

Set the version number of github.com/shakenfist/client-go in the go.mod file to the new release version. Then tag and release as per shakenfist/shakenfist.
