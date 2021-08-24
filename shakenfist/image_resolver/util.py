import requests


from shakenfist.util import general as util_general


def fetch_remote_checksum(checksum_url):
    resp = requests.get(checksum_url,
                        headers={'User-Agent': util_general.get_user_agent()})
    if resp.status_code != 200:
        return {}

    checksums = {}
    for line in resp.text.split('\n'):
        elems = line.split()
        if len(elems) == 2:
            checksums[elems[1]] = elems[0]
    return checksums
