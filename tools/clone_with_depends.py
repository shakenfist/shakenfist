#!/usr/bin/python3

import git
import re
import os

# Clone all the required repositories, with handling for dependencies between
# them. This script assumes it is being called by a github action and that
# various environment variables are in fact set.
#
# To test outside github, create /srv/github/_work/shakenfist/shakenfist and then
# do this:
#     export GITHUB_WORKSPACE=/srv/github/_work/shakenfist/shakenfist
#     export SF_PRIMARY_REPO='shakenfist'
#     export SF_HEAD_SHA=e8f50179329b21741c190ba2c08acf46aa3fc721

REPOS = {
    'agent-python': {
        'github': 'https://github.com/shakenfist/agent-python'
    },
    'client-python': {
        'github': 'https://github.com/shakenfist/client-python'
    },
    'shakenfist': {
        'github': 'https://github.com/shakenfist/shakenfist'
    },
}


DEPENDS_RE = re.compile(
    'Depends on https://github.com/shakenfist/([^/]*)/(.*)')


def main():
    # Ensure we have a checkout of all repositories
    for repo in REPOS:
        repo_path = os.path.join(os.environ['GITHUB_WORKSPACE'], repo)
        if not os.path.exists(repo_path):
            repo = git.Repo.clone_from(REPOS[repo]['github'], repo_path)

    # Determine if the primary repository has any dependent PRs. We use formatted
    # comments in the git commit message like this:
    #     Depends on https://github.com/shakenfist/$project/
    primary_repo_path = os.path.join(
        os.environ['GITHUB_WORKSPACE'], os.environ['SF_PRIMARY_REPO'])
    primary_repo = git.Repo(primary_repo_path)

    primary_commit_sha = os.environ['SF_HEAD_SHA']
    primary_commit = primary_repo.commit(primary_commit_sha)

    for line in primary_commit.message.split('\n'):
        line = line.lstrip(' ')
        if line.startswith('Depends on '):
            m = DEPENDS_RE.match(line)
            if m:
                print('Depends on detected: %s' % line)
                dep_repo_name = m.group(1)
                dep_pr = m.group(2)

                dep_repo_path = os.path.join(
                    os.environ['GITHUB_WORKSPACE'], dep_repo_name)
                dep_git = git.Git(dep_repo_path)

                dep_git.execute(['git', 'fetch', 'origin',
                                 '%s/head:dependson' % dep_pr])
                dep_git.execute(['git', 'checkout', 'dependson'])


if __name__ == '__main__':
    main()
