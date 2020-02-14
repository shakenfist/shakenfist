# Helpers to resolve images when we don't have an image service

import logging
import re
import urllib.request


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


def resolve_image(name):
    if name.startswith('cirros'):
        return _resolve_cirros(name)
    if name.startswith('ubuntu'):
        return _resolve_ubuntu(name)
    return name


def _resolve_cirros(name):
    req = urllib.request.Request(
        'http://download.cirros-cloud.net/', method='GET')
    resp = urllib.request.urlopen(req)

    if name == 'cirros':
        versions = []
        dir_re = re.compile('.*<a href="([0-9]+\.[0-9]+\.[0-9]+)/">.*/</a>.*')
        for line in resp.read().decode('utf-8').split('\n'):
            m = dir_re.match(line)
            if m:
                versions.append(m.group(1))
        LOG.info('Found cirros versions: %s' % versions)
        ver = versions[-1]
    else:
        # Name is assumed to be in the form cirros-0.4.0
        _, ver = name.split('-')

    return ('http://download.cirros-cloud.net/%(ver)s/cirros-%(ver)s-x86_64-disk.img'
            % {'ver': ver})


def _resolve_ubuntu(name):
    req = urllib.request.Request(
        'https://cloud-images.ubuntu.com', method='GET')
    resp = urllib.request.urlopen(req)

    versions = {}
    dir_re = re.compile(
        '.*<a href="(.*)/">.*Ubuntu Server ([0-9]+\.[0-9]+).*')
    for line in resp.read().decode('utf-8').split('\n'):
        m = dir_re.match(line)
        if m:
            versions[m.group(2)] = m.group(1)
    LOG.info('Found ubuntu versions: %s' % versions)
    print(versions)

    if name == 'ubuntu':
        ver = sorted(versions.keys())[-1]
    else:
        # Name is assumed to be in the form ubuntu-xenial or ubuntu-19.04
        _, req = name.split('-')
        ver = versions.get(req, req)

    return ('https://cloud-images.ubuntu.com/%(ver)s/current/%(ver)s-server-cloudimg-amd64.img'
            % {'ver': ver})
