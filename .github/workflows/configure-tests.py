#!/usr/bin/python

import jinja2

JOBS = {
    'ci-images': [
        {
            'name': 'debian-10',
            'baseimage': 'debian:10',
            'baseuser': 'debian',
            'outputlabel': 'ci-images/debian-10',
            'scheduled': True
        },
        {
            'name': 'debian-11',
            'baseimage': 'debian:11',
            'baseuser': 'debian',
            'outputlabel': 'ci-images/debian-11',
            'scheduled': True
        },
        {
            'name': 'debian-12',
            'baseimage': 'debian:12',
            'baseuser': 'debian',
            'outputlabel': 'ci-images/debian-12',
            'scheduled': True
        },
        {
            'name': 'ubuntu-2004',
            'baseimage': 'ubuntu:20.04',
            'baseuser': 'ubuntu',
            'outputlabel': 'ci-images/ubuntu-2004',
            'scheduled': True
        },
        {
            'name': 'ubuntu-2204',
            'baseimage': 'ubuntu:22.04',
            'baseuser': 'ubuntu',
            'outputlabel': 'ci-images/ubuntu-2204',
            'scheduled': True
        },
        {
            'name': 'debian-10-test',
            'baseimage': 'debian:10',
            'baseuser': 'debian',
            'outputlabel': 'ci-images/debian-10-test',
            'scheduled': False
        },
        {
            'name': 'debian-11-test',
            'baseimage': 'debian:11',
            'baseuser': 'debian',
            'outputlabel': 'ci-images/debian-11-test',
            'scheduled': False
        },
        {
            'name': 'debian-12-test',
            'baseimage': 'debian:12',
            'baseuser': 'debian',
            'outputlabel': 'ci-images/debian-12-test',
            'scheduled': False
        },
        {
            'name': 'ubuntu-2004-test',
            'baseimage': 'ubuntu:20.04',
            'baseuser': 'ubuntu',
            'outputlabel': 'ci-images/ubuntu-2004-test',
            'scheduled': False
        },
        {
            'name': 'ubuntu-2204-test',
            'baseimage': 'ubuntu:22.04',
            'baseuser': 'ubuntu',
            'outputlabel': 'ci-images/ubuntu-2204-test',
            'scheduled': False
        },
    ],
    'functional-tests': [
        {
            'name': 'debian-11-localhost',
            'baseimage': 'sf://label/ci-images/debian-11',
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
            'name': 'debian-12-slim-primary',
            'baseimage': 'sf://label/ci-images/debian-12',
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
        {
            'name': 'ubuntu-2204-slim-primary',
            'baseimage': 'sf://label/ci-images/ubuntu-2204',
            'baseuser': 'ubuntu',
            'topology': 'slim-primary',
            'concurrency': 5
        },
    ],
    'scheduled-tests': [
        {
            'name': 'develop-debian-11-localhost',
            'baseimage': 'sf://label/ci-images/debian-11',
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
            'name': 'develop-debian-12-slim-primary',
            'baseimage': 'sf://label/ci-images/debian-12',
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
            'name': 'develop-ubuntu-2204-slim-primary',
            'baseimage': 'sf://label/ci-images/ubuntu-2204',
            'baseuser': 'ubuntu',
            'topology': 'slim-primary',
            'concurrency': 5,
            'branch': 'develop'
        },
        {
            'name': 'v07-debian-11-localhost',
            'baseimage': 'sf://label/ci-images/debian-11',
            'baseuser': 'debian',
            'topology': 'localhost',
            'concurrency': 3,
            'branch': 'v0.7-releases'
        },
        {
            'name': 'v07-debian-11-slim-primary',
            'baseimage': 'sf://label/ci-images/debian-11',
            'baseuser': 'debian',
            'topology': 'slim-primary',
            'concurrency': 5,
            'branch': 'v0.7-releases'
        },
        {
            'name': 'v07-ubuntu-2004-slim-primary',
            'baseimage': 'sf://label/ci-images/ubuntu-2004',
            'baseuser': 'ubuntu',
            'topology': 'slim-primary',
            'concurrency': 5,
            'branch': 'v0.7-releases'
        },
        {
            'name': 'v07-released-debian-11-localhost',
            'baseimage': 'sf://label/ci-images/debian-11',
            'baseuser': 'debian',
            'topology': 'localhost-released',
            'concurrency': 3,
            'branch': 'v0.7-releases'
        },
        {
            'name': 'v07-released-debian-11-slim-primary',
            'baseimage': 'sf://label/ci-images/debian-11',
            'baseuser': 'debian',
            'topology': 'slim-primary-released',
            'concurrency': 5,
            'branch': 'v0.7-releases'
        },
        {
            'name': 'v07-released-ubuntu-2004-slim-primary',
            'baseimage': 'sf://label/ci-images/ubuntu-2004',
            'baseuser': 'ubuntu',
            'topology': 'slim-primary-released',
            'concurrency': 5,
            'branch': 'v0.7-releases'
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
