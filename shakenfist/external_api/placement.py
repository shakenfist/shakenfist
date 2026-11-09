# Documentation state:
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist: no
#        - and link to OpenAPI docs: no
#        - and include examples: no
#   - Has complete CI coverage:
"""Ask whether a set of requested shapes would fit the cluster now.

A placement query is the scheduler's own answer to "could I create
these", given before anything is created. It walks the real filter,
affinity and ranking pipeline, charging each shape to the node it
would land on before the next shape is considered, so a set cannot be
told that two of its members fit in one node's worth of room.

Three properties make it a query rather than a create:

* It writes nothing. No instance row, no capacity counter, no claim.
  The charges it reasons about live in a snapshot built for the call
  and discarded with it.
* It publishes no events. The placement walk addresses its audit
  events to the objects its caller names, and this caller names none,
  so a speculative evaluation leaves no trail against anybody's
  instance.
* Its answer is advisory and racy by construction. The room it found
  can be gone before the caller's next request, and nothing here
  reserves any of it. That is why a set which does not fit is a 200
  carrying ``fits: false`` rather than a 507: the question was asked
  and answered, and a caller's error handling has to be able to tell
  "the cluster is full" from "the query failed". A 507 remains what a
  create gets.

The answer names the pipeline stage which refused rather than a bare
boolean, in the stage vocabulary the scheduler already reports --
``cpu_max_per_instance``, ``sufficient_idle_cpu``,
``sufficient_idle_memory``, ``sufficient_free_disk``,
``sufficient_idle_disk``, ``capacity_guard`` and the rest. Nothing
here mints a stage name: a prediction and a real refusal are compared
label for label by code which is already deployed, and a stage this
endpoint invented would be a silent incompatibility rather than a
compile error.
"""

from flasgger import swag_from
from shakenfist_utilities import api as sf_api

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist import scheduler
from shakenfist.external_api import base as api_base
from shakenfist.util.access_tokens import request_namespace


SCHEDULER = None


placement_post_example = """{
    "fits": true,
    "shapes": [
        {
            "node": "00000000-0000-4000-8000-000000000001",
            "candidates": [
                "00000000-0000-4000-8000-000000000001",
                "00000000-0000-4000-8000-000000000003"
            ]
        },
        {
            "node": "00000000-0000-4000-8000-000000000003",
            "candidates": [
                "00000000-0000-4000-8000-000000000003"
            ]
        }
    ]
}"""


