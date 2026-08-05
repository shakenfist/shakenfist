# Copyright 2019 Michael Still and contributors
"""Where each declared API parameter really comes from.

The ``swagger_helper()`` declarations on every endpoint say which
parameters exist, what type they are and where they arrive. Phase 3 of
docs/plans/PLAN-api-input-validation.md compiles them into request
validation, at which point a declaration that disagrees with its handler
stops being a documentation bug and starts rejecting valid requests. This
module is the single statement of what agreement means.

Four sources decide where a parameter comes from, in order:

* a name appearing in a route the class is mounted on is in the ``path``;
* a name in the schema of a ``@use_kwargs(..., location='query')`` on the
  handler is in the ``query``;
* a name the handler reads from ``flask.request.args`` is in the
  ``query``, even if it can also arrive in the body -- the published
  documentation and the query-string fallback phase 3 compiles must
  agree;
* everything else is in the ``body``, because ``log_request`` merges the
  JSON body into handler kwargs.

``header`` and ``formData`` say where a value comes from in a way none of
those can check, so they are reported as underivable and left alone.

Every source here answers "not found" and "cannot read this" with the
same empty set, so input which is skipped produces a confident wrong
answer rather than a missing one. Anything unreadable is collected into
``problems`` and both consumers fail on it.

Two consumers share this: ``tools/fix-api-parameter-locations.py``
rewrites the declarations to agree, and
``shakenfist/tests/external_api/test_parameter_declarations.py`` fails
when they do not. They used to carry near-identical copies of the walk,
which had already diverged in how they resolved a non-literal parameter
name.

Everything here reads source with ``ast`` rather than importing it. That
keeps the pre-commit hook runnable with a bare interpreter, and means a
declaration which would abort ``sf-api`` at import time can still be
analysed.
"""
import ast
import collections
import functools
import glob
import os
import re
from collections.abc import Iterator
from typing import Any
from typing import NamedTuple
from typing import Optional
from typing import Union


API_DIR = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(API_DIR, 'app.py')
BASE = os.path.join(API_DIR, 'base.py')

HANDLER_METHODS = ('get', 'post', 'put', 'delete', 'patch')

# Objects the decorators inject into a handler's kwargs. They are not
# request parameters and must never be declared or validated.
INJECTED_SUFFIX = '_from_db'

# Locations no rule here can derive. Reported rather than rewritten:
# deriving one of these to 'body' would turn a correct declaration into
# a wrong one.
UNDERIVABLE_LOCATIONS = frozenset(['header', 'formData'])

# A scope a name can be defined in. Both carry a ``body`` of their own
# statements, which is what makes innermost-first resolution possible.
Scope = Union[ast.Module, ast.ClassDef]


class Declaration(NamedTuple):
    """One parameter as an endpoint declares it.

    ``location_node`` is the AST node holding the location literal,
    which is what the fixer rewrites in place. The resolved fields are
    None when they could not be read statically.
    """

    path: Optional[str]
    cls: Optional[str]
    method: str
    name: Optional[str]
    location: Optional[str]
    required: Optional[bool]
    location_node: Optional[ast.expr]


def _parse(path: str) -> ast.Module:
    with open(path) as f:
        return ast.parse(f.read())


@functools.cache
def base_constants() -> dict[str, Any]:
    """Module-level string constants of base.py, by name.

    Cached and called lazily rather than computed at import: this
    module ships inside the runtime package, and opening base.py's
    source as a side effect of import would fail in any deployment
    where the source is not on disk. A missing file should surface at
    use, from the consumer that needed it.

    ``RAW_BODY_PARAMETER`` is referenced rather than spelled out in the
    one declaration that documents a raw request body, so resolving it
    is the difference between reading that declaration and skipping it.
    Read from source rather than imported, because importing base.py
    means importing flask.
    """
    out: dict[str, Any] = {}
    for node in _parse(BASE).body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = value
    return out


def literal(node: Optional[ast.AST]) -> Any:
    """A declaration element's value, or None if it is not static.

    An ``api_base.SOMETHING`` reference resolves to the constant's
    value; anything else which is not a literal is None, which every
    caller treats as "cannot be checked" rather than as a value.
    """
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        # ValueError is the common "not a literal" answer for a Name, an
        # Attribute or a Call. TypeError arrives from a node which parses
        # but cannot be evaluated. Both mean "not static", which is what
        # every caller here is asking, so neither should escape as a
        # traceback from a helper documented to answer with None.
        return base_constants().get(ast.unparse(node).split('.')[-1])


