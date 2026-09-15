# Copyright 2019 Michael Still and contributors

"""The compilation of declarations into request schemas.

Phase 3 PR 2. Nothing validates yet, so these assertions are about
whether the compiled schemas *describe* what the declarations say --
which is the property phase 4 turns into rejections, and so the last
point at which a mistake is cheap.
"""

from unittest import mock

import marshmallow
from marshmallow import fields

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.external_api import validation
from shakenfist.tests import base
from shakenfist.tests.external_api.test_parameter_declarations import (
    ANY_TOKEN_DECLARATIONS, UNDOCUMENTED_BY_DESIGN)

# The fourteen metadata endpoints, without the (always 'value')
# parameter name ANY_TOKEN_DECLARATIONS also carries -- this module
# indexes the compiled registry by (cls, method) alone.
ANY_TOKEN_ENDPOINTS = sorted({(cls, method)
                             for (cls, method, _) in ANY_TOKEN_DECLARATIONS})


def _every_compiled_field(field):
    """A compiled field and everything nested inside it.

    A property of "every compiled field" has to be asserted at every
    depth or it is a property of the top level wearing a broader name,
    and phase 7 gave the compiler two ways down: a list's `inner` and a
    nested schema's `fields`. The nested arm reaches nothing today,
    since no declaration carries a `properties` block until step 4 --
    which is exactly why it is written now rather than when the first
    one lands.
    """
    yield field
    inner = getattr(field, 'inner', None)
    if inner is not None:
        yield from _every_compiled_field(inner)
    if isinstance(field, fields.Nested):
        for nested in field.schema.fields.values():
            yield from _every_compiled_field(nested)


class ValidationCompilerTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.saved_node_uuid = config.NODE_UUID
        config.NODE_UUID = 'test-node-uuid'
        self.addCleanup(self._restore_node_uuid)
        self.registry = validation.build_registry(external_api.app)

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def test_every_documented_handler_compiles(self):
        """A mounted handler resolves to a schema, or is exempt by name.

        The absence of a schema and the presence of an empty one are
        different things, and only one of them is allowed to be silent.
        Without this, a route mounted without a declaration would simply
        not be validated -- absence indistinguishable from success,
        which is the rule the audit was rewritten around.
        """
        mounted = set()
        for view in external_api.app.view_functions.values():
            cls = getattr(view, 'view_class', None)
            if cls is None or not issubclass(cls, api_base.Resource):
                continue
            for method in ('get', 'post', 'put', 'delete', 'patch'):
                if getattr(cls, method, None) is not None:
                    mounted.add((cls.__name__, method))

        uncompiled = mounted - set(self.registry)
        self.assertEqual(
            UNDOCUMENTED_BY_DESIGN, uncompiled,
            'every mounted handler must compile to a schema or be listed '
            'in UNDOCUMENTED_BY_DESIGN. Uncompiled and unlisted: %s'
            % sorted(uncompiled - UNDOCUMENTED_BY_DESIGN))

    def test_compiled_names_match_the_declared_names(self):
        """The runtime compilation agrees with the static audit.

        Two independent readings of the same declarations: this one
        through swagger_helper() and flasgger at import time, the other
        by parsing the source. They are the inputs to phase 4's
        enforcement and to the fixer respectively, so a disagreement
        between them is a parameter which is validated but not audited,
        or audited but not validated.
        """
        for path, tree, cls, fn in declarations.handlers():
            key = (cls.name, fn.name)
            if key not in self.registry:
                continue
            declared = {d.name for d in declarations.declarations(fn)}
            # The raw body marker names the whole body rather than a
            # parameter within it, and compiles to a flag. Discarded
            # only where the compiler actually treated it as one:
            # RAW_BODY_PARAMETER is the literal string 'body', so an
            # unconditional discard would also hide an ordinary
            # parameter of that name failing to compile.
            if self.registry[key].raw_body:
                declared.discard(api_base.RAW_BODY_PARAMETER)

            self.assertEqual(
                declared, self.registry[key].names,
                '%s.%s: the compiled parameter names differ from the '
                'declared ones' % key)

    def test_the_raw_body_is_not_compiled_as_json(self):
        """An upload body is bytes. Parsing it as JSON would reject
        every upload the moment phase 4 enforces."""
        compiled = self.registry[('UploadDataEndpoint', 'post')]

        self.assertTrue(compiled.raw_body)
        self.assertIsNone(compiled.body)

    def test_only_published_bounds_and_formats_become_validators(self):
        """A validator exists exactly where the specification says one should.

        Until phase 6's step 4 this test asserted the opposite for
        `format`: every semantic token rendered a format string and
        compiled to a bare fields.String, which is finding F3 of
        PLAN-api-input-validation-phase-06-required.md and the reason
        issue 3269 survived four years. Step 4 gave five of those
        format strings a validator apiece (decision D33), so the rule
        is now that a field carries validators if and only if the
        published schema carries a bound *or* a format `_FORMATS`
        knows.

        Derived from the published specification rather than from a
        list of examples, which would go stale as tokens are retyped.
        """
        # netblock still has no pattern -- publishing an IPv4 CIDR
        # regex would describe the API as narrower than
        # NetworksEndpoint.post() is, since it parses with
        # ipaddress.ip_network() and takes IPv6 too. What it has
        # instead is a validator which calls that same function.
        netblock = self.registry[('NetworksEndpoint', 'post')].body
        self.assertIsInstance(netblock.fields['netblock'], fields.String)
        self.assertNotIn(
            'pattern',
            api_base.ARGTYPES['netblock'],
            'netblock must stay pattern-free; see the comment on it in '
            'base.py')
        self.assertEqual(
            [validation._format_netblock],
            list(netblock.fields['netblock'].validators))

        constrained = 0
        formatted = 0
        format_sites = []
        for view in external_api.app.view_functions.values():
            cls = getattr(view, 'view_class', None)
            if cls is None or not issubclass(cls, api_base.Resource):
                continue
            for method in ('get', 'post', 'put', 'delete', 'patch'):
                handler = getattr(cls, method, None)
                specs = getattr(handler, 'specs_dict', None)
                if specs is None:
                    continue
                compiled = self.registry[(cls.__name__, method)]
                for parameter in specs['parameters']:
                    if parameter.get('in') == 'body':
                        published = parameter.get(
                            'schema', {}).get('properties', {})
                        schema = compiled.body
                    elif parameter.get('in') == 'query':
                        published = {parameter['name']: parameter}
                        schema = compiled.query
                    else:
                        continue
                    for name, spec in published.items():
                        bounded = any(k in spec for k in
                                      ('minimum', 'maximum', 'pattern'))
                        semantic = (spec.get('type') == 'string'
                                    and spec.get('format')
                                    in validation._FORMATS)
                        constrained += bool(bounded)
                        formatted += bool(semantic)
                        if semantic:
                            format_sites.append(
                                (cls.__name__, method, name,
                                 spec.get('format')))
                        self.assertEqual(
                            bounded or semantic,
                            bool(schema.fields[name].validators),
                            '%s.%s %s: published bound %s, published '
                            'format %s, compiled validators %s'
                            % (cls.__name__, method, name, bounded,
                               spec.get('format'),
                               schema.fields[name].validators))

        # The rule above is vacuous if nothing is bounded, so the count
        # is pinned. Twenty nine, from two sources which both have to
        # work: sixteen carrying an explicit constraints element (the
        # seven events `limit` caps, the two `key_ttl` ranges, the two
        # namespace claim `expires_in_seconds` minimums, and the five
        # agent operation timing parameters -- `deadline_seconds` on
        # all three creating endpoints plus `progress_timeout_seconds`
        # on get and put), and thirteen whose bound comes from the type
        # token alone -- `minimum: 0` rendered by `unsignedinteger` on
        # max_versions, offset, blob limit, cpus, memory and the six
        # namespace claim limits. A change in either is meant to fail
        # here and be re-counted deliberately.
        #
        # Seven events endpoints rather than five as of
        # scheduler-reservations phase 7, which added the namespace and
        # namespace claim ones.
        #
        # The agent timing five are worth a note: their `minimum: 0` is
        # backed by a 400 in api_base.agent_operation_timing() rather
        # than by the coercion the events `limit` cap uses, so the
        # published bound is honest whatever API_VALIDATION_MODE is set
        # to. They compile into validators here as well, which is
        # belt and braces rather than the mechanism.
        self.assertEqual(29, constrained)

        # And the thirteen semantic format declarations, named rather
        # than merely counted: this is F3's table, which is the list
        # each validator's width was established against. A new one
        # arriving here has not had that reading done for it, so it
        # fails until someone does it and adds the row.
        self.assertEqual(
            [
                ('ArtifactUploadEndpoint', 'post', 'blob_uuid', 'uuid'),
                ('ArtifactUploadEndpoint', 'post', 'source_url', 'url'),
                ('ArtifactUploadEndpoint', 'post', 'upload_uuid', 'uuid'),
                ('ArtifactsEndpoint', 'post', 'url', 'url'),
                ('AuthIssuerEndpoint', 'put', 'jwks_uri', 'url'),
                ('AuthIssuersEndpoint', 'post', 'jwks_uri', 'url'),
                ('ClusterOperationsEndpoint', 'get', 'target_uuid', 'uuid'),
                ('InstanceAgentPutEndpoint', 'post', 'blob_uuid', 'uuid'),
                ('InstancesEndpoint', 'post', 'nvram_template', 'url'),
                ('InstancesEndpoint', 'post', 'user_data', 'byte'),
                ('LabelEndpoint', 'post', 'blob_uuid', 'uuid'),
                ('NetworkDNSAddressEndpoint', 'post', 'value',
                 'an IPv4 address as a string'),
                ('NetworksEndpoint', 'post', 'netblock',
                 'a CIDR netblock'),
            ],
            sorted(format_sites))
        self.assertEqual(13, formatted)

    def test_the_prose_formats_still_validate_nothing(self):
        """The tokens step 4 deliberately left alone.

        `namespace` and `node` resolve against the database in a ref
        decorator which answers 404, which is a stronger check than a
        format one and already runs; `uuidorname` is ambiguous by
        construction. Adding any of them to `_FORMATS` would be a
        narrowing, so their absence is asserted rather than assumed.
        """
        for token in ('namespace', 'node', 'uuidorname', 'string',
                      'bearer', 'binary'):
            self.assertNotIn(
                api_base.ARGTYPES[token].get('format'), validation._FORMATS,
                '%s is a prose format and must not compile to a '
                'validator' % token)

        # macaddr is the other deliberate absence, and for the opposite
        # reason: it is already validated, by the anchored pattern
        # ARGTYPES gives it. A _FORMATS entry would be a second
        # definition of one format.
        self.assertIn('pattern', api_base.ARGTYPES['macaddr'])
        self.assertNotIn(
            api_base.ARGTYPES['macaddr']['format'], validation._FORMATS)

    def test_published_bounds_compile_into_validators(self):
        """The other direction: a bound which *is* declared must arrive.

        Phase 2 published these into the specification so callers could
        see them. If they did not compile, phase 4 would enforce a
        contract looser than the one it publishes.
        """
        limit = self.registry[('BlobEventsEndpoint', 'get')].body.fields['limit']
        ranges = [v for v in limit.validators
                  if isinstance(v, marshmallow.validate.Range)]

        self.assertEqual(1, len(ranges))
        self.assertEqual(1, ranges[0].min)
        self.assertEqual(1000, ranges[0].max)

    def test_required_is_recorded_but_not_enforced(self):
        """`required` is metadata in this phase.

        `mode` on the agent-put endpoint is declared required while
        omitting it has always been accepted, so compiling required-ness
        into a constraint would break working clients the moment phase 4
        enforced. Phase 6 decides what to do about it; warn-only exists
        to give that decision numbers.
        """
        compiled = self.registry[('InstanceAgentPutEndpoint', 'post')]

        self.assertIn('mode', compiled.required_names)
        self.assertFalse(compiled.body.fields['mode'].required)

        for key, endpoint in self.registry.items():
            for schema in (endpoint.body, endpoint.query):
                if schema is None:
                    continue
                required = [n for n, f in schema.fields.items() if f.required]
                self.assertEqual(
                    [], required,
                    '%s.%s compiled a required field, which would reject a '
                    'request phase 3 must not reject' % key)

    def test_every_field_accepts_null(self):
        """A JSON null reaches the handler as None today.

        Several handlers treat that as "not supplied". Rejecting it
        would be a rule this module invented rather than one any
        declaration states, and in warn-only it would fill the log with
        findings that are artefacts of the compiler.
        """
        for key, endpoint in self.registry.items():
            for schema in (endpoint.body, endpoint.query):
                if schema is None:
                    continue
                rejects = [n for n, f in schema.fields.items()
                           if not f.allow_none]
                self.assertEqual([], rejects, '%s.%s' % key)

    def test_structured_parameters_compile_to_structures(self):
        """The compiler and the specification pin cannot disagree.

        test_openapi_spec.STRUCTURED_PARAMETERS pins what the published
        specification says about the parameters carrying a structure or
        a bound. This compiles from that same rendered specification, so
        the two agreeing is close to tautological -- which is the point.
        It is asserted anyway because the alternative design, mapping
        the type tokens a second time here, would have made them
        independent and therefore able to drift, and that is exactly the
        defect class phase 2 hit twice.
        """
        instance_create = self.registry[('InstancesEndpoint', 'post')].body

        self.assertIsInstance(instance_create.fields['metadata'], fields.Dict)
        self.assertIsInstance(instance_create.fields['disk'], fields.List)
        self.assertIsInstance(
            instance_create.fields['disk'].inner, fields.Dict)
        self.assertIsInstance(instance_create.fields['cpus'], fields.Integer)

    def test_every_compiled_integer_refuses_a_fractional_number(self):
        """Decision D46, over the whole registry rather than one field.

        _SCALARS is the only place an integer field is constructed, so
        this is close to tautological today -- which is the point, the
        same argument the test above makes about the structured
        parameters. It is asserted anyway because D46 is a property of
        the published API ("no compiled integer claims 1.5 is an
        integer") rather than of one line of the compiler, so a future
        branch of _field() which built an integer some other way has to
        fail here.

        The population is pinned as non-empty for the usual reason: the
        rule is vacuous if nothing compiles to an integer at all.
        """
        integers = []
        for key, endpoint in self.registry.items():
            for schema in (endpoint.body, endpoint.query):
                if schema is None:
                    continue
                for name, field in schema.fields.items():
                    for inner in _every_compiled_field(field):
                        if isinstance(inner, fields.Integer):
                            integers.append(('%s.%s' % key, name, inner))

        self.assertNotEqual([], integers)
        # Asserted by asking the field rather than by checking its
        # class: the class is how it is done today, the refusal is what
        # D46 actually promises.
        accepted = []
        for where, name, field in integers:
            try:
                field.deserialize(1.5)
                accepted.append((where, name))
            except marshmallow.ValidationError:
                pass
        self.assertEqual([], accepted)

    def test_any_accepts_every_json_shape(self):
        """'any' (D15) compiles to fields.Raw, the same fallback the
        compiler already uses for a type token it does not recognise --
        phase 3's third review round made that path drop published
        bounds, so a Raw field carrying none is already the supported
        case. Proved at every one of the fourteen metadata `value`
        sites, not just one, because a future compiler change could
        special-case the token by name and treat the others
        differently."""
        for (cls, method) in ANY_TOKEN_ENDPOINTS:
            value = self.registry[(cls, method)].body.fields['value']
            with self.subTest(cls=cls, method=method):
                self.assertIsInstance(value, fields.Raw)
                self.assertEqual({'a': 1}, value.deserialize({'a': 1}))
                self.assertEqual([1, 2, 3], value.deserialize([1, 2, 3]))
                self.assertEqual('a string', value.deserialize('a string'))
                self.assertIsNone(value.deserialize(None))

    def test_any_is_not_compiled_through_the_unrecognised_type_path(self):
        """'any' is deliberate, so it must not use the fallback.

        Both paths return fields.Raw, so the resulting *field* cannot
        tell them apart -- only the warning can, and the warning is the
        point. The fallback shouts "unrecognised parameter type in the
        published specification", which is a real signal: a token the
        vocabulary defines and the compiler does not is a compiler bug.
        While 'any' took that path, sf-api logged fourteen of those at
        every start and a genuinely unrecognised token was
        indistinguishable from the expected noise.

        Asserted over a whole registry build rather than one call,
        because the property wanted is that a correct tree compiles
        silently -- which also fails the day a real unrecognised token
        arrives, and that is a failure worth having.
        """
        with mock.patch.object(validation, 'LOG') as mock_log:
            validation.build_registry(external_api.app)

        self.assertEqual(
            [], mock_log.with_fields.return_value.warning.call_args_list,
            'compiling the published specification warned. Either a type '
            'token really is unrecognised, or a deliberate one has fallen '
            'back into the unrecognised path the way `any` once did.')

    def test_an_unrecognised_token_still_warns(self):
        """The mutation check on the test above.

        A LOG patched over the whole build would also record nothing if
        the warning had simply been deleted, so this proves the
        fallback is still there and still audible, and that the
        assertion above is looking in the place a warning would land.
        """
        with mock.patch.object(validation, 'LOG') as mock_log:
            field = validation._field({'type': 'widget'})

        self.assertIsInstance(field, fields.Raw)
        self.assertEqual(
            1, mock_log.with_fields.return_value.warning.call_count)

    def test_the_any_rendering_is_the_one_the_vocabulary_publishes(self):
        """The compiler recognises `any` by ARGTYPES' own rendering.

        The recognition is a comparison against a rendered `format`
        string, which is a shape a hand-written duplicate would drift
        from silently -- the compiled schema would then say something
        the published specification does not. base.py builds the token
        from validation.ANY_VALUE_FORMAT for that reason, and this
        holds it there.
        """
        self.assertEqual(
            {'format': validation.ANY_VALUE_FORMAT},
            api_base.ARGTYPES['any'])
        self.assertNotIn('type', api_base.ARGTYPES['any'])

    def test_console_length_keeps_its_sentinel(self):
        """-1 means "the whole log", and the functional suite sends it.

        The regression this guards was shipped once already: phase 2
        retyped this to unsignedinteger, publishing minimum 0 over a
        value get_console_data() special-cases. Here it would compile
        into a rejection rather than merely a wrong line of
        documentation.
        """
        length = self.registry[
            ('InstanceConsoleDataEndpoint', 'get')].body.fields['length']

        self.assertEqual([], list(length.validators))
        self.assertEqual(-1, length.deserialize(-1))