def _integer(name, value):
    """One of a shape's integer figures, or an error response.

    Returns (value, error), exactly one of which is None. The same
    strictness the namespace claim limits apply to the same kind of
    figure, and for the same reason: bool is an int in Python, so a
    body saying ``"cpus": true`` would otherwise quietly ask about one
    vCPU.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None, sf_api.error(400, '%s is not an integer' % name)
    if value < 0:
        return None, sf_api.error(400, '%s cannot be negative' % name)
    return value, None


def _shape_disk_gb(label, shape):
    """How much disk one requested shape asks for, in gigabytes.

    ``disk`` is the summed form and ``disk_spec`` the list a create
    body carries; giving both is refused rather than reconciled,
    because either choice would silently discard half of what the
    caller asked for.

    The list form is summed by mariadb.disk_spec_virtual_gb(), which is
    the one implementation of "how big is this disk claim" and is what
    the create path's own capacity claim calls. A second sum here is
    exactly how a dry run and a create come to disagree about how large
    a request is.
    """
    disk = shape.get('disk')
    disk_spec = shape.get('disk_spec')

    if disk is not None and disk_spec is not None:
        return None, sf_api.error(
            400, '%s: disk and disk_spec are alternatives, not both' % label)

    if disk_spec is not None:
        if not isinstance(disk_spec, list):
            return None, sf_api.error(
                400, '%s: disk_spec is not a list of disks' % label)
        return mariadb.disk_spec_virtual_gb(disk_spec), None

    if disk is None:
        # A shape which asks for no disk is a legitimate question, and
        # the pipeline charges a disk of zero without complaint.
        return 0, None
    return _integer('%s: disk' % label, disk)


def _expand(shapes, namespace):
    """The request body as a list of PlacementShape, or an error response.

    Returns (shapes, error), exactly one of which is None.

    ``count`` is expanded here into that many identical shapes, because
    the placement core takes a flat list with no multiplicity: each
    copy is walked in its own right and sees the ones before it already
    sitting on their nodes, which is what makes a count of three ask
    for room for three rather than for one.

    That expansion is also why there is a bound. A short body can ask
    for a long walk, so the expanded total is capped and the refusal
    names the cap -- a caller which cannot see the bound cannot respect
    it.

    Every shape is evaluated in the caller's own namespace. That is not
    decoration: the affinity stages count the tags of a node's existing
    instances, and they only count instances in the same namespace,
    because crossing that boundary would let a caller learn what
    another tenant is running by watching where their own shapes
    refuse to land.
    """
    # Type before falsiness, so a body sending 0 or "" for shapes is
    # told what is wrong with it rather than that it supplied nothing.
    # None is the one falsy value which really does mean "not
    # supplied", and it is the handler's own default.
    if shapes is None:
        return None, sf_api.error(400, 'no shapes specified')
    if not isinstance(shapes, list):
        return None, sf_api.error(400, 'shapes is not a list of shapes')
    if not shapes:
        return None, sf_api.error(400, 'no shapes specified')

    requested = []
    total = 0
    for index, shape in enumerate(shapes):
        label = 'shape %d' % index
        if not isinstance(shape, dict):
            return None, sf_api.error(400, '%s is not an object' % label)

        unknown = set(shape) - set(api_base.SHAPESPEC_SCHEMA['properties'])
        if unknown:
            return None, sf_api.error(
                400, '%s has unknown keys %s'
                % (label, ', '.join(sorted(unknown))))

        for name in api_base.SHAPESPEC_SCHEMA['required']:
            # A null and an omission are one fact from here: the shape
            # did not say how large it is.
            if shape.get(name) is None:
                return None, sf_api.error(
                    400, '%s: no %s specified' % (label, name))

        cpus, err = _integer('%s: cpus' % label, shape['cpus'])
        if err:
            return None, err
        memory_mb, err = _integer('%s: memory' % label, shape['memory'])
        if err:
            return None, err
        disk_gb, err = _shape_disk_gb(label, shape)
        if err:
            return None, err

        affinity = shape.get('affinity')
        if affinity is None:
            affinity = {}
        # Checked by the same helper POST /instances checks the
        # affinity it is about to store with, and answered with that
        # helper's own words rather than words of this endpoint's, so
        # the two cannot disagree about either what a document may say
        # or what is wrong with one. The scheduler reads a *stored*
        # document leniently -- a mistyped hard constraint becomes no
        # constraint -- which is right for a document accepted long
        # ago and would be a lie here: a query which quietly dropped
        # the constraint would answer that a shape fits when the shape
        # the caller described was never asked about.
        err = api_base.validate_affinity_document(affinity)
        if err:
            return None, err

        count = shape.get('count')
        if count is None:
            count = 1
        else:
            count, err = _integer('%s: count' % label, count)
            if err:
                return None, err
            if count < 1:
                return None, sf_api.error(
                    400, '%s: count must be at least 1' % label)

        total += count
        if total > api_base.MAX_PLACEMENT_SHAPES:
            return None, sf_api.error(
                400, 'a placement query may ask about at most %d shapes, '
                'counting each shape\'s count'
                % api_base.MAX_PLACEMENT_SHAPES)

        requested.extend(
            [scheduler.PlacementShape(
                cpus=cpus, memory_mb=memory_mb, disk_gb=disk_gb,
                affinity=affinity, namespace=namespace,
                disk_spec=shape.get('disk_spec'))] * count)

    return requested, None


class PlacementEndpoint(api_base.Resource):
    @api_base.scope(verb='read')
    @swag_from(api_base.swagger_helper(
        'placement',
        'Ask whether a set of requested shapes would fit the cluster now. '
        'Nothing is created, nothing is reserved, and no capacity is held: '
        'the answer describes the cluster at the moment it was asked and '
        'the room it found may be gone by the next request. A set which '
        'does not fit is answered with a 200 carrying <code>fits: '
        'false</code> and the pipeline stage which refused, because the '
        'question was answered successfully. Per-node detail is '
        'administrative; every other caller is answered at set level.',
        [('shapes', 'body', 'arrayofshapespec',
          'The shapes to ask about, as a set. Every shape is placed in its '
          'own right and is charged to the node it would land on before the '
          'next is considered, so a set is never told that two of its '
          'members fit in one node\'s worth of room. At most %d shapes once '
          'each shape\'s count has been expanded.'
          % api_base.MAX_PLACEMENT_SHAPES, True)],
        [(200, 'Whether the set fits, in <code>fits</code>. A cluster '
               'administrator is also told, in <code>shapes</code> and in '
               'the order the shapes were asked about, which node each '
               'shape would be placed on in <code>node</code>, and that '
               'node followed by its fallbacks in '
               '<code>candidates</code> -- the chosen node is the head of '
               'that list, not absent from it. A set which does not fit '
               'carries no <code>shapes</code> at all -- placement is all '
               'or nothing -- and instead names the pipeline stage which '
               'emptied the candidate list in <code>stage</code> and the '
               'index of the shape it was walking in <code>shape</code>.',
          placement_post_example),
         (400, 'The request does not describe a set of shapes: a shape '
               'without a vCPU or memory figure, a shape giving both disk '
               'and disk_spec, or more shapes than the published bound '
               'allows.', None),
         (503, 'The database could not be reached, so the cluster\'s '
               'resources could not be read and the question could not '
               'be answered. Retry. A query answers a cluster whose '
               'capacity counters are unreadable but whose nodes are '
               'not, because a create in that state proceeds on the '
               'measured figures too and the point of the question is '
               'to meet the same answer a create would.', None)]))
    @api_base.log_token_use
    def post(self, shapes=None):
        global SCHEDULER

        requested, err = _expand(shapes, request_namespace())
        if err:
            return err

        if not SCHEDULER:
            SCHEDULER = scheduler.Scheduler()

        try:
            # related_objects=None is what keeps a speculative
            # evaluation out of the event log: the walk addresses its
            # audit events to the objects a caller names, and a caller
            # which names none publishes nothing.
            assignment = SCHEDULER.find_candidates_for_shapes(
                requested, related_objects=None)

        # One clause for both of the core's refusals.
        # AffinityConstraintUnsatisfiable is a subclass of
        # LowResourceException, and here the two become the same answer:
        # a stage name and no assignment at all. The create path has to
        # separate them because 507 and 409 mean different things to
        # something which was building an instance; a query is not.
        except exceptions.LowResourceException as e:
            # The stage is published exactly as the refusal carried it,
            # with nothing substituted for an empty one. A consumer
            # scoring a prediction against a real refusal compares the
            # two labels, so a label invented here would be one that
            # cannot appear on either side of that comparison -- worse
            # than an empty string, which is at least visibly absent.
            # Every refusal the pipeline raises carries a stage, and a
            # test over the raise sites is what keeps that true rather
            # than a fallback here.
            #
            # Both fields are read directly: SchedulerException declares
            # them, so they are always present on anything this clause
            # can catch.
            #
            # The shape index is whatever the walk attached to the
            # refusal, and is an index into the expanded request order:
            # a shape asked for with a count of three contributes three
            # consecutive indexes, so it lines up with the shapes array
            # a successful answer carries.
            return {
                'fits': False,
                'stage': e.stage,
                'shape': e.shape,
            }

        answer = {'fits': True}

        # The per-node half of the answer is administrative. A tenant
        # learning which node UUIDs exist, how much room each has and
        # which filter stage each failed is a cluster topology
        # disclosure no other endpoint gives a non-administrator, and
        # the set level verdict is the whole of what a tenant asked.
        if api_base.caller_has_admin_rights():
            answer['shapes'] = [
                {
                    # The head of each list is the node the shape was
                    # charged to, which is what makes the rest of the
                    # list fallbacks rather than alternatives: a caller
                    # which took a node further down would be left with
                    # an arithmetic in which every later shape was
                    # ranked against a placement that did not happen.
                    'node': assignment[index][0],
                    'candidates': assignment[index],
                }
                for index in range(len(requested))]

        return answer
