from shakenfist.image_resolver import centos
from shakenfist.image_resolver import cirros
from shakenfist.image_resolver import debian
from shakenfist.image_resolver import ubuntu

resolvers = {
    'centos': centos,
    'cirros': cirros,
    'debian': debian,
    'ubuntu': ubuntu
}


def resolve(url):
    for resolver in resolvers:
        if url.startswith(resolver):
            return resolvers[resolver].resolve(url)
    return url, None, None
