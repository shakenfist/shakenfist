Shaken Fist's release process
=============================

Shaken Fist is now split across a number of repositories to simplify development
and usage. Unfortunately, that complicated the release process. This page
documents the current release process although the reality is that only Michael
can do a release right now because of the requirement to sign releases with his
GPG key.

## Testing

We only release things which have passed CI testing, and preferably have had a
period running as the underlying cloud for the CI cluster as well. Sometimes in
an emergency we will bend the rules for a hotfix, but we should try and avoid
doing that.

## For reach repository to be released

Checkout the repository and ensure you're in the right branch. Then just run
`release.sh` and follow the bounching ball.