# Copyright (c) 2013 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import setuptools

this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

version_path = os.path.join(this_directory, 'VERSION.txt')
if os.path.exists(version_path):
    with open(version_path) as f:
        os.environ['PBR_VERSION'] = f.readline().rstrip()


setuptools.setup(
    setup_requires=['pbr'],
    pbr=True,
    long_description=long_description,
    long_description_content_type='text/markdown',
    data_files=[
        (
            'share/shakenfist/templates', [
                'deploy/ansible/files/libvirt.tmpl',
                'deploy/ansible/files/dhcp.tmpl',
                'deploy/ansible/files/dhcphosts.tmpl',
                'deploy/ansible/files/sf.service'
            ]
        ),
        (
            'share/shakenfist/installer', [
                'deploy.tgz',
                'deploy/install',
                'deploy/uninstall'
            ]
        ),
        (
            'share/shakenfist/docs', [
                'docs.tgz'
            ]
        )
    ],
)