class NestedMessageFlatteningTestCase(base.ShakenFistTestCase):
    """A container's element errors must name the element, not a dict.

    marshmallow answers validate() with a list of strings when a field
    is wrong and a dict keyed by index when a container's *elements*
    are wrong. Phase 4's enforce flip turned the finding detail from a
    log line into the caller's error message, so rendering that dict
    with str() would answer `disk: {0: ['Not a valid mapping type.']}`.
    """

    class _Schema(marshmallow.Schema):
        disk = fields.List(fields.Dict())
        tags = fields.List(fields.String())
        name = fields.String()
        # The phase 7 shape: a list of structured elements, so an error
        # arrives two levels down rather than one.
        spec = fields.List(fields.Nested(
            marshmallow.Schema.from_dict({
                'size': fields.Integer(allow_none=True),
                'base': fields.String(allow_none=True)})(
                    unknown=marshmallow.RAISE),
            allow_none=True), allow_none=True)

    def _findings(self, supplied):
        schema = self._Schema(unknown=marshmallow.EXCLUDE)
        return [(f.parameter, f.detail)
                for f in validation._schema_findings(schema, supplied)]

    def test_a_bad_element_names_its_index(self):
        self.assertEqual(
            [('disk[0]', 'Not a valid mapping type.')],
            self._findings({'disk': ['not a dict']}))

    def test_every_bad_element_is_reported_in_order(self):
        self.assertEqual(
            [('disk[0]', 'Not a valid mapping type.'),
             ('disk[1]', 'Not a valid mapping type.')],
            self._findings({'disk': ['one', 'two']}))

    def test_indices_are_ordered_numerically_not_lexically(self):
        """Eleven elements, because two cannot tell the difference.

        marshmallow keys a sequence error by integer index, and
        sorting those as strings gives 0, 1, 10, 2 -- so a caller whose
        third and eleventh disks are both malformed would be told about
        `disk[10]`, since enforcement answers with the first finding
        only. The count here is deliberately past ten.
        """
        self.assertEqual(
            ['disk[%d]' % index for index in range(12)],
            [parameter for parameter, _ in
             self._findings({'disk': ['x'] * 12})])

    def test_a_nested_failure_names_an_indexed_dotted_path(self):
        """Decision D48, and definition-of-done item 6.

        marshmallow reports a failure inside a nested schema as a
        nested dict -- {'spec': {1: {'size': ['Not a valid
        integer.']}}} -- and a finding naming only `spec` would make a
        list of six diskspecs impossible to debug. The recursion which
        produces `disk[0]` for an array element already produces this;
        the second index is deliberate, because a path built from the
        first element only would pass with a hard coded 0.
        """
        self.assertEqual(
            [('spec[1].size', 'Not a valid integer.')],
            self._findings({'spec': [{'size': 8}, {'size': 'eight'}]}))

    def test_an_unknown_nested_key_names_its_path_too(self):
        # unknown=RAISE keys its error by the offending key, so it
        # arrives through the same recursion and must read the same
        # way.
        self.assertEqual(
            [('spec[0].wombat', 'Unknown field.')],
            self._findings({'spec': [{'size': 8, 'wombat': 1}]}))

    def test_a_nested_finding_carries_the_nested_value_type(self):
        # The path _flatten_messages carries is what lets a finding
        # report the type of the value that actually failed rather than
        # the type of the array around it (decision D5).
        findings = validation._schema_findings(
            self._Schema(unknown=marshmallow.EXCLUDE),
            {'spec': [{'size': 8}, {'size': ['a', 'list']}]})

        self.assertEqual(
            [('spec[1].size', 'list')],
            [(f.parameter, f.value_type) for f in findings])

    def test_the_findings_one_container_can_produce_are_bounded(self):
        """Nothing bounds a request body, so something must bound this.

        Each finding is a log line shipped to centralised logging, and
        naming elements individually is precisely what turns one bad
        parameter into one line per element. The unknown-parameter scan
        has had this bound since phase 3 for the same reason; element
        naming gave type mismatches the same unbounded property.
        """
        findings = self._findings(
            {'disk': ['x'] * (validation.MAX_TYPE_MISMATCH_FINDINGS + 500)})

        self.assertEqual(
            validation.MAX_TYPE_MISMATCH_FINDINGS + 1, len(findings))
        self.assertEqual(
            ('(overflow)',
             '500 further mismatching values not reported individually'),
            findings[-1])
        # The bound is on findings, not on which ones: the reported
        # elements are still the first ones, in order.
        self.assertEqual(
            ['disk[%d]' % index
             for index in range(validation.MAX_TYPE_MISMATCH_FINDINGS)],
            [parameter for parameter, _ in findings[:-1]])

    def test_a_finding_carries_the_element_type_not_the_containers(self):
        """Decision D5 makes the type the only thing a finding says
        about a value, so it must be the type of the value that failed.
        `disk[0]` reported as a list describes the array around it."""
        schema = self._Schema(unknown=marshmallow.EXCLUDE)
        findings = validation._schema_findings(
            schema, {'disk': ['a string'], 'tags': [{'a': 1}]})

        self.assertEqual(
            [('disk[0]', 'str'), ('tags[0]', 'dict')],
            sorted((f.parameter, f.value_type) for f in findings))

    def test_an_unresolvable_path_falls_back_to_the_container(self):
        # marshmallow can key an error by something the supplied value
        # has no entry for. The container's type is still true of what
        # the caller sent, which beats reporting nothing.
        self.assertEqual(
            'list',
            validation.Finding(
                validation.TYPE_MISMATCH, 'disk[3]', 'nope',
                validation._element_value(['only one'], (3,))).value_type)

    def test_a_scalar_field_is_unchanged(self):
        # The flattening must not disturb the shape the rest of the
        # phase's tests pin, which is the common case by far.
        self.assertEqual(
            [('name', 'Not a valid string.')], self._findings({'name': 5}))

    def test_a_whole_container_of_the_wrong_type_is_unchanged(self):
        # marshmallow answers a plain list here, not a dict, so this
        # never went through the nested path at all.
        self.assertEqual(
            [('disk', 'Not a valid list.')],
            self._findings({'disk': 'not a list'}))

    def test_no_detail_renders_a_python_repr(self):
        # The property the fix exists for, stated directly: whatever
        # marshmallow nests, no finding may carry a dict or list repr
        # into the caller's error message.
        for supplied in ({'disk': ['x']}, {'tags': [{'a': 1}]},
                         {'disk': ['x', 'y']}, {'name': 5}):
            for parameter, detail in self._findings(supplied):
                self.assertNotIn('{', detail, parameter)
                self.assertNotIn('[', detail, parameter)


