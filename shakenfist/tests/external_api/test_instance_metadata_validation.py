# Copyright 2019 Michael Still and contributors

"""Validation of the reserved instance metadata keys.

``_validate_instance_metadata()`` is the only place a caller is told
they got the ``affinity`` key wrong, and it is shared by instance
create and both metadata endpoints, so a value it fails to refuse
becomes a 500 on three public paths rather than one.

The affinity cases below are all about the *inner* per-tag value.
The outer cases already work -- a non-dict is refused by the
isinstance check above the loop, and None by the falsiness check at
the top of the function -- and stating that here is the point: the
hole was one level down from where it looked.
"""

from unittest import mock

from shakenfist.external_api.instance import AFFINITY_DEPRECATION_MESSAGE
from shakenfist.external_api.instance import _affinity_spec_is_weighted
from shakenfist.external_api.instance import _validate_instance_metadata
from shakenfist.external_api.instance import _warn_if_weighted_affinity
from shakenfist.instance import Instance
from shakenfist.tests import base


AFFINITY = Instance.METADATA_KEY_AFFINITY
TAGS = Instance.METADATA_KEY_TAGS


class ValidateInstanceMetadataTestCase(base.ShakenFistTestCase):
    def _assert_refused(self, value, expected_fragment):
        resp = _validate_instance_metadata(AFFINITY, value)
        self.assertIsNotNone(
            resp, 'value %r was accepted, expected a 400' % (value,))
        self.assertEqual(400, resp.status_code)
        self.assertIn(expected_fragment, resp.get_data(as_text=True))

    def test_valid_weighted_spec_is_accepted(self):
        self.assertIsNone(
            _validate_instance_metadata(AFFINITY, {'first-node': 100}))
        self.assertIsNone(
            _validate_instance_metadata(AFFINITY, {'db': -50, 'web': 1}))

    def test_list_valued_entry_is_refused(self):
        # int(['a']) raises TypeError, which the original handler did not
        # catch. This is also the shape the binary affinity model uses, so
        # a caller guessing the new syntax early hits exactly this.
        self._assert_refused({'first-node': ['a']}, 'should be integers')

    def test_dict_valued_entry_is_refused(self):
        self._assert_refused({'first-node': {'x': 1}}, 'should be integers')

    def test_none_valued_entry_is_refused(self):
        # Note this is the *inner* value being None. An outer None is
        # refused by the falsiness check and never reaches the loop.
        self._assert_refused({'first-node': None}, 'should be integers')

    def test_infinite_valued_entry_is_refused(self):
        # int(float('inf')) raises OverflowError, not TypeError or
        # ValueError. json.loads() accepts the bare Infinity literal by
        # default, so flask hands this straight through from a request
        # body and it is reachable without a client doing anything odd.
        self._assert_refused({'first-node': float('inf')}, 'should be integers')
        self._assert_refused({'first-node': float('-inf')}, 'should be integers')

    def test_nan_valued_entry_is_refused(self):
        # NaN was already handled -- int(float('nan')) raises ValueError --
        # but it arrives by the same route as Infinity, so it is asserted
        # here so a later narrowing of the except clause cannot drop it
        # silently.
        self._assert_refused({'first-node': float('nan')}, 'should be integers')

    def test_boolean_valued_entry_is_refused(self):
        # A deliberate behaviour change: int(True) is 1, so this used to be
        # accepted as a weight of one.
        self._assert_refused({'first-node': True}, 'not booleans')
        self._assert_refused({'first-node': False}, 'not booleans')

    def test_numeric_string_entry_is_still_accepted(self):
        # int('3') succeeds, so this is accepted today. Asserted so that a
        # later tightening of the check to a type test is a deliberate
        # decision with a failing test behind it, rather than an unnoticed
        # compatibility break.
        self.assertIsNone(
            _validate_instance_metadata(AFFINITY, {'first-node': '3'}))

    def test_non_numeric_string_entry_is_refused(self):
        self._assert_refused({'first-node': 'banana'}, 'should be integers')

    def test_outer_affinity_value_cases_are_unchanged(self):
        # The two outer refusals this fix is not about, asserted so that
        # widening the inner handler cannot be mistaken for having moved
        # them.
        resp = _validate_instance_metadata(AFFINITY, ['a'])
        self.assertEqual(400, resp.status_code)
        self.assertIn('valid JSON dictionary', resp.get_data(as_text=True))

        resp = _validate_instance_metadata(AFFINITY, None)
        self.assertEqual(400, resp.status_code)
        self.assertIn('no value specified', resp.get_data(as_text=True))

    def test_tags_key_is_unaffected(self):
        self.assertIsNone(_validate_instance_metadata(TAGS, ['a', 'b']))

        resp = _validate_instance_metadata(TAGS, {'a': 1})
        self.assertEqual(400, resp.status_code)
        self.assertIn('should be a JSON list', resp.get_data(as_text=True))

    def test_no_key_is_refused(self):
        resp = _validate_instance_metadata('', 'value')
        self.assertEqual(400, resp.status_code)
        self.assertIn('no key specified', resp.get_data(as_text=True))


