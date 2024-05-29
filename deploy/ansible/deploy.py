#!/usr/bin/python3

import datetime
import fcntl
import json
import os
import secrets
import select
import string
import subprocess
import sys


PASSWORD_CHARS = ''
for c in string.ascii_letters + string.ascii_letters.upper() + string.digits:
    PASSWORD_CHARS += c


os.makedirs('/etc/sf', exist_ok=True)
with open('/etc/sf/deploy-log', 'w') as logfile:
    def log_write(s):
        s = s.rstrip()
        timestamp = datetime.datetime.now()
        print('{} {}'.format(timestamp, s))
        logfile.write('{} {}\n'.format(timestamp, s.rstrip()))

    variables = {}
    if os.path.exists('/etc/sf/deploy-vars.json'):
        with open('/etc/sf/deploy-vars.json') as varsfile:
            variables = json.loads(varsfile.read())

    def update_if_specified(varname, default, rename=None):
        v = os.environ.get(varname.upper())
        if rename:
            varname = rename

        if v:
            variables[varname] = v
            log_write('Changed configuration value {} to {}'.format(varname, v))
        elif varname not in variables:
            variables[varname] = default
            log_write('Defaulted configuration value {} to {}'.format(varname, v))

    def update_if_specified_bool(varname, default):
        v = os.environ.get(varname.upper())
        if v:
            if v.lower() == 'true':
                v = '1'
            elif v.lower() == 'false':
                v = '0'
            variables[varname] = v
            log_write('Changed configuration value {} to {}'.format(varname, v))
        elif varname not in variables:
            variables[varname] = default
            log_write('Defaulted configuration value {} to {}'.format(varname, v))

    update_if_specified('server_package', 'shakenfist')
    update_if_specified('client_package', 'shakenfist-client')
    update_if_specified('agent_package', 'shakenfist-agent')
    update_if_specified('pip_extra', '')
    update_if_specified('topology', None)

    update_if_specified('ssh_key_filename', '')
    if variables['ssh_key_filename'] and os.path.exists(variables['ssh_key_filename']):
        with open(variables['ssh_key_filename']) as f:
            variables['ssh_key'] = f.read()
    update_if_specified('ssh_user', '')

    update_if_specified(
        'admin_password', ''.join(secrets.choice(PASSWORD_CHARS) for i in range(12)))
    update_if_specified(
        'auth_secret', ''.join(secrets.choice(PASSWORD_CHARS) for i in range(50)))

    # vault_system_key_path must be absent not empty if unset
    update_if_specified('vault_system_key_path', '')
    if variables['vault_system_key_path'] == '':
        del variables['vault_system_key_path']

    update_if_specified('floating_ip_block', '10.10.0.0/24', rename='floating_network_ipblock')
    update_if_specified_bool('ksm_enabled', '1')
    update_if_specified('deploy_name', 'sf')
    update_if_specified_bool('ignore_mtu', '0')
    update_if_specified('dns_server', '8.8.8.8')
    update_if_specified('http_proxy', '')

    update_if_specified('extra_config', '[]')

    with open('/etc/sf/deploy-vars.json', 'w') as varsfile:
        varsfile.write(json.dumps(variables, indent=4, sort_keys=True))

    log_write('Install started')
    log_write('Install variables:\n    %s'
              % '\n    '.join(json.dumps(variables, indent=4, sort_keys=True).split('\n')))

    env = os.environ.copy()
    env['ANSIBLE_SSH_PIPELINING'] = '0'

    obj = subprocess.Popen(
        ('ansible-playbook -i hosts --extra-vars "@/etc/sf/deploy-vars.json" '
         '%s deploy.yml'
         % ' '.join(sys.argv[1:])),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        env=env)
    log_write('Installer pid is %d\n' % obj.pid)

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
                log_write(line)

    log_write('\nInstall finished with exit code %d' % obj.returncode)

    if obj.returncode != 0:
        log_write('***************************************************')
        log_write('*                     WARNING                     *')
        log_write('*                                                 *')
        log_write('* The return code of the deploy script indicates  *')
        log_write('* that your install has failed. You will need to  *')
        log_write('* inspect /etc/sf/deploy-log to determine why.    *')
        log_write('* The failure will likely look like a fatal       *')
        log_write('* ansible task. You can also request assistance   *')
        log_write('* by filing a github issue at                     *')
        log_write('* https://github.com/shakenfist/shakenfist/issues *')
        log_write('***************************************************')

    sys.exit(obj.returncode)
