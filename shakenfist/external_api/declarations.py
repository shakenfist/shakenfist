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
import glob
import os
import re


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


Declaration = collections.namedtuple(
    'Declaration', ['path', 'cls', 'method', 'name', 'location',
                    'required', 'location_node'])


def _parse(path):
    with open(path) as f:
        return ast.parse(f.read())


def _base_constants():
    """Module-level string constants of base.py, by name.

    ``RAW_BODY_PARAMETER`` is referenced rather than spelled out in the
    one declaration that documents a raw request body, so resolving it
    is the difference between reading that declaration and skipping it.
    Read from source rather than imported, because importing base.py
    means importing flask.
    """
    out = {}
    for node in _parse(BASE).body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = value
    return out


CONSTANTS = _base_constants()


def literal(node):
    """A declaration element's value, or None if it is not static.

    An ``api_base.SOMETHING`` reference resolves to the constant's
    value; anything else which is not a literal is None, which every
    caller treats as "cannot be checked" rather than as a value.
    """
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except ValueError:
        return CONSTANTS.get(ast.unparse(node).split('.')[-1])


def route_parameters(app=APP):
    """Path parameter names per endpoint class, from the mounted routes.

    Werkzeug routes may name a converter, as in ``<path:label_name>`` or
    ``<int(min=1):x>``, so the parameter name is whatever follows the
    last colon. An earlier version of this matched only bare names and
    so silently skipped three LabelEndpoint declarations.
    """
    out = collections.defaultdict(set)
    for node in ast.walk(_parse(app)):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, 'attr', '') != 'add_resource':
            continue
        cls = ast.unparse(node.args[0]).split('.')[-1]
        for arg in node.args[1:]:
            route = literal(arg)
            if isinstance(route, str):
                out[cls] |= {segment.split(':')[-1]
                             for segment in re.findall(r'<([^>]+)>', route)}
    return out


def query_parameters(fn, scopes):
    """Names the handler parses from the query string with webargs.

    Read off the handler's own ``@use_kwargs`` decorator: its
    ``location`` keyword, and the schema its first argument names. The
    earlier version looked for any class-level assignment called
    ``get_args`` and applied it to every handler in the class, which
    described a weaker rule than the docstring claimed -- a schema bound
    at ``location='json'``, or a class with a webargs ``get`` beside a
    ``post`` declaring a same-named parameter, would both have been
    derived wrongly.
    """
    out = set()
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call) or not dec.args:
            continue
        if ast.unparse(dec.func).split('.')[-1] != 'use_kwargs':
            continue
        location = None
        for keyword in dec.keywords:
            if keyword.arg == 'location':
                location = literal(keyword.value)
        if location != 'query':
            continue
        out |= _schema_keys(scopes, ast.unparse(dec.args[0]))
    return out


def _schema_keys(scopes, name):
    """The keys of the dict assigned to ``name``, innermost scope first.

    The scopes are searched in order and the first one to define the
    name wins, rather than every definition being unioned together.
    Each scope contributes only its own assignments -- a module's are
    its top-level statements, not everything nested inside it -- so one
    endpoint class's ``get_args`` cannot leak into the derivation for
    another class in the same file. That leak was real: it made the
    fixer willing to rewrite a correct `body` declaration to `query`,
    and phase 3 would then have compiled a query-string fallback for a
    parameter which never arrives that way. Drift introduced by the
    machinery built to prevent drift.
    """
    for scope in scopes:
        out = set()
        for node in scope.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(ast.unparse(t) == name for t in node.targets):
                continue
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    value = literal(key)
                    if value is not None:
                        out.add(value)
        if out:
            return out
    return set()


def request_args_parameters(fn):
    """Names the handler reads straight out of flask.request.args.

    ``ClusterOperationsEndpoint.get`` accepts its target parameters as
    body keys, via the ``log_request`` merge, but falls back to
    ``flask.request.args.get()`` for each so a raw ``?target_...=`` GET
    keeps working -- the form AGENTS.md documents. A parameter read this
    way is a query parameter whatever else it also is.
    """
    out = set()
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


def _is_request_args(node):
    """Is this node ``request.args``, however ``request`` was imported?"""
    return (isinstance(node, ast.Attribute) and node.attr == 'args'
            and ast.unparse(node.value).split('.')[-1] == 'request')


def handler_kwargs(fn):
    """Every parameter a caller could populate, keyword-only included."""
    args = list(fn.args.args) + list(fn.args.kwonlyargs)
    return [a.arg for a in args
            if a.arg != 'self' and not a.arg.endswith(INJECTED_SUFFIX)]


def handlers(api_dir=API_DIR):
    """Yield (source path, module, class, method) for every endpoint.

    An endpoint is a Resource subclass with an HTTP method. Matching on
    the method name alone would pull in any helper class with a ``get``
    accessor, and then demand a ``swag_from`` on it.
    """
    for path in sorted(glob.glob(os.path.join(api_dir, '*.py'))):
        tree = _parse(path)
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if not any(ast.unparse(base).endswith('Resource')
                       for base in cls.bases):
                continue
            for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                if fn.name in HANDLER_METHODS:
                    yield path, tree, cls, fn


def declarations(fn, path=None, cls=None):
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


def documented(fn):
    """Does this handler carry a swagger_helper declaration at all?

    Distinct from declaring parameters. Eight endpoints correctly
    declare an empty parameter list because they accept none, so
    "declares nothing" and "is absent from the published API" are
    different questions and only the second is a defect.
    """
    return any('swagger_helper' in ast.unparse(dec)
               for dec in fn.decorator_list)


def derived_location(name, fn, tree, cls, routes):
    """Where a parameter of this name actually arrives."""
    if name in routes.get(cls.name, set()):
        return 'path'
    if (name in query_parameters(fn, [cls, tree])
            or name in request_args_parameters(fn)):
        return 'query'
    return 'body'


def audit(api_dir=API_DIR, app=None):
    """Compare every declaration against the code that reads it.

    Returns (drifted, underivable). Each entry is a (Declaration, want)
    pair; ``want`` is None for the underivable ones. An empty drifted
    list is the property both consumers care about: the fixer has
    nothing to rewrite, and the audit test passes.

    The directory is a parameter so the fixer's rewrite path can be
    exercised against a constructed tree. It defaults to this package.
    """
    routes = route_parameters(app or os.path.join(api_dir, 'app.py'))
    drifted = []
    underivable = []

    for path, tree, cls, fn in handlers(api_dir):
        for declared in declarations(fn, path=path, cls=cls.name):
            if declared.location in UNDERIVABLE_LOCATIONS:
                underivable.append((declared, None))
                continue
            if declared.name is None or declared.location is None:
                # Unreadable, and so unfixable by the script. The audit
                # test reports these separately and in more detail.
                continue
            want = derived_location(declared.name, fn, tree, cls, routes)
            if declared.location != want:
                drifted.append((declared, want))

    return drifted, underivable