def route_parameters(app: str = APP,
                     problems: Optional[list[str]] = None
                     ) -> dict[str, set[str]]:
    """Path parameter names per endpoint class, from the mounted routes.

    Werkzeug routes may name a converter, as in ``<path:label_name>`` or
    ``<int(min=1):x>``, so the parameter name is whatever follows the
    last colon. An earlier version of this matched only bare names and
    so silently skipped three LabelEndpoint declarations.

    A route this cannot read is recorded in ``problems`` rather than
    dropped. Dropping it empties the class's route set, which derives
    every one of its parameters to ``body`` -- so the fixer would
    rewrite a *correct* ``path`` declaration, and phase 3 would compile
    a schema looking in the JSON body for a URL segment.

    Keyed on the bare class name, which is what the caller has. Two
    endpoint classes of the same name in different modules would
    therefore share one merged route set and each derive the other's
    URL segments as ``path`` -- a confidently wrong answer rather than
    an empty one, so it is recorded too.

    The class being mounted has to be readable for any of that to
    apply: a registration whose first argument is not a plain name or
    attribute names no class this can match, which silently empties
    some class's route set exactly as an unreadable route would.
    """
    out: dict[str, set[str]] = collections.defaultdict(set)
    qualified: dict[str, str] = {}
    for node in ast.walk(_parse(app)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, 'attr', '') != 'add_resource':
            continue

        resource = node.args[0] if node.args else None
        if not isinstance(resource, (ast.Name, ast.Attribute)):
            if problems is not None:
                problems.append(
                    'a resource is mounted by an expression this cannot read '
                    '(%s), so the routes of whichever class it names are '
                    'missing' % (ast.unparse(node) if resource is None
                                 else ast.unparse(resource)))
            continue

        mounted = ast.unparse(resource)
        cls = mounted.split('.')[-1]
        if qualified.setdefault(cls, mounted) != mounted and (
                problems is not None):
            problems.append(
                '%s is mounted from two modules (%s and %s), so their path '
                'parameters cannot be told apart'
                % (cls, qualified[cls], mounted))
        for arg in node.args[1:]:
            route = literal(arg)
            if isinstance(route, str):
                names = {segment.split(':')[-1]
                         for segment in re.findall(r'<([^>]+)>', route)}
                # Routes are merged per class, and derived_location()
                # asks only whether a name is in the class's set. Two
                # routes of different shapes -- the collection and item
                # pair, /things and /things/<thing_ref> -- would give the
                # collection handler a path parameter it never receives,
                # and the fixer would rewrite a correct declaration to
                # match. Nothing in the tree does this today.
                if cls in out and out[cls] != names and problems is not None:
                    problems.append(
                        '%s is mounted on routes with different parameters '
                        '(%s and %s), so which of them any one handler '
                        'receives cannot be derived'
                        % (cls, ', '.join(sorted(out[cls])) or 'none',
                           ', '.join(sorted(names)) or 'none'))
                out[cls] |= names
            elif problems is not None:
                problems.append(
                    '%s is mounted on a route this cannot read (%s), so its '
                    'path parameters cannot be derived'
                    % (cls, ast.unparse(arg)))
    return out


def query_parameters(fn: ast.FunctionDef, scopes: list[Scope],
                     problems: Optional[list[str]] = None) -> set[str]:
    """Names the handler parses from the query string with webargs.

    Read off the handler's own ``@use_kwargs`` decorator: its
    ``location`` keyword, and the schema its first argument names. The
    earlier version looked for any class-level assignment called
    ``get_args`` and applied it to every handler in the class, which
    described a weaker rule than the docstring claimed -- a schema bound
    at ``location='json'``, or a class with a webargs ``get`` beside a
    ``post`` declaring a same-named parameter, would both have been
    derived wrongly.

    webargs accepts a tuple of locations as well as a single one, so a
    schema bound at ``('query', 'json')`` is a query schema too. No site
    uses that today, but it is the shape a fix for issue 3629 -- and
    decision D6's fallback -- would introduce, and reading it as "not
    query" would send the fixer to rewrite the very declarations the fix
    had just made true.
    """
    out: set[str] = set()
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call) or not dec.args:
            continue
        if ast.unparse(dec.func).split('.')[-1] != 'use_kwargs':
            continue

        declared = [k for k in dec.keywords if k.arg == 'location']
        location = literal(declared[-1].value) if declared else None
        if declared and location is None:
            # Absent means webargs' default of json, which is not this.
            # Present but unreadable is a different answer wearing the
            # same face, and resolves to 'body' for every key it binds.
            if problems is not None:
                problems.append(
                    '%s binds a webargs schema at a location this cannot '
                    'read (%s)'
                    % (fn.name, ast.unparse(declared[-1].value)))
            continue
        if isinstance(location, (tuple, list)):
            if 'query' not in location:
                continue
        elif location != 'query':
            continue
        keys = _schema_keys(scopes, ast.unparse(dec.args[0]), problems)
        if keys is None:
            # An inline dict literal, or a name defined somewhere this
            # cannot follow. Deriving nothing from it means every one of
            # its parameters falls through to 'body'.
            if problems is not None:
                problems.append(
                    '%s parses the query string with a schema this cannot '
                    'resolve (%s)' % (fn.name, ast.unparse(dec.args[0])))
            continue
        out |= keys
    return out


