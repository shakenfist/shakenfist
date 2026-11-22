# Copyright 2026 Michael Still and contributors

"""A failed database RPC tells its caller which RPC failed, nothing more.

The servicer in ``shakenfist/daemons/database/main.py`` has two ways to
report a failure, and both of them leave the database tier: the gRPC
status detail, which arrives at the client as part of
``str(grpc.RpcError)``, and a reply's ``error`` field, which the
``mariadb.py`` wrappers read back by name. Passing ``str(e)`` to either
sends the exception's rendering with it, and when the exception is a
SQLAlchemy error -- or a ``DatabaseUnavailable`` wrapping one -- that
rendering is the statement which failed followed by its bound
parameters, uuids included.

The client then folds what it received into its own exception message
and log lines, and for the placement and claim RPCs into an event on
the object (``Instance._release_placement()`` puts ``result['error']``
straight into ``add_event()``'s extra). A tenant can read that event
through the API, so the statement text travelled from the database tier
to a tenant-readable record (#5027). #5024 stopped it for the three
credential attribute readers by hand; the remaining 191 sites are what
this test exists for.

The guard is structural rather than behavioural, because the defect is
a habit: it came back one handler at a time as the service grew, and a
test which exercises a few handlers cannot see the next one. So the
rule is that inside an ``except ... as e`` handler in that file, the
name the handler bound may not appear anywhere in a ``set_details()``
argument or an ``error=`` keyword. That covers ``str(e)``, ``repr(e)``
and ``f'...{e}'`` alike -- it is the exception reaching the client
which is the defect, not the particular way it was stringified.

A handler with something specific and safe to say is not blocked from
saying it: the rule is about the exception, not about the detail. The
``INVALID_ARGUMENT`` arm of ``GetNamespaceClaims`` names the argument
the caller got wrong, and this guard accepts that because the message
is written rather than derived from the exception.
"""

import ast
import os
from unittest import mock
from uuid import uuid4

import grpc
from sqlalchemy.exc import OperationalError

from shakenfist.daemons.database import main as daemons_database_main
from shakenfist import exceptions
from shakenfist.protos import database_pb2
from shakenfist.tests import base


SERVICER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'daemons', 'database', 'main.py')

# There were 194 guarded sites across 186 handlers when this was
# written. The floor sits a long way below that: its job is to catch a
# source file which stopped parsing or stopped being found, not to
# track the count.
MINIMUM_GUARDED_SITES = 150


def _mentions(node, name):
    """True if ``name`` is read anywhere under ``node``."""
    return any(isinstance(inner, ast.Name) and inner.id == name
               for inner in ast.walk(node))


def _client_visible_values(node):
    """Every expression in ``node`` which a client gets to see.

    The two channels are a ``set_details()`` call's arguments and an
    ``error=`` keyword on a reply constructor. Nothing else in a
    handler reaches the caller.
    """
    for inner in ast.walk(node):
        if (isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == 'set_details'):
            yield from inner.args
        if isinstance(inner, ast.Call):
            for keyword in inner.keywords:
                if keyword.arg == 'error':
                    yield keyword.value


def find_leaked_exceptions(source):
    """(lineno, handler name) for each site which hands on its exception.

    Nested handlers are both walked, so an inner handler's site is seen
    from the outer one too; the set makes each site count once.
    """
    tree = ast.parse(source)
    sites = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        for value in _client_visible_values(node):
            if _mentions(value, node.name):
                sites.add((value.lineno, node.name))
    return sorted(sites)


def count_guarded_sites(source):
    """How many client-visible values sit inside an except handler.

    Without this a scan which found no handlers at all would read as a
    clean result.
    """
    tree = ast.parse(source)
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            total += len(list(_client_visible_values(node)))
    return total


