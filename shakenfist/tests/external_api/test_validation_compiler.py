# Copyright 2019 Michael Still and contributors

"""The compilation of declarations into request schemas.

Phase 3 PR 2. Nothing validates yet, so these assertions are about
whether the compiled schemas *describe* what the declarations say --
which is the property phase 4 turns into rejections, and so the last
point at which a mistake is cheap.
"""

import marshmallow
from marshmallow import fields

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.external_api import validation
from shakenfist.tests import base
from shakenfist.tests.external_api.test_parameter_declarations import (
    UNDOCUMENTED_BY_DESIGN)


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
        # is pinned. Twenty two, from two sources which both have to
        # work: nine carrying an explicit constraints element (the five
        # events `limit` caps, the two `key_ttl` ranges, and the two
        # namespace claim `expires_in_seconds` minimums), and thirteen
        # whose bound comes from the type token alone -- `minimum: 0`
        # rendered by `unsignedinteger` on max_versions, offset, blob
        # limit, cpus, memory and the six namespace claim limits. A
        # change in either is meant to fail here and be re-counted
        # deliberately.
        self.assertEqual(22, constrained)

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
