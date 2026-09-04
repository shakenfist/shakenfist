# Copyright 2026 Michael Still and contributors
"""Running commands for the CI harness.

This replaces the harness's use of
``oslo_concurrency.processutils.execute()``. Ten call sites wanted
``subprocess.run()`` with an acceptable-exit-code list, which is what
this is.

It does not, on its own, remove the oslo stack from an install, and
nothing in ``pyproject.toml`` changes because of it. ``oslo.concurrency``
and its eleven companions (oslo.config, oslo.i18n, oslo.utils,
debtcollector, fasteners, iso8601, netaddr, pyparsing, rfc3986,
stevedore and wrapt) are pulled in by two *declared* dependencies, and a
source-level import has no say in pip's resolution:

* ``shakenfist-utilities`` requires it. Removed on that project's
  develop branch by shakenfist/library-utilities#44, so this one is
  waiting on a release and a pin bump here.
* ``clingwrap`` requires it, and uses it -- ``clingwrap/main.py``
  imports ``processutils`` for the same reason this file existed to
  serve. Until clingwrap makes the same change, the twelve packages
  stay.

So this is one of three steps, and the last of the three to be
identified rather than the last to land. What it buys today is that the
harness no longer depends on a package it does not declare, which is
what ``undeclared-direct-dependency`` in the consistency audit asks of
it.

Like ``safe_headers``, this module deliberately imports nothing beyond
the standard library, so the unit suite can load it from source. The CI
harness itself is a client of a running cluster and is not importable
from ``shakenfist/tests``.
"""

import os
import re
import subprocess


#: A backstop for credentials on a command line, not the mechanism that
#: keeps them off one.
#:
#: docs/developer_guide/coding_rules.md ("Credential-carrying routes are
#: not logged, not redacted") records why name-based redaction is not
#: allowed to be the protection: the API layer tried it and leaked
#: identity tokens for as long as it took somebody to add a field the
#: lists had not heard of. The same hole is here -- this matches
#: ``--key``, ``--ssh-key`` and ``--token=`` but cannot match ``-k``, a
#: positional secret, or a flag nobody thought of.
#:
#: The commands that carry a namespace key therefore pass it through the
#: environment instead (``SHAKENFIST_KEY``), and never build it into
#: argv. This regex is what catches the case somebody gets wrong later.
SECRET_FLAG_RE = re.compile(
    r'(--?[\w-]*(?:key|password|passwd|token|secret)(?:=|\s+))(\S+)',
    re.IGNORECASE)


def mask_secrets(command):
    """Replace the value of any credential-bearing flag with '***'."""
    return SECRET_FLAG_RE.sub(r'\1***', command)


class ProcessExecutionError(Exception):
    """A command exited with a code the caller did not accept.

    The attribute names match the oslo exception this replaces, because
    callers read `.exit_code` and interpolate the exception into skip
    messages.

    `.cmd`, `.stdout` and `.stderr` have all been through
    `mask_secrets()`, so they are not byte-identical to what `execute()`
    returns on the success path. That is deliberate -- this object is
    built to be printed -- but it does mean the exception is the wrong
    place to read a command's exact output from.
    """

    def __init__(self, cmd, exit_code, stdout, stderr):
        self.cmd = cmd
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(cmd, exit_code, stdout, stderr)

    def __str__(self):
        return (
            'Unexpected error while running command.\n'
            'Command: %s\n'
            'Exit code: %s\n'
            'Stdout: %r\n'
            'Stderr: %r'
            % (self.cmd, self.exit_code, self.stdout, self.stderr))


class ProcessTimeoutError(ProcessExecutionError):
    """A command did not finish inside its timeout, and was killed.

    A subclass so that the handlers which already treat "the command did
    not work" as a reason to skip -- `_require_node_exec()` most of all,
    where a wedged node is exactly as untestable as an unreachable one
    -- catch this without being taught about it. `.exit_code` is None,
    because the command was killed rather than exiting.
    """

    def __init__(self, cmd, timeout, stdout, stderr):
        self.timeout = timeout
        super().__init__(cmd, None, stdout, stderr)

    def __str__(self):
        return (
            'Command did not complete within %s seconds and was killed.\n'
            'Command: %s\n'
            'Stdout: %r\n'
            'Stderr: %r'
            % (self.timeout, self.cmd, self.stdout, self.stderr))


def execute(*args, shell=False, check_exit_code=True, env=None, timeout=None):
    """Run a command and return its (stdout, stderr) as text.

    With ``shell=False`` the arguments are the argv of the command. With
    ``shell=True`` a single string is handed to the shell.

    ``check_exit_code`` is True for "only 0", False for "any", an int
    for a single acceptable code, or a collection of acceptable codes.
    Any exit code which is not acceptable raises ProcessExecutionError.

    ``env`` is a mapping laid over this process' environment, not a
    replacement for it -- the commands here need PATH. It is how a
    credential is handed to a child without putting it on a command line
    somebody is going to log.

    ``timeout`` is in seconds, and None means wait forever, which is
    what oslo did. Exceeding it kills the command and raises
    ProcessTimeoutError regardless of ``check_exit_code``, there being
    no exit code to accept.

    Output is decoded with ``os.fsdecode``, which cannot fail, rather
    than a strict UTF-8 decode: this runs ``ip``, ``ssh`` and the
    client against real hosts, and a test must not disappear into a
    UnicodeDecodeError because a command emitted a stray byte. Nothing
    is stripped, because callers split the output on newlines and parse
    it as JSON.

    stdin is /dev/null. ssh reads stdin, and a command inheriting the
    test runner's would block rather than fail.
    """
    if shell:
        if len(args) != 1:
            raise ValueError(
                'shell=True takes exactly one command string, got %d '
                'arguments' % len(args))
        command = str(args[0])
        rendered = command
    else:
        command = [str(a) for a in args]
        rendered = ' '.join(command)

    child_env = None
    if env is not None:
        child_env = dict(os.environ)
        child_env.update(env)

    try:
        completed = subprocess.run(
            command, shell=shell, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, check=False, env=child_env, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ProcessTimeoutError(
            cmd=mask_secrets(rendered), timeout=timeout,
            stdout=mask_secrets(os.fsdecode(e.stdout or b'')),
            stderr=mask_secrets(os.fsdecode(e.stderr or b'')))

    stdout = os.fsdecode(completed.stdout)
    stderr = os.fsdecode(completed.stderr)

    if check_exit_code is True:
        acceptable = [0]
    elif check_exit_code is False:
        acceptable = None
    elif isinstance(check_exit_code, int):
        # After the identity checks above, so True and False are already
        # gone -- they are ints too, and would land here otherwise.
        acceptable = [check_exit_code]
    else:
        acceptable = list(check_exit_code)

    if acceptable is not None and completed.returncode not in acceptable:
        raise ProcessExecutionError(
            cmd=mask_secrets(rendered), exit_code=completed.returncode,
            stdout=mask_secrets(stdout), stderr=mask_secrets(stderr))

    return stdout, stderr
