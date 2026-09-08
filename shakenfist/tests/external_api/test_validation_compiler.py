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

    def test_documented_formats_do_not_become_validators(self):
        """Prose formats are documentation, and netblock is deliberately
        pattern-free.

        ``netblock`` has no pattern because NetworksEndpoint.post()
        parses with ipaddress.ip_network(), which takes IPv6 as well;
        publishing an IPv4 CIDR regex would describe the API as
        narrower than it is and phase 4 would compile that into a 400
        for input which works today. The prose formats on uuidorname,
        namespace, node, url and ipv4 are the same kind of claim.
        Semantic validation of any of them is phase 6.
        """
        netblock = self.registry[('NetworksEndpoint', 'post')].body
        self.assertIsInstance(netblock.fields['netblock'], fields.String)
        self.assertEqual([], list(netblock.fields['netblock'].validators))

        # And the general rule the netblock case is one instance of,
        # derived from the published specification rather than from a
        # list of examples, which would go stale as tokens are retyped:
        # a validator exists only where a bound is published. `format`
        # never produces one.
        constrained = 0
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
                        constrained += bool(bounded)
                        self.assertEqual(
                            bounded, bool(schema.fields[name].validators),
                            '%s.%s %s: published bound %s, compiled '
                            'validators %s' % (cls.__name__, method, name,
                                               bounded,
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