def _schema_keys(scopes: list[Scope], name: str,
                 problems: Optional[list[str]] = None) -> Optional[set[str]]:
    """The keys of the dict assigned to ``name``, innermost scope first.

    The scopes are searched in order and the first one to *define* the
    name wins, rather than every definition being unioned together --
    and rather than the first definition to yield a key, which is what
    an earlier version implemented. Under that rule a class-level
    ``get_args = {}``, or one whose keys could not be read, fell
    through to a same-named module-level dict: the cross-scope leak
    with an extra step. Each scope contributes only its own assignments
    -- a module's are its top-level statements, not everything nested
    inside it -- so one endpoint class's ``get_args`` cannot leak into
    the derivation for another class in the same file. That leak was
    real: it made the fixer willing to rewrite a correct `body`
    declaration to `query`, and phase 3 would then have compiled a
    query-string fallback for a parameter which never arrives that way.
    Drift introduced by the machinery built to prevent drift.

    Returns None when no scope defines the name, which the caller
    reports. A defining scope whose content cannot be read is reported
    here instead, by name, and never falls through: 'not found' and
    'cannot read this' must not share an answer. An empty literal dict
    is neither -- it is readable and legitimately binds nothing.
    """
    for scope in scopes:
        defined = False
        unreadable = False
        out: set[str] = set()
        for node in scope.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(ast.unparse(t) == name for t in node.targets):
                continue
            defined = True
            if not isinstance(node.value, ast.Dict):
                unreadable = True
                continue
            for key in node.value.keys:
                value = literal(key)
                if value is None:
                    unreadable = True
                else:
                    out.add(value)
        if defined:
            if unreadable and problems is not None:
                problems.append(
                    '%s is assigned something this cannot read (a value '
                    'which is not a dict literal, or a key which is not a '
                    'literal), so keys bound from it are missing' % name)
            return out
    return None