class ValidateBinaryAffinityTestCase(base.ShakenFistTestCase):
    """The binary affinity value shape.

    The binary model is a second value shape under the same ``affinity``
    key rather than a key of its own, so that a caller cannot supply
    both models at once and mean nothing coherent. The two are told
    apart by type: a dict of integers is the weighted form, a dict of
    the four reserved names mapping to lists is the binary one.
    """

    def _assert_refused(self, value, expected_fragment):
        resp = _validate_instance_metadata(AFFINITY, value)
        self.assertIsNotNone(
            resp, 'value %r was accepted, expected a 400' % (value,))
        self.assertEqual(400, resp.status_code)
        self.assertIn(expected_fragment, resp.get_data(as_text=True))

    def test_all_four_constraints_are_accepted(self):
        self.assertIsNone(_validate_instance_metadata(AFFINITY, {
            'require_with_tag': ['web'],
            'require_without_tag': ['batch'],
            'prefer_with_tag': ['cache'],
            'prefer_without_tag': ['noisy'],
        }))

    def test_a_subset_of_constraints_is_accepted(self):
        self.assertIsNone(_validate_instance_metadata(
            AFFINITY, {'require_with_tag': ['web']}))

    def test_empty_tag_lists_are_accepted(self):
        self.assertIsNone(_validate_instance_metadata(
            AFFINITY, {'prefer_with_tag': []}))

    def test_non_list_constraint_value_is_refused(self):
        self._assert_refused(
            {'require_with_tag': 'web'}, 'should be a JSON list of tags')
        self._assert_refused(
            {'require_with_tag': {'a': 1}}, 'should be a JSON list of tags')

    def test_non_string_tag_is_refused(self):
        self._assert_refused(
            {'require_with_tag': [1]}, 'non-empty tag names')
        self._assert_refused(
            {'require_with_tag': [None]}, 'non-empty tag names')
        self._assert_refused(
            {'require_with_tag': ['']}, 'non-empty tag names')
        # isinstance(True, str) is false, so a boolean is caught by the
        # tag name check here rather than needing its own clause.
        self._assert_refused(
            {'require_with_tag': [True]}, 'non-empty tag names')

    def test_mixing_the_two_forms_is_refused(self):
        # Either way of resolving this silently discards half of what the
        # caller asked for, so it is refused rather than guessed at.
        self._assert_refused(
            {'require_with_tag': ['web'], 'socialite': 100}, 'cannot be mixed')

    def test_a_misspelled_constraint_alone_reads_as_the_weighted_form(self):
        # 'require_with_tags' is not a reserved name, so this is not the
        # binary shape at all and falls through to the weighted
        # validation -- which refuses it, because a list is not an
        # integer. The caller still gets a 400; this asserts which one,
        # so that the shape discrimination is pinned rather than
        # incidental.
        self._assert_refused(
            {'require_with_tags': ['web']}, 'should be integers')


class WeightedAffinityDeprecationTestCase(base.ShakenFistTestCase):
    """The deprecation warning for the weighted affinity form.

    Emitted where a specification is accepted rather than where it is
    consumed, so it reaches the caller at the moment they submit the
    deprecated form rather than at some later reschedule.

    The site matters and is easy to get wrong. It cannot live in
    _validate_instance_metadata: that is module level with a
    (key, value) signature and, on the create path, runs before
    Instance.new(), so there would be no object to hang an event on and
    the create path -- the one a weighted caller is most likely using --
    would emit nothing at all.
    """

    def _emitted(self, key, value):
        inst = mock.MagicMock()
        _warn_if_weighted_affinity(inst, key, value)
        return [c for c in inst.add_event.call_args_list
                if c[0][1] == AFFINITY_DEPRECATION_MESSAGE]

    def test_weighted_spec_warns(self):
        emitted = self._emitted(AFFINITY, {'first-node': 100})
        self.assertEqual(1, len(emitted))
        # The warning has to say what the spec was mapped to, or an
        # operator cannot act on it without rederiving the mapping.
        extra = emitted[0][1]['extra']
        self.assertEqual({'first-node': 100}, extra['affinity'])
        self.assertEqual(['first-node'], extra['mapped_to']['prefer_with_tag'])

    def test_binary_spec_does_not_warn(self):
        self.assertEqual(
            [], self._emitted(AFFINITY, {'prefer_with_tag': ['first-node']}))

    def test_another_metadata_key_does_not_warn(self):
        # 'tags' values are lists, and a list is not a weighted affinity
        # spec, but the key check should stop it before the shape check
        # ever runs.
        self.assertEqual([], self._emitted(TAGS, ['first-node']))

    def test_empty_affinity_does_not_warn(self):
        self.assertEqual([], self._emitted(AFFINITY, {}))
        self.assertEqual([], self._emitted(AFFINITY, None))

    def test_the_predicate_matches_the_validator(self):
        # The shape test is shared so that the validator and the warning
        # cannot disagree about which form a caller sent.
        self.assertTrue(_affinity_spec_is_weighted({'a': 1}))
        self.assertFalse(_affinity_spec_is_weighted({'prefer_with_tag': ['a']}))
        self.assertFalse(_affinity_spec_is_weighted({}))
        self.assertFalse(_affinity_spec_is_weighted(['a']))
