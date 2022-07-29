This document describes the Shaken Fist branching model, which only really matters for developers.

All active development is done in the `develop` branch. This is where pull requests should target.
Once a pull request has been merged to develop, it might be backported to a release branch, but that
is optional and would depend on the particular situation.

For each major release there is a branch with `-releases` at the end of its name. At the time of
writing these are:

* `v0.5-releases`
* `v0.6-releases`

These release branches have the component versions for Shaken Fist packages pinned to their
corresponding release number. This is different from the `develop` branch, which has no component
pinning.

Thus, to the release process looks like this:

## Minor release

Run `./release.sh` from the relevant release branch.

## Major release

Branch off develop into a new release branch. Pin the component versions in `requirements.txt` and
`getsf`. Addittionally, use the clingwrap hashin output to lock all our dependancies to specific
versions. It would be good if this was done with hashes, but that is currently not possible. Then
run `./release.sh`.