def request_args_parameters(fn: ast.FunctionDef) -> set[str]:
    """Names the handler reads straight out of flask.request.args.

    ``ClusterOperationsEndpoint.get`` accepts its target parameters as
    body keys, via the ``log_request`` merge, but falls back to
    ``flask.request.args.get()`` for each so a raw ``?target_...=`` GET
    keeps working -- the form AGENTS.md documents. A parameter read this
    way is a query parameter whatever else it also is.
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get'
                and _is_request_args(node.func.value)
                and node.args):
            key = literal(node.args[0])
        elif isinstance(node, ast.Subscript) and _is_request_args(node.value):
            key = literal(node.slice)
        else:
            continue
        if key is not None:
            out.add(key)
    return out


def _is_request_args(node: ast.AST) -> bool:
    """Is this node ``request.args``, however ``request`` was imported?"""
    return (isinstance(node, ast.Attribute) and node.attr == 'args'
            and ast.unparse(node.value).split('.')[-1] == 'request')


def handler_kwargs(fn: ast.FunctionDef) -> list[str]:
    """Every parameter a caller could populate, keyword-only included."""
    args = list(fn.args.args) + list(fn.args.kwonlyargs)
    return [a.arg for a in args
            if a.arg != 'self' and not a.arg.endswith(INJECTED_SUFFIX)]


def handlers(api_dir: str = API_DIR,
             problems: Optional[list[str]] = None
             ) -> Iterator[tuple[str, ast.Module, ast.ClassDef,
                                 ast.FunctionDef]]:
    """Yield (source path, module, class, method) for every endpoint.

    An endpoint is a Resource subclass with an HTTP method. Matching on
    the method name alone would pull in any helper class with a ``get``
    accessor and then demand a ``swag_from`` on it.

    A class whose base is another *endpoint* is a different matter: it
    is an endpoint by inheritance, and skipping it would exempt it from
    every assertion here rather than merely omit it. Recorded in
    ``problems`` so that reads as the unhandled case it is.

    Two endpoint classes sharing a name is recorded for the same
    reason: ``derived_location()`` looks their routes up by bare name,
    so a collision gives each of them the other's path parameters.
    """
    seen: dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(api_dir, '*.py'))):
        tree = _parse(path)
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            methods = [n for n in cls.body if isinstance(n, ast.FunctionDef)
                       and n.name in HANDLER_METHODS]
            if not methods:
                continue

            bases = [ast.unparse(base) for base in cls.bases]
            if not any(base.endswith('Resource') for base in bases):
                if problems is not None and any(
                        base.endswith('Endpoint') for base in bases):
                    problems.append(
                        '%s subclasses an endpoint (%s) rather than Resource, '
                        'so its declarations are not audited'
                        % (cls.name, ', '.join(bases)))
                continue

            if cls.name in seen and problems is not None:
                problems.append(
                    '%s is defined twice (%s), so route lookups by class '
                    'name give each of them the other\'s path parameters'
                    % (cls.name, seen[cls.name] if seen[cls.name] == path
                       else '%s and %s' % (seen[cls.name], path)))
            seen.setdefault(cls.name, path)

            for fn in methods:
                yield path, tree, cls, fn


def declarations(fn: ast.FunctionDef, path: Optional[str] = None,
                 cls: Optional[str] = None) -> list[Declaration]:
    """The parameters a handler declares, as Declaration tuples.

    A declaration which cannot be read statically is returned with None
    in the offending field rather than skipped, so that a declaration
    this module cannot check fails the audit instead of silently
    escaping every assertion in it.
    """
    out = []
    for dec in fn.decorator_list:
        if 'swagger_helper' not in ast.unparse(dec):
            continue
        call = dec.args[0] if isinstance(dec, ast.Call) and dec.args else None
        if not (isinstance(call, ast.Call) and len(call.args) >= 3
                and isinstance(call.args[2], ast.List)):
            out.append(Declaration(path, cls, fn.name, None, None, None, None))
            continue
        for item in call.args[2].elts:
            # swagger_helper() destructures a fixed five elements, so a
            # tuple of any other length is malformed however readable
            # its parts are.
            if not (isinstance(item, ast.Tuple) and len(item.elts) == 5):
                out.append(
                    Declaration(path, cls, fn.name, None, None, None, None))
                continue
            out.append(Declaration(
                path, cls, fn.name, literal(item.elts[0]),
                literal(item.elts[1]), literal(item.elts[4]), item.elts[1]))
    return out


def documented(fn: ast.FunctionDef) -> bool:
    """Does this handler carry a swagger_helper declaration at all?

    Distinct from declaring parameters. Eight endpoints correctly
    declare an empty parameter list because they accept none, so
    "declares nothing" and "is absent from the published API" are
    different questions and only the second is a defect.
    """
    return any('swagger_helper' in ast.unparse(dec)
               for dec in fn.decorator_list)


def derived_location(name: str, fn: ast.FunctionDef, tree: ast.Module,
                     cls: ast.ClassDef, routes: dict[str, set[str]],
                     problems: Optional[list[str]] = None) -> str:
    """Where a parameter of this name actually arrives."""
    if name in routes.get(cls.name, set()):
        return 'path'
    if (name in query_parameters(fn, [cls, tree], problems)
            or name in request_args_parameters(fn)):
        return 'query'
    return 'body'


def audit(api_dir: str = API_DIR, app: Optional[str] = None
          ) -> tuple[list[tuple[Declaration, str]],
                     list[tuple[Declaration, None]], list[str]]:
    """Compare every declaration against the code that reads it.

    Returns (drifted, underivable, problems). The first two hold
    (Declaration, want) pairs, with ``want`` None for the underivable
    ones; ``problems`` holds input this module could not read.

    An empty ``drifted`` is the property both consumers care about: the
    fixer has nothing to rewrite and the audit test passes. An empty
    ``problems`` is what makes that meaningful, because a source which
    could not be read produces the same empty set as one with nothing
    in it, and the derivation then confidently returns a wrong answer.
    """
    problems: list[str] = []
    routes = route_parameters(
        app or os.path.join(api_dir, 'app.py'), problems)
    drifted = []
    underivable = []

    for path, tree, cls, fn in handlers(api_dir, problems):
        for declared in declarations(fn, path=path, cls=cls.name):
            if declared.location in UNDERIVABLE_LOCATIONS:
                underivable.append((declared, None))
                continue
            if declared.name is None or declared.location is None:
                # Unreadable, and so unfixable by the script -- which is
                # exactly why it has to be reported. Skipping it silently
                # let the fixer, and so the pre-commit hook, answer "0
                # locations would change" for a tree carrying a
                # declaration it could not parse. The audit test still
                # reports these per-field and in more detail.
                problems.append(
                    '%s.%s has a declaration this cannot read (its %s is '
                    'not a literal or an api_base constant)'
                    % (cls.name, fn.name,
                       'name' if declared.name is None else 'location'))
                continue
            want = derived_location(
                declared.name, fn, tree, cls, routes, problems)
            if declared.location != want:
                drifted.append((declared, want))

    return drifted, underivable, sorted(set(problems))
