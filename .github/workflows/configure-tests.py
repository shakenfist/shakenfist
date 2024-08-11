#!/usr/bin/python

import jinja2

JOBS = {
    'ci-images': [
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
            'name': 'rocky-9',
            'baseimage': 'rocky:9',
            'baseuser': 'cloud-user',
            'outputlabel': 'ci-images/rocky-9',
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
        {
            'name': 'rocky-9-test',
            'baseimage': 'rocky:9',
            'baseuser': 'cloud-user',
            'outputlabel': 'ci-images/rocky-9-test',
            'scheduled': False
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
