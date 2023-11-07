# Consoles

Shaken Fist supports three types of instance consoles. This page describes each
of those consoles and their functionality. Additionally, Shaken Fist can collect
screen captures of your instance's consoles, which is also documented here.

## Read only serial console

Similarly to OpenStack, we support requesting a read only copy of the content
of the serial console via our REST API. This console is based off a log of
console activity, so can return events which have already occurred. You can
access the console information from the command line like this:

```bash
sf-client instance consoledata ca8b3e3b-f681-47a2-99c5-6ddea00cc49f 1000
[530]: |                       Self-hosted runner registration                        |
[   74.594732] cloud-init[530]: |                                                                              |
[   74.596442] cloud-init[530]: --------------------------------------------------------------------------------
[   74.602452] cloud-init[530]: # Authentication
[   79.099457] cloud-init[530]: √ Connected to GitHub
[   79.184807] cloud-init[530]: # Runner Registration
[   80.283666] cloud-init[530]: √ Runner successfully added
[   83.608069] cloud-init[530]: √ Runner connection is good
[   83.609389] cloud-init[530]: # Runner settings
[   83.615351] cloud-init[530]: √ Settings Saved.
[   86.194598] cloud-init[530]: √ Connected to GitHub
[   87.331467] cloud-init[530]: Current runner version: '2.299.1'
[   87.333703] cloud-init[530]: 2023-01-21 06:54:11Z: Listening for Jobs
[   91.102179] cloud-init[530]: 2023-01-21 06:54:15Z: Running job: ubuntu-2004-slim-primary
```

The final argument is optional. In this example we are requesting the final
1,000 bytes of console output.

## Interactive serial console

There is also interactive access to that same serial console, although it
requires direct network access to the hypervisor node at the moment. If you lookup
the instance with a show command, you'll see a "console port" listed:

```bash
sf-client instance show c301ad4a-1ad4-49d7-b1e7-cb08ad3bf23d
uuid          : c301ad4a-1ad4-49d7-b1e7-cb08ad3bf23d
name          : sfcbr-s2SXNcZGKaJMywSd
...
node          : sf-2
console port  : 30049
vdi port      : 37804
...
```

If you telnet to the console port on the hypervisor node's IP, you'll land in
an interactive console for the instance. So in this example:

```bash
telnet sf-2 30049
```

## Archival of the serial console

Optionally, the content of the serial console can be archived when an instance
is deleted. This is useful for debugging ephemeral instances which might have
been deleted by the time you notice a problem -- for example instances used for
Continuous Integration environments.

The time the serial console is kept for is configured with the
ARCHIVE_INSTANCE_CONSOLE_DURATION configuration variable, which specifies how many
days to keep the console for. On instance deletion, the console log is converted
to an artifact and stored as any other. These artifacts have type 'other', and
will have a source URL in the form of sf://instance/...uuid.../console within
the same namespace as the instance. Set ARCHIVE_INSTANCE_CONSOLE_DURATION to
0 to disable this behavior.

## Interactive VDI console

There is also a graphical console. Similarly to the telnet console, it requires
direct network access to the hypervisor node, and is accessed at the "vdi port"
TCP port. By default this console is SPICE since v0.7, although VNC is also
available.

You can select from 'vnc' or 'spice' (the default) by setting the `vdi` argument
in your video specification for the instance. If you set `vdi=spiceconcurrent`, then
experimental support for multiple users accessing the same SPICE console at the
same time is enabled. For more details about the experimental nature of concurrent
SPICE consoles, see https://www.spice-space.org/multiple-clients.html.

And example video specification would be:

```
--video model=qxl,memory=65536,vdi=spiceconcurrent
```

## Screen captures

Since v0.78, Shaken Fist also provides an API for collecting screen captures of
the console. This works for either serial consoles or graphical consoles, its
literally the same was whatever would have been displayed on the monitor if the
instance was a physical machine.

You can take a screen capture of an instance using the python command line client
like this:

```bash
sf-client instance screenshot ...uuid...
```

