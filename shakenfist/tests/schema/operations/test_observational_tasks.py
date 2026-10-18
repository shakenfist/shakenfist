# Copyright 2026 Michael Still and contributors
#
# Pins which cluster operation tasks are declared observational. See
# docs/plans/PLAN-cluster-op-visibility.md.

import importlib
import pkgutil

from shakenfist.operations.clusteroperationmapping import OPERATION_NAMES_TO_CLASSES
from shakenfist.schema import operations
from shakenfist.tests import base


def _operation_models():
    """Return {module name: model class} for every schema operation."""
    models = {}
    for info in pkgutil.iter_modules(operations.__path__):
        module = importlib.import_module(
            f'{operations.__name__}.{info.name}')
        model = getattr(module, 'model', None)
        if model is None:
            # util and baseclusteroperation define no operation model.
            continue
        models[info.name] = model
    return models


class ObservationalTasksInertTestCase(base.ShakenFistTestCase):
    """No operation model declares an observational task yet.

    Phase 2 of the cluster-op-visibility plan adds the observational
    flag and the filters that read it, but deliberately classifies
    nothing: if any model declared an observational task, the filtered
    get_latest and has_pending queries would change behaviour before
    phase 3's audit of the tasks. Phase 3 replaces this test with one
    that pins the classification table.
    """

    def test_operation_models_are_found(self):
        # Guard against an import-path mistake making the test below
        # pass vacuously over an empty set of models.
        # Derived rather than counted: every operation type the
        # dispatcher knows must have a schema model the scan found.
        models = _operation_models()
        self.assertEqual(
            set(OPERATION_NAMES_TO_CLASSES), set(models), sorted(models))

    def test_no_model_declares_observational_tasks(self):
        for name, model in _operation_models().items():
            with self.subTest(model=name):
                self.assertFalse(
                    getattr(model, 'observational_tasks', frozenset()),
                    f'{name} declares observational tasks')
                # A declaration missing its ClassVar annotation would be
                # a pydantic field, invisible to is_observational().
                self.assertNotIn('observational_tasks', model.model_fields)