# The shape a structured type token will render once phase 7's step 3
# writes one: a diskspec, near enough. Written out here rather than
# read from ARGTYPES because step 2 deliberately precedes the
# vocabulary. The compiler's contract is with the *rendered
# specification* and not with any type token (see validation.py's
# module docstring), so the fragment a test hands it is exactly as good
# evidence as one ARGTYPES produced -- and these tests have to be able
# to run before anything declares one.
OBJECT_FRAGMENT = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['size'],
    'properties': {
        'size': {'type': 'integer', 'minimum': 0},
        'base': {'type': 'string'},
        'type': {'type': 'string'},
    },
}


def _permissive_fragment():
    """OBJECT_FRAGMENT with additionalProperties dropped, nothing else."""
    out = dict(OBJECT_FRAGMENT)
    del out['additionalProperties']
    return out


class ObjectFragmentCompilationTestCase(base.ShakenFistTestCase):
    """A rendered `properties` block becomes a nested schema.

    Phase 7's step 2. Until it, `_SCALARS` mapped `object` to
    fields.Dict and nothing else, so a diskspec was checked for being a
    mapping and then handed to the handler unexamined -- eleven nested
    values were a recorded 500 and nine more were accepted in silence
    (findings F5 and F6 of
    PLAN-api-input-validation-phase-07-structured.md).

    Asserted through _schema_findings() rather than against marshmallow's
    raw error dict, because the finding is what a caller sees: phase 4
    turned the detail string into the response body's error message.
    """

    def _findings(self, field, supplied):
        schema = marshmallow.Schema.from_dict({'disk': field})()
        return [(f.parameter, f.detail)
                for f in validation._schema_findings(schema, supplied)]

    def test_a_fragment_with_properties_compiles_to_a_nested_schema(self):
        field = validation._field(OBJECT_FRAGMENT)

        self.assertIsInstance(field, fields.Nested)
        self.assertEqual(
            [('disk.size', 'Not a valid integer.')],
            self._findings(field, {'disk': {'size': 'eight'}}))
        self.assertEqual(
            [], self._findings(
                field, {'disk': {'size': 8, 'base': 'label:ubuntu',
                                 'type': 'disk'}}))

    def test_each_property_compiles_through_the_same_path(self):
        """A property is a rendered fragment like any other, so
        everything _field() knows applies one level down too -- which is
        the whole reason the branch recurses rather than building
        fields.Dict(keys=..., values=...). The published minimum on
        `size` is the visible case."""
        field = validation._field(OBJECT_FRAGMENT)

        self.assertEqual(
            [('disk.size', 'Must be greater than or equal to 0.')],
            self._findings(field, {'disk': {'size': -1}}))
        self.assertEqual(
            [('disk.base', 'Not a valid string.')],
            self._findings(field, {'disk': {'size': 8, 'base': 5}}))

    def test_a_fragment_without_properties_is_still_a_mapping(self):
        """Decision D47, and the reason the branch is keyed on
        `properties` rather than on the type being `object`.

        ARGTYPES['dict'] renders a bare {'type': 'object'}, and two live
        parameters need that to keep meaning "any mapping at all":
        `metadata` on POST /instances, whose keys are the caller's, and
        `bound_claims` on the mapping rule endpoints, which
        federation.py guards by hand with better messages than a schema
        could produce. A branch keyed on the type would have compiled
        both to an empty nested schema -- which, with marshmallow's
        default of unknown=RAISE, would have refused every key either
        one has ever carried.
        """
        field = validation._field(api_base.ARGTYPES['dict'])

        self.assertIsInstance(field, fields.Dict)
        self.assertNotIsInstance(field, fields.Nested)
        self.assertEqual(
            [], self._findings(field, {'disk': {'anything': 'at all'}}))

    def test_a_mapping_takes_both_a_dict_and_a_list_as_one_value(self):
        """The shape ARGTYPES['dict']'s own comment records sfcbr's k3s
        traffic storing under one parameter. It is asserted here at the
        compiler, and end to end through POST /instances in
        test_request_validation.MetadataStaysUnconstrainedTestCase --
        the second is the one definition-of-done item 1 asks for, and
        this is the one which says where it broke."""
        field = validation._field(api_base.ARGTYPES['dict'])

        for value in ({'nested': 'dict'}, ['a', 'list'], 'a string', 5):
            with self.subTest(value=value):
                self.assertEqual(
                    [], self._findings(field, {'disk': {'k3s': value}}))

    def test_unknown_keys_are_refused_only_where_the_fragment_says_so(self):
        """additionalProperties: false compiles to unknown=RAISE (D41).

        The EXCLUDE arm is the one worth testing: marshmallow's own
        default for a schema is RAISE, so without writing EXCLUDE out
        every structured token would refuse unknown keys whether it
        published additionalProperties: false or not, and the published
        specification would say something the server does not do.
        """
        refuses = validation._field(OBJECT_FRAGMENT)
        permits = validation._field(_permissive_fragment())

        self.assertEqual(marshmallow.RAISE, refuses.schema.unknown)
        self.assertEqual(
            [('disk.wombat', 'Unknown field.')],
            self._findings(refuses, {'disk': {'size': 8, 'wombat': 1}}))

        self.assertEqual(marshmallow.EXCLUDE, permits.schema.unknown)
        self.assertEqual(
            [], self._findings(permits, {'disk': {'size': 8, 'wombat': 1}}))

    def test_a_required_property_is_honoured(self):
        """A property inside a fragment has no CompiledEndpoint to carry
        its required-ness as metadata the way a parameter does, so
        marshmallow enforces it directly. Still a check and never a
        coercion: the absence becomes a finding naming its path."""
        required = validation._field(OBJECT_FRAGMENT)

        self.assertEqual(
            [('disk.size', 'Missing data for required field.')],
            self._findings(required, {'disk': {'base': 'label:ubuntu'}}))
        # And the properties the fragment does not list stay optional.
        self.assertEqual([], self._findings(required, {'disk': {'size': 8}}))

    def test_a_property_still_accepts_a_null(self):
        """_field()'s nullability rule reaches one level down as well.

        Every compiled field is allow_none=True because a JSON null
        reaches the handler as None today and several handlers read that
        as "not supplied". A nested field which refused one would be a
        rule this module invented rather than one any declaration
        states -- and required-ness does not change it, for the same
        reason it does not at the top level.
        """
        field = validation._field(OBJECT_FRAGMENT)

        self.assertEqual([], self._findings(field, {'disk': {'size': None}}))
        self.assertEqual([], self._findings(field, {'disk': None}))

    def test_a_bound_on_the_fragment_itself_is_dropped(self):
        """The same reasoning the `any` and unrecognised-type branches
        record, and the reason this branch pops `validate` too.

        validate.Range meeting a mapping raises TypeError rather than
        ValidationError, which escapes schema.validate() into
        _schema_findings' broad except -- and that reports *no* findings
        for the whole request, silently disabling every other check on
        it. base._validated_constraints() refuses a minimum, maximum or
        pattern on a non-numeric, non-string type at import time, so
        none can arrive here from a declaration today; this is what
        keeps a future token rendering its own from doing the damage.
        """
        bounded = dict(OBJECT_FRAGMENT)
        bounded['minimum'] = 1
        bounded['pattern'] = '^x$'

        field = validation._field(bounded)

        self.assertEqual([], list(field.validators))
        self.assertEqual([], self._findings(field, {'disk': {'size': 8}}))


