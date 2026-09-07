# Copyright 2019 Michael Still and contributors
"""Where each declared API parameter really comes from.

The ``swagger_helper()`` declarations on every endpoint say which
parameters exist, what type they are and where they arrive. Phase 3 of
docs/plans/PLAN-api-input-validation.md compiles them into request
validation, at which point a declaration that disagrees with its handler
stops being a documentation bug and starts rejecting valid requests. This
module is the single statement of what agreement means.

Five sources decide where a parameter comes from, in order:

* a name appearing in a route the class is mounted on is in the ``path``;
* a name in the schema of a ``@use_kwargs(..., location='query')`` on the
  handler is in the ``query``;
* a name the handler reads from ``flask.request.args`` is in the
  ``query``, even if it can also arrive in the body -- the published
  documentation and the query-string fallback phase 3 compiles must
  agree;
* a name one of the handler's decorators pops out of kwargs before the
  handler runs is in the ``body``. It needs a term of its own precisely
  because it reaches none of the other three and never appears in the
  handler's signature either, which is how the ref decorators' popped
  ``namespace`` went undeclared for years (#3739);
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
    # Explicit encoding, unlike the rest of the codebase: python source
    # is UTF-8 by definition (PEP 3120), but open() defaults to the
    # locale's encoding, and the pre-commit hook and mutation harness
    # run in whatever environment the developer has. Three of the files
    # this reads contain non-ASCII, so an ASCII locale crashed the
    # whole audit here.
    with open(path, encoding='utf-8') as f:
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

    A schema bound at ``'json_or_query'`` -- the custom location
    base.py registers so a query parameter may also arrive in the JSON
    body (issue 3629, decision D6's fallback) -- is a query schema too,
    as is one bound at a tuple of locations naming ``'query'``. The
    outstanding-operations endpoints bind their ``all`` parameter at
    ``'json_or_query'``; reading it as "not query" would send the fixer
    to rewrite the very declarations the fix made true.
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
        elif location not in ('query', 'json_or_query'):
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


def request_args_parameters(fn: ast.FunctionDef,
                            problems: Optional[list[str]] = None) -> set[str]:
    """Names the handler reads straight out of flask.request.args.

    ``ClusterOperationsEndpoint.get`` accepts its target parameters as
    body keys, via the ``log_request`` merge, but falls back to
    ``flask.request.args.get()`` for each so a raw ``?target_...=`` GET
    keeps working -- the form ``docs/developer_guide/writing_an_endpoint.md``
    documents. A parameter read this way is a query parameter whatever
    else it also is.

    Only two read forms are recognised: a ``.get()`` call with a
    literal key, and a literal subscript. Anything else touching
    ``request.args`` -- a non-literal key, ``.getlist()``,
    ``.to_dict()``, iterating the whole MultiDict -- reads query
    parameters this walk cannot name, so it lands in ``problems``
    rather than being silently dropped. Without that, the parameter
    derives to ``body`` with an empty problems list: the confident
    wrong answer the rest of this module was rewritten to refuse.
    """
    out: set[str] = set()
    recognised: set[int] = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get'
                and _is_request_args(node.func.value)
                and node.args):
            recognised.add(id(node.func.value))
            key = literal(node.args[0])
        elif isinstance(node, ast.Subscript) and _is_request_args(node.value):
            recognised.add(id(node.value))
            key = literal(node.slice)
        else:
            continue
        if key is None:
            if problems is not None:
                problems.append(
                    '%s reads flask.request.args with a key which is not a '
                    'literal, so a query parameter is missing from the '
                    'derivation' % fn.name)
        else:
            out.add(key)

    for node in ast.walk(fn):
        if (isinstance(node, ast.Attribute) and _is_request_args(node)
                and id(node) not in recognised and problems is not None):
            problems.append(
                '%s touches flask.request.args other than via .get() with a '
                'literal key or a literal subscript, so query parameters '
                'read that way are missing from the derivation' % fn.name)
    return out


def _is_request_args(node: ast.AST) -> bool:
    """Is this node ``request.args``, however ``request`` was imported?"""
    return (isinstance(node, ast.Attribute) and node.attr == 'args'
            and ast.unparse(node.value).split('.')[-1] == 'request')


def handler_kwargs(fn: ast.FunctionDef,
                   problems: Optional[list[str]] = None) -> list[str]:
    """Every parameter a caller could populate, keyword-only included.

    A variadic handler defeats the enumeration: log_request merges the
    whole JSON body into the handler's kwargs, so ``**kwargs`` accepts
    arbitrary undeclared names while this list stays near-empty and an
    assertion iterating it passes vacuously (issue 3642). No handler in
    the tree is variadic today; recorded as a problem so the first one
    fails the audit rather than silently exempting itself.
    """
    if problems is not None:
        if fn.args.vararg is not None:
            problems.append(
                '%s is variadic (*%s), so the parameters it accepts '
                'cannot be enumerated' % (fn.name, fn.args.vararg.arg))
        if fn.args.kwarg is not None:
            problems.append(
                '%s is variadic (**%s), so the parameters it accepts '
                'cannot be enumerated' % (fn.name, fn.args.kwarg.arg))
    args = list(fn.args.args) + list(fn.args.kwonlyargs)
    return [a.arg for a in args
            if a.arg != 'self' and not a.arg.endswith(INJECTED_SUFFIX)]


def package_functions(api_dir: str = API_DIR
                      ) -> dict[str, list[ast.FunctionDef]]:
    """Module level functions of the API package, by bare name.

    The index decorator_kwargs() resolves names through. Keyed on the
    bare name because that is all a decoration site carries: the ref
    decorators are written ``@api_base.arg_is_instance_ref`` in one
    module and ``@arg_is_artifact_ref`` in another, and both name the
    same kind of thing. A name defined in two modules keeps both
    definitions, so the ambiguity is visible to the caller rather than
    resolved by whichever file sorted first.

    Only module level definitions are indexed. A decorator defined
    inside a class or a function is not something a decoration site in
    another module could name, and treating one as a match would
    resolve a name to a function that is not what ran.
    """
    out: dict[str, list[ast.FunctionDef]] = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(api_dir, '*.py'))):
        for node in _parse(path).body:
            if isinstance(node, ast.FunctionDef):
                out[node.name].append(node)
    return out


def _bare_name(node: ast.expr) -> Optional[str]:
    """The last dotted component of a Name or Attribute, else None."""
    if isinstance(node, (ast.Name, ast.Attribute)):
        return ast.unparse(node).split('.')[-1]
    return None


def _kwargs_dicts(fn: ast.FunctionDef) -> set[str]:
    """Names bound as ``**kwargs`` anywhere inside this function.

    A decorator's pops happen in its wrapper, not in the decorator
    itself, and they happen to the wrapper's ``**kwargs`` -- the dict
    flask's dispatch and log_request's body merge fill in. Collecting
    the names first is what keeps ``some_local_dict.pop('x')`` from
    reading as a consumed request parameter.
    """
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)) and node.args.kwarg is not None:
            out.add(node.args.kwarg.arg)
    return out


def _consumed_kwargs(fn: ast.FunctionDef,
                     functions: dict[str, list[ast.FunctionDef]],
                     problems: Optional[list[str]],
                     seen: set[str]) -> set[str]:
    """Request parameters this decorator removes from kwargs.

    Two forms are recognised, both with a literal key:
    ``kwargs.pop('name', ...)`` and ``del kwargs['name']``. A key which
    is not a literal is reported: the parameter is consumed either way,
    and answering "this decorator consumes nothing" would leave it
    undeclarable and unenforceable, which is the defect this whole term
    exists to catch arriving one level down.

    Delegation is followed through a *top level* ``return
    some_function(...)``, which is how ``arg_is_artifact_ref`` and
    ``arg_is_visible_artifact_ref`` are written -- both are one liners
    returning ``_resolve_artifact_ref(func, widen=...)``, and the pop
    lives there. Only the top level of the body, because the wrapper's
    own ``return func(*args, **kwargs)`` is a call to a parameter and
    following it would mean chasing the handler itself.

    A delegation target this cannot resolve is a problem, not an
    absence: the decorator's real body is somewhere else, and the empty
    set returned for it is indistinguishable from a decorator which
    genuinely consumes nothing.
    """
    out: set[str] = set()
    dicts = _kwargs_dicts(fn)

    for node in ast.walk(fn):
        key: Optional[ast.expr] = None
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'pop'
                and _bare_name(node.func.value) in dicts
                and node.args):
            key = node.args[0]
        elif (isinstance(node, ast.Delete) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Subscript)
              and _bare_name(node.targets[0].value) in dicts):
            key = node.targets[0].slice
        else:
            continue

        name = literal(key)
        if isinstance(name, str):
            out.add(name)
        elif problems is not None:
            problems.append(
                '%s removes a kwarg named by something this cannot read '
                '(%s), so a parameter it consumes is missing from the '
                'derivation' % (fn.name, ast.unparse(key)))

    for stmt in fn.body:
        if not isinstance(stmt, ast.Return) or not isinstance(stmt.value,
                                                              ast.Call):
            continue
        target = _bare_name(stmt.value.func)
        candidates = functions.get(target or '', [])
        if target is None or not candidates or len(candidates) > 1:
            if problems is not None:
                problems.append(
                    '%s delegates to %s, which this cannot resolve to a '
                    'single module level function, so the kwargs it '
                    'consumes are missing from the derivation. If it '
                    'consumes no request parameter, restructure the return '
                    'so this can see that (return the wrapper, or apply '
                    'functools.wraps as a decorator on it); otherwise move '
                    'the target to module level in %s'
                    % (fn.name, ast.unparse(stmt.value.func),
                       os.path.basename(API_DIR)))
            continue
        if target in seen:
            continue
        out |= _consumed_kwargs(
            candidates[0], functions, problems, seen | {target})

    return out


def decorator_kwargs(fn: ast.FunctionDef,
                     functions: dict[str, list[ast.FunctionDef]],
                     problems: Optional[list[str]] = None) -> set[str]:
    """Request parameters the handler's decorators consume for it.

    ``arg_is_instance_ref``, ``arg_is_network_ref`` and the two
    artifact ref decorators all ``kwargs.pop('namespace', None)`` and
    resolve the lookup with it. The parameter is functional -- the
    official client sends it -- but it is in none of the other three
    sources: it is not a route segment, not a webargs key and not a
    flask.request.args read, and it never reaches the handler's
    signature either, because the decorator took it. Without this term
    the derivation has nothing to say about it, so it was declared
    nowhere and phase 3's warn window found it as an
    ``unknown-parameter`` finding against working callers (#3739).

    A decorator name this cannot find among the package's module level
    functions is skipped in silence, and that is deliberate rather than
    an oversight of the "absence must not look like success" rule
    every other source here follows. Every handler in the tree carries
    several decorators which are not package functions at all --
    ``swag_from`` and ``use_kwargs`` are imported from third party
    libraries -- so reporting an unfound name would report most of the
    API and mean nothing. "Unresolvable" here means the narrower and
    more useful thing: the function *was* found and something inside it
    could not be read. That is what lands in ``problems``.

    The gap this leaves is a decorator defined outside this package
    which pops a kwarg. There is none today, every ref decorator lives
    in base.py or artifact.py beside the endpoints they decorate, and
    the alternative -- resolving imports across the whole tree from
    source -- buys a check against a shape which has never existed.
    """
    out: set[str] = set()
    for dec in fn.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        name = _bare_name(node)
        candidates = functions.get(name or '', [])
        if not candidates:
            continue
        if len(candidates) > 1:
            if problems is not None:
                problems.append(
                    '%s is decorated with %s, which is defined more than '
                    'once in this package, so the kwargs it consumes cannot '
                    'be told apart' % (fn.name, name))
            continue
        out |= _consumed_kwargs(
            candidates[0], functions, problems, {name or ''})
    return out


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
            # swagger_helper() destructures five fixed elements plus an
            # optional constraints dictionary, so a tuple of any other
            # length is malformed however readable its parts are.
            if not (isinstance(item, ast.Tuple) and len(item.elts) in (5, 6)):
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
    """Where a parameter of this name actually arrives.

    Called for the names a handler declares and, since #3739, for the
    names its decorators consume as well -- see ``audit()``. A consumed
    name takes no branch of its own: it arrives in the JSON body that
    ``log_request`` merges into kwargs, which is the fallback below, and
    a consumed name which is *also* a route segment or a webargs query
    key really does come from there instead. So the answer for a
    consumed name is derived by exactly the rules below rather than
    asserted by the caller.
    """
    # Every source is consulted before answering, rather than
    # short-circuiting on the first hit, so that a problem in a later
    # source is collected even for a name an earlier one resolved.
    #
    # The route check used to return ahead of these two, which made the
    # rule true of the query pair and false of the path case: a handler
    # whose declared parameters are all path parameters could read
    # flask.request.args with a key this cannot name and the audit would
    # report the tree clean, because the only call which would have
    # noticed returned before making it. That is the confident wrong
    # answer request_args_parameters() exists to refuse, reintroduced one
    # level up. Found by the generated cross product rather than by the
    # tree, which contains no handler of that shape.
    in_query = name in query_parameters(fn, [cls, tree], problems)
    in_args = name in request_args_parameters(fn, problems)
    if name in routes.get(cls.name, set()):
        return 'path'
    if in_query or in_args:
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

    A parameter a decorator consumes and nothing declares lands in
    ``problems`` too. It is not drift -- there is no location literal to
    rewrite, so the fixer cannot correct it and must refuse the tree
    instead -- and it is the one defect in here which reaches callers
    rather than readers: an undeclared functional parameter is a 400 for
    everyone using it the moment validation enforces (#3739).
    """
    problems: list[str] = []
    routes = route_parameters(
        app or os.path.join(api_dir, 'app.py'), problems)
    functions = package_functions(api_dir)
    drifted = []
    underivable = []

    for path, tree, cls, fn in handlers(api_dir, problems):
        # Called for its problems, not its answer: a variadic handler
        # accepts names no enumeration can produce, and the fixer's
        # promise to refuse to derive from input it could not read is
        # only complete if that arrives through audit() like every
        # other problem. Without this the guard existed only in the
        # unit test, so the pre-commit hook would still rewrite a tree
        # containing one.
        handler_kwargs(fn, problems)
        consumed = decorator_kwargs(fn, functions, problems)
        declared_names = set()

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
            declared_names.add(declared.name)
            want = derived_location(
                declared.name, fn, tree, cls, routes, problems)
            if declared.location != want:
                drifted.append((declared, want))

        # Gated on carrying a declaration at all, like
        # test_accepted_parameters_are_declared: a handler absent from
        # the published API declares nothing on purpose, and demanding
        # a parameter of it would be demanding it be published.
        if documented(fn):
            for name in sorted(consumed - declared_names):
                problems.append(
                    '%s.%s has a decorator which consumes %r before the '
                    'handler runs, so a caller can send it, but nothing '
                    'declares it; declare it in the %s. There is no '
                    'UNDECLARED_BY_DESIGN exemption for a '
                    'decorator-consumed parameter: declare it or stop '
                    'consuming it'
                    % (cls.name, fn.name, name,
                       derived_location(name, fn, tree, cls, routes,
                                        problems)))

    return drifted, underivable, sorted(set(problems))