class ErrorDetailCheckTestCase(base.ShakenFistTestCase):
    """The positive controls. Without these a clean scan proves nothing."""

    def test_str_of_the_exception_in_a_detail_is_an_offence(self):
        offences = find_leaked_exceptions(
            'try:\n'
            '    read()\n'
            'except Exception as e:\n'
            '    context.set_details(str(e))\n')
        self.assertEqual([(4, 'e')], offences)

    def test_str_of_the_exception_in_an_error_field_is_an_offence(self):
        offences = find_leaked_exceptions(
            'try:\n'
            '    write()\n'
            'except Exception as e:\n'
            '    return StatusReply(success=False, error=str(e))\n')
        self.assertEqual([(4, 'e')], offences)

    def test_an_interpolated_exception_is_an_offence(self):
        """An f-string carries the statement just as faithfully as
        str() does, so the rule is about the exception reaching the
        client rather than about str()."""
        offences = find_leaked_exceptions(
            'try:\n'
            '    read()\n'
            'except Exception as err:\n'
            "    context.set_details(f'read failed: {err}')\n")
        self.assertEqual([(4, 'err')], offences)

    def test_repr_of_the_exception_is_an_offence(self):
        offences = find_leaked_exceptions(
            'try:\n'
            '    read()\n'
            'except Exception as e:\n'
            '    context.set_details(repr(e))\n')
        self.assertEqual([(4, 'e')], offences)

    def test_a_written_message_is_accepted(self):
        """The negative control: a handler is free to say something
        specific, as long as it wrote it rather than derived it from
        the exception."""
        offences = find_leaked_exceptions(
            'try:\n'
            '    read()\n'
            'except ValueError:\n'
            "    context.set_details('coverage_state is not a valid state')\n")
        self.assertEqual([], offences)

    def test_logging_the_exception_is_not_an_offence(self):
        """The exception must still be recorded here, on the node which
        can act on it. Only what leaves the node is checked."""
        offences = find_leaked_exceptions(
            'try:\n'
            '    read()\n'
            'except Exception as e:\n'
            "    util_exceptions.ignore_exception('Read failed', e)\n"
            "    context.set_details(_rpc_error('Read'))\n")
        self.assertEqual([], offences)

    def test_a_nested_handler_is_reported_once(self):
        """ast.walk() reaches an inner handler's site from the outer
        handler too, so without deduplication every nested site would
        be reported twice."""
        offences = find_leaked_exceptions(
            'try:\n'
            '    read()\n'
            'except OperationalError:\n'
            '    try:\n'
            '        retry()\n'
            '    except Exception as e:\n'
            '        context.set_details(str(e))\n')
        self.assertEqual([(7, 'e')], offences)


class ServicerErrorDetailTestCase(base.ShakenFistTestCase):
    def test_no_handler_hands_its_exception_to_the_client(self):
        """The real guard, over the servicer as it stands."""
        with open(SERVICER) as f:
            source = f.read()

        self.assertEqual([], find_leaked_exceptions(source))
        self.assertGreater(
            count_guarded_sites(source), MINIMUM_GUARDED_SITES)


class FailedRpcRepliesTestCase(base.ShakenFistTestCase):
    """What a client actually receives, for one RPC of each shape."""

    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.uuid = str(uuid4())
        # What SQLAlchemy renders for a failed statement: str() appends
        # the SQL and the bound parameters to the DBAPI message, which
        # is how the uuid got into a reply in the first place.
        self.sqlalchemy_error = OperationalError(
            'SELECT namespace_key_attributes.uuid FROM '
            'namespace_key_attributes WHERE uuid = %(uuid_1)s',
            {'uuid_1': self.uuid},
            Exception('(1045, "Access denied for user")'))

    def assertCarriesNoStatement(self, reported):
        self.assertNotIn('SELECT', reported)
        self.assertNotIn(self.uuid, reported)
        self.assertNotIn('Access denied', reported)

    def test_a_failed_read_reports_only_the_rpc_name(self):
        # Wrapped the way the credential readers wrap it, so the test
        # covers the indirection the issue describes rather than just a
        # bare SQLAlchemy error.
        wrapped = exceptions.DatabaseUnavailable(
            f'namespace key {self.uuid} could not be read: '
            f'{self.sqlalchemy_error}')
        self.assertIn('SELECT', str(wrapped))

        context = mock.MagicMock()
        with mock.patch('shakenfist.mariadb.'
                        '_direct_get_namespace_key_attributes',
                        side_effect=wrapped):
            reply = self.servicer.GetNamespaceKeyAttributes(
                database_pb2.GetNamespaceKeyAttributesRequest(
                    uuid=self.uuid),
                context)

        self.assertFalse(reply.found)
        context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
        detail = context.set_details.call_args[0][0]
        self.assertEqual('database GetNamespaceKeyAttributes failed', detail)
        self.assertCarriesNoStatement(detail)

    def test_a_failed_write_reports_only_the_rpc_name(self):
        # Enqueue answers with success=False and an error field rather
        # than a status code, which is the other half of the leak: the
        # reply body is read back by name, not through RpcError.
        context = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._direct_work_queue_enqueue',
                        side_effect=self.sqlalchemy_error):
            reply = self.servicer.Enqueue(
                database_pb2.EnqueueRequest(
                    queue_name='node-test', work_item='{"tasks": []}'),
                context)

        self.assertFalse(reply.success)
        self.assertEqual('database Enqueue failed', reply.error)
        self.assertCarriesNoStatement(reply.error)
