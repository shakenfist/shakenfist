#!/usr/bin/python

import jinja2

JOBS = {
    'ci-images': [
        {
            'name': 'debian-10',
            'baseimage': 'debian:10',
            'baseuser': 'debian',
            'outputlabel': 'ci-images/debian-10'
        },
        {
            'name': 'debian-11',
            'baseimage': 'debian:11',
            'baseuser': 'debian',
            'outputlabel': 'ci-images/debian-11'
        },
        {
            'name': 'ubuntu-2004',
            'baseimage': 'ubuntu:20.04',
            'baseuser': 'ubuntu',
            'outputlabel': 'ci-images/ubuntu-2004'
        },
    ],
    'functional-tests': [
        {
            'name': 'debian-10-localhost',
            'baseimage': 'sf://label/ci-images/debian-10',
            'baseuser': 'debian',
            'topology': 'localhost',
            'concurrency': 3
        },
        {
            'name': 'debian-11-slim-primary',
            'baseimage': 'sf://label/ci-images/debian-11',
            'baseuser': 'debian',
            'topology': 'slim-primary',
            'concurrency': 5
        },
        {
            'name': 'ubuntu-2004-slim-primary',
            'baseimage': 'sf://label/ci-images/ubuntu-2004',
            'baseuser': 'ubuntu',
            'topology': 'slim-primary',
            'concurrency': 5
        },
    ],
    'scheduled-tests': [
        {
            'name': 'develop-debian-10-localhost',
            'baseimage': 'sf://label/ci-images/debian-10',
            'baseuser': 'debian',
            'topology': 'localhost',
            'concurrency': 3,
            'branch': 'develop'
        },
        {
            'name': 'develop-debian-11-slim-primary',
            'baseimage': 'sf://label/ci-images/debian-11',
            'baseuser': 'debian',
            'topology': 'slim-primary',
            'concurrency': 5,
            'branch': 'develop'
        },
        {
            'name': 'develop-ubuntu-2004-slim-primary',
            'baseimage': 'sf://label/ci-images/ubuntu-2004',
            'baseuser': 'ubuntu',
            'topology': 'slim-primary',
            'concurrency': 5,
            'branch': 'develop'
        },
        {
            'name': 'v06-debian-10-localhost',
            'baseimage': 'sf://label/ci-images/debian-10',
            'baseuser': 'debian',
            'topology': 'localhost',
            'concurrency': 3,
            'branch': 'v0.6-releases'
        },
        {
            'name': 'v06-debian-11-slim-primary',
            'baseimage': 'sf://label/ci-images/debian-11',
            'baseuser': 'debian',
            'topology': 'slim-primary',
            'concurrency': 5,
            'branch': 'v0.6-releases'
        },
        {
            'name': 'v06-ubuntu-2004-slim-primary',
            'baseimage': 'sf://label/ci-images/ubuntu-2004',
            'baseuser': 'ubuntu',
            'topology': 'slim-primary',
            'concurrency': 5,
            'branch': 'v0.6-releases'
        },
        {
            'name': 'v06-released-debian-10-localhost',
            'baseimage': 'sf://label/ci-images/debian-10',
            'baseuser': 'debian',
            'topology': 'localhost-released',
            'concurrency': 3,
            'branch': 'v0.6-releases'
        },
        {
            'name': 'v06-released-debian-11-slim-primary',
            'baseimage': 'sf://label/ci-images/debian-11',
            'baseuser': 'debian',
            'topology': 'slim-primary-released',
            'concurrency': 5,
            'branch': 'v0.6-releases'
        },
        {
            'name': 'v06-released-ubuntu-2004-slim-primary',
            'baseimage': 'sf://label/ci-images/ubuntu-2004',
            'baseuser': 'ubuntu',
            'topology': 'slim-primary-released',
            'concurrency': 5,
            'branch': 'v0.6-releases'
        },
    ]
}


if __name__ == '__main__':
    for style in JOBS.keys():
        with open('%s.tmpl' % style) as f:
            t = jinja2.Template(f.read())

        for job in JOBS[style]:
            with open('%s-%s.yml' % (style, job['name']), 'w') as f:
                f.write(t.render(job))
