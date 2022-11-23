# Development Workflow

### Short Lesson
The majority of teams using git have a work flow that looks similar to the four
well known work flows:

* [Git Flow](https://datasift.github.io/gitflow/IntroducingGitFlow.html)
* [GitHub Flow](https://guides.github.com/introduction/flow/)
* [GitLab Flow](https://about.gitlab.com/blog/2014/09/29/gitlab-flow/)
* [Trunk Based Development](https://trunkbaseddevelopment.com/)

## Git Development - the Shaken Fist Way

The Shaken Fist developers have chosen **Trunk Based Development** with some
minor tweaks.

## Branch Types

1. Branch `develop`
    - This is the development trunk and has largely replaced `master` in most of our respositories.
    - All `feature` branches are branched from `develop` and merged to `develop`.
    - New releases are cut from the `develop` branch when we decide its time to bump the major release number.
    - The `develop` branch has automated nightly CI tests, and failures create GitHub issues which must be regularly triaged. That is, CI failures on `develop` are exceptional and should not be accepted as flakey tests or "situation normal". CI failures are labelled as `ci-failure` in GitHub issues.

2. Feature branches
    - Short-lived, generally a few days although sometimes much longer for complicated things.
    - Normally only one developer.
    - When presented to the team, it is expected to pass the linter, unit tests, and CI tests.
    - It is normal that other team members suggest changes / improvements before merging.

3. Branches `vX.X-release`
    - Created from `develop` when a new major release is first cut. Patches and minor releases for that major version are then cut from this branch.
    - Commits to this branch are cherry-picks from `develop` except in exceptional circumstances (for example the code to be changed no longer existing on `develop`).
    - It is not expected that many commits are made to this branch.
    - If many commits are required to a release branch then this indicates the need for another release.
    - "Recent" release branches has automated nightly CI tests, and failures create GitHub issues which must be regularly triaged. That is, CI failures on recent releases are exceptional and should not be accepted as flakey tests or "situation normal". CI failures are labelled as `ci-failure` in GitHub issues. For now, this is treated as all releases from v0.6 onwards, although that will likely change at some point.


## Process

### Bug fix branches
* Bug fix branches have a prefix consisting of the GitHub issue number and the word "bug", for example "bug-XXX".
* You commit should include the text `Fixes #XXX` where XXX is the GitHub issue number for the bug. It is possible to fix more than one GitHub issue in a single commit.

### Feature branches
* Feature branches should be named "feature-branch-XXX" where XXX is a short description of the feature.
* The feature branch developers should squash commits to remove WIP commits before creating a Pull Request, but it is acceptable to have a series of incremental changes building up to a complete feature in the feature branch at merge time.
* It is preferable that each remaining commit passes unit testing and CI, but the final state that is merged _must_ pass unit tests and CI.

### Merging
* Commits are **not** squashed when merged to `develop`.
* Not squashing commits maintains history of multiple issues being solved.
* Pull Request related commits remain grouped and can be understood as a single merge

### Minimal backports

Only **necessary** bug fixes are cherry-picked from `master` to an existing release branch.