class ExactIntegerTestCase(base.ShakenFistTestCase):
    """1.5 is not an integer (decision D46, finding F8).

    The compiled path is check-only (decision D14): validate_request()
    runs schema.validate() for its findings and discards the
    deserialised result, so the handler receives the body the caller
    sent. marshmallow's fields.Integer accepts anything int() will take,
    so before this `POST /instances {"cpus": 1.5}` passed validation,
    reached InstanceData unchanged and raised a pydantic
    ValidationError -- a recorded 500 for a caller's mistake.

    What the field must *not* do is refuse a value the server converts
    faithfully today, which is why this is not marshmallow's
    strict=True. See _ExactInteger's docstring, and the two tests below
    which pin the width.
    """

    def _error(self, field, value):
        try:
            field.deserialize(value)
        except marshmallow.ValidationError as e:
            return e.messages
        return None

    def test_a_fractional_number_is_not_an_integer(self):
        field = validation._field({'type': 'integer'})

        self.assertEqual(['Not a valid integer.'], self._error(field, 1.5))
        self.assertEqual(['Not a valid integer.'], self._error(field, -0.5))

    def test_an_integral_float_is_an_integer(self):
        """JSON has one numeric type, so 8.0 and 8 are the same JSON
        number and reading the first as the integer 8 invents nothing.
        It is also accepted today, and phase 6's width rule says a
        validator must be no narrower than its handler."""
        field = validation._field({'type': 'integer'})

        self.assertEqual(8, field.deserialize(8.0))
        self.assertEqual(8, field.deserialize(8))

    def test_a_numeric_string_is_still_an_integer(self):
        """The reason strict=True cannot be used, and it is not a
        nicety: base.py hands flask.request.args.to_dict() to check(),
        so every integer *query* parameter arrives as a string.
        `offset=10` on GET /blobs/<uuid>/data is the live case, and
        strict=True answered `400 offset: Not a valid integer.` for
        it."""
        field = validation._field({'type': 'integer'})

        self.assertEqual(10, field.deserialize('10'))
        self.assertEqual(-1, field.deserialize('-1'))
        # A fractional string was already refused, by int() itself.
        self.assertEqual(['Not a valid integer.'], self._error(field, '1.5'))

    def test_a_number_field_is_left_alone(self):
        """fields.Float has the same flag and must not get it. JSON has
        one numeric type and every integer is a valid float, so a
        `number` field accepting an integer is describing JSON rather
        than lying about it."""
        field = validation._field({'type': 'number'})

        self.assertIsInstance(field, fields.Float)
        self.assertEqual(2.0, field.deserialize(2))
        self.assertEqual(1.5, field.deserialize(1.5))

    def test_the_check_reaches_every_nesting_depth(self):
        """D46 says "at every nesting depth", and a nested integer is
        the case that would otherwise have inherited the lie the moment
        phase 7's step 3 declares one."""
        field = validation._field(
            {'type': 'array', 'items': OBJECT_FRAGMENT})
        schema = marshmallow.Schema.from_dict({'disk': field})()

        self.assertEqual(
            [('disk[1].size', 'Not a valid integer.')],
            [(f.parameter, f.detail) for f in validation._schema_findings(
                schema, {'disk': [{'size': 8}, {'size': 8.5}]})])
