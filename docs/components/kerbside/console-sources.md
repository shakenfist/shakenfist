# Console Sources
Kerbside can connect to the following platforms:
* [Shaken Fist](https://shakenfist.com)
* [oVirt](https://www.ovirt.org), an Open Source Red Hat supported virtualization system
* [OpenStack](https://www.openstack.org), an Open Source cloud compute platform

The connection to each platform (a source of consoles, so "console sources") is defined in
the `sources.yaml` configuration file in YAML format.

Console sources are queried regularly (once a minute) for a list of consoles available.
It is possible to have more than one console source for a given type as well, so for example
the VDI proxy could be used to combine virtual machines from two OpenStack clusters together
seamlessly.

## Shaken Fist
The following options are used to configure a Shaken Fist console source (`type: shakenfist`).

| Option | Description |
|--------|-------------|
|source|The name of the source|
|type|The type of the source: `shakenfist`|
|url|The API URL for the source|
|username|The Shaken Fist namespace to authenticate to.
|password| The password to authenticate with
|ca_cert|Optional: the SSL CA public key certificate to validate API and VDI connections against|

## oVirt
The following options are used to configure an oVirt console source (`type: ovirt`).

| Option | Description |
|--------|-------------|
|source|The name of the source|
|type|The type of the source: `ovirt`|
|url|The API URL for the source|
|username|The username to authenticate to the source as|
|password| The password to authenticate with
|ca_cert|Optional: the SSL CA public key certificate to validate API and VDI connections against|


## OpenStack
The following options are used to configure an OpenStack console source (`type: openstack`).

| Option | Description |
|--------|-------------|
|source|The name of the source|
|type|The type of the source: `openstack|
|url|The API URL for the source|
|username|The username to authenticate to the source as . In the case of Shaken Fist, which does not have usernames, this is interpreted by Shaken Fist as the namespace to authenticate to.
|password| The password to authenticate with
|ca_cert|Optional: the SSL CA public key certificate to validate API and subsequent VDI connections against|
|project_name|The OpenStack project name for the associated used|
|user_domain_id|The OpenStack user domain id|
|project_domain_id|The OpenStack project domain id|
|flavors|The list of the flavors to expose as a console.|


## Example sources.yaml

An example follows:
``` 
- source: sfmel 
  type: shakenfist 
  url: https://sfmel.example.org/api 
  username: sfvdi 
  password: …omitted… 
  ca_cert: | 
    -----BEGIN CERTIFICATE----- 
    … 
    -----END CERTIFICATE----- 
 
- source: ovirt 
  type: ovirt 
  url: https://ovirt.example.org/ovirt-engine 
  username: Kerbside@internal 
  password: …
  ca_cert: | 
    -----BEGIN CERTIFICATE----- 
    … 
    -----END CERTIFICATE----- 
 
- source: kolla 
  type: openstack 
  url: http://kolla.example.org:5000 
  username: admin 
  password: …
  project_name: admin 
  user_domain_id: default 
  project_domain_id: default 
  flavors: 
    - vdi 
    - othervdi 
```
