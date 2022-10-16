#!/usr/bin/python3

import datetime
import fcntl
import json
import os
import select
import subprocess
import sys


os.makedirs('/etc/sf', exist_ok=True)
with open('/etc/sf/deploy-log', 'w') as logfile:
    def logwrite(s):
        s = s.rstrip()
        timestamp = datetime.datetime.now()
        print('%s %s' % (timestamp, s))
        logfile.write('%s %s\n' % (timestamp, s.rstrip()))

    variables = {}
    variables['server_package'] = os.environ.get(
        'SERVER_PACKAGE', 'shakenfist')
    variables['client_package'] = os.environ.get(
        'CLIENT_PACKAGE', 'shakenfist-client')
    variables['agent_package'] = os.environ.get(
        'AGENT_PACKAGE', 'shakenfist-agent')
    variables['pip_extra'] = os.environ.get('PIP_EXTRA', '')

    variables['topology'] = os.environ.get('TOPOLOGY')

    if os.environ.get('SSH_KEY_FILENAME'):
        variables['ssh_key_filename'] = os.environ.get(
            'SSH_KEY_FILENAME')
        with open(variables['ssh_key_filename']) as f:
            variables['ssh_key'] = f.read()
        variables['ssh_user'] = os.environ.get('SSH_USER')
    else:
        variables['ssh_key_filename'] = ''
        variables['ssh_key'] = ''
        variables['ssh_user'] = ''

    if os.environ.get('ADMIN_PASSWORD'):
        variables['admin_password'] = os.environ.get('ADMIN_PASSWORD')
    elif os.environ.get('VAULT_SYSTEM_KEY_PATH'):
        variables['vault_system_key_path'] = os.environ.get(
            'VAULT_SYSTEM_KEY_PATH')
    else:
        variables['admin_password'] = 'Ukoh5vie'

    variables['floating_network_ipblock'] = os.environ.get(
        'FLOATING_IP_BLOCK', '10.10.0.0/24')
    variables['ksm_enabled'] = os.environ.get('KSM_ENABLED', 1)
    variables['deploy_name'] = os.environ.get('DEPLOY_NAME', 'sf')
    variables['restore_backup'] = os.environ.get('RESTORE_BACKUP', '')
    variables['ignore_mtu'] = (os.environ.get('IGNORE_MTU', 0) == 1)
    variables['dns_server'] = os.environ.get('DNS_SERVER', '8.8.8.8')
    variables['http_proxy'] = os.environ.get('HTTP_PROXY', '')

    with open('/etc/sf/deploy-vars.json', 'w') as varsfile:
        varsfile.write(json.dumps(variables, indent=4, sort_keys=True))

    logwrite('Install started')
    logwrite('Install variables:\n    %s'
             % '\n    '.join(json.dumps(variables, indent=4, sort_keys=True).split('\n')))

    env = os.environ.copy()
    env['ANSIBLE_SSH_PIPELINING'] = '0'

    obj = subprocess.Popen(
        ('ansible-playbook -i hosts --extra-vars "@/etc/sf/deploy-vars.json" '
         'deploy.yml'),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        env=env)
    logwrite('Installer pid is %d\n' % obj.pid)

    flags = fcntl.fcntl(obj.stdout, fcntl.F_GETFL)
    fcntl.fcntl(obj.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    flags = fcntl.fcntl(obj.stderr, fcntl.F_GETFL)
    fcntl.fcntl(obj.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    obj.stdin.close()
    while obj.poll() is None:
        readable, _, _ = select.select([obj.stderr, obj.stdout], [], [], 1)
        for f in readable:
            d = os.read(f.fileno(), 10000)
            for line in d.decode('utf-8').split('\n'):
                logwrite(line)

    logwrite('\nInstall finished with exit code %d' % obj.returncode)

    if obj.returncode != 0:
        logwrite('***************************************************')
        logwrite('*                     WARNING                     *')
        logwrite('*                                                 *')
        logwrite('* The return code of the deploy script indicates  *')
        logwrite('* that your install has failed. You will need to  *')
        logwrite('* inspect /etc/sf/deploy-log to determine why.    *')
        logwrite('* The failure will likely look like a fatal       *')
        logwrite('* ansible task. You can also request assistance   *')
        logwrite('* by filing a github issue at                     *')
        logwrite('* https://github.com/shakenfist/shakenfist/issues *')
        logwrite('***************************************************')

    sys.exit(obj.returncode)
