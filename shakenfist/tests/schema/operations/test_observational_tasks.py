# Copyright 2026 Michael Still and contributors
#
# Pins which cluster operation tasks are declared observational. See
# "When is a task observational?" in docs/developer_guide/database_internals.md.

import importlib
import pkgutil

from shakenfist.operations.clusteroperationmapping import OPERATION_NAMES_TO_CLASSES
from shakenfist.schema import operations
from shakenfist.tests import base


OBS = 'observational'
MUT = 'mutating'

# Every task of every operation model, classified. A new task or model
# fails the tests below until it is added here.
CLASSIFICATION = {
    'artifact_fetch_op': {'image_fetch': MUT},
    'imgcache_op': {'archive_transcode': MUT},
    'net_iface_op': {'interface_float': MUT},
    'net_iface_ip_op': {'interface_defloat': MUT},
    'net_ip_op': {'route_address': MUT, 'unroute_address': MUT},
    'net_macaddr_ip_op': {'remove_dhcp_lease': MUT},
    'net_op': {
        'network_deploy': MUT,
        'network_destroy': MUT,
        'network_update_dnsmasq': MUT,
        'network_remove_dnsmasq': MUT,
        'network_remove_nat': MUT,
        'network_ensure_mesh': MUT,
        'network_add_floating_ip': MUT,
        'network_remove_floating_ip': MUT,
        'network_apply_update_dnsmasq': MUT,
        'network_apply_remove_dnsmasq': MUT,
        'network_apply_create_network_node': MUT,
        'network_apply_delete_network_node': MUT,
    },
    'node_aop_op': {'preflight': MUT},
    'node_blob_op': {'verify_size_and_checksum': OBS, 'ensure_local': MUT},
    'node_inst_net_iface_op': {
        'hot_plug_instance_interface': MUT,
        'attach_instance_interface': MUT,
    },
    'node_inst_netdesc_op': {
        'instance_preflight': MUT,
        'instance_start': MUT,
        'instance_create': MUT,
    },
    'node_inst_op': {
        'collect_billing_statistics': OBS,
        'health_check_kvm_process': OBS,
        'instance_delete': MUT,
    },
    'node_inst_snap_op': {'instance_snapshot': MUT},
    'node_net_op': {
        'network_destroy': MUT,
        'network_apply_create_hypervisor': MUT,
    },
}

RUBRIC = ('classify it per the rubric in '
          'docs/developer_guide/database_internals.md')


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


def _model_task_names(name):
    module = importlib.import_module(f'{operations.__name__}.{name}')
    return {t.name for t in module.model_tasks}


class ObservationalTasksPinTestCase(base.ShakenFistTestCase):
    """The classification of every operation task is pinned."""

    def test_operation_models_are_found(self):
        # Guard against an import-path mistake making the tests below
        # pass vacuously over an empty set of models.
        # Derived rather than counted: every operation type the
        # dispatcher knows must have a schema model the scan found.
        models = _operation_models()
        self.assertEqual(
            set(OPERATION_NAMES_TO_CLASSES), set(models), sorted(models))

    def test_table_covers_every_model(self):
        models = set(_operation_models())
        for name in sorted(models - set(CLASSIFICATION)):
            self.fail(f'{name} is not in the classification table; '
                      f'{RUBRIC}')
        for name in sorted(set(CLASSIFICATION) - models):
            self.fail(f'{name} is in the classification table but is not '
                      f'an operation model')

    def test_table_covers_every_task(self):
        for name in _operation_models():
            with self.subTest(model=name):
                actual = _model_task_names(name)
                table = set(CLASSIFICATION.get(name, {}))
                for task in sorted(actual - table):
                    self.fail(f'{name}.{task} is not in the classification '
                              f'table; {RUBRIC}')
                for task in sorted(table - actual):
                    self.fail(f'{name}.{task} is in the classification '
                              f'table but is not a task of {name}')

    def test_declarations_match_table(self):
        for name, model in _operation_models().items():
            with self.subTest(model=name):
                declared = {
                    t.name for t in getattr(
                        model, 'observational_tasks', frozenset())}
                expected = {
                    task for task, kind in CLASSIFICATION.get(name, {}).items()
                    if kind == OBS}
                for task in sorted(declared - expected):
                    self.fail(f'{name}.{task} is declared observational '
                              f'but the table says otherwise; {RUBRIC}')
                for task in sorted(expected - declared):
                    self.fail(f'{name}.{task} is observational in the table '
                              f'but not declared on the model; {RUBRIC}')

    def test_table_values_are_valid(self):
        for name, tasks in CLASSIFICATION.items():
            for task, kind in tasks.items():
                self.assertIn(
                    kind, (OBS, MUT),
                    f'{name}.{task} must be {OBS!r} or {MUT!r}')

    def test_declaration_is_not_a_field(self):
        for name, model in _operation_models().items():
            with self.subTest(model=name):
                # A declaration missing its ClassVar annotation would be
                # a pydantic field, invisible to is_observational().
                self.assertNotIn(
                    'observational_tasks', model.model_fields,
                    f'{name} declares observational_tasks without '
                    f'ClassVar, so pydantic made it a field')
