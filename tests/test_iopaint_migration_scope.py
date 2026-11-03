"""验证冻结的迁移范围；manifest 是设计约定，不是当前运行配置。"""
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCOPE = json.loads((ROOT / 'docs/iopaint_migration_scope.json').read_text(encoding='utf-8'))


def registry():
    document = yaml.safe_load((ROOT / 'backend/core/models.yaml').read_text(encoding='utf-8'))
    return {model['id']: model for model in document['models']}


def test_scope_accounts_for_all_existing_models_without_overlap():
    keep, retire = SCOPE['models']['keep'], SCOPE['models']['retire']
    assert len(keep) == len(set(keep)) == 33
    assert len(retire) == len(set(retire)) == 15
    assert not set(keep) & set(retire)
    # 迁移期间允许逐项移除退役项，保留项必须仍存在；新增条目须同步评审范围。
    assert set(registry()) == set(SCOPE["current_model_ids"])
    assert set(registry()) | set(SCOPE["removed_model_ids"]) == set(keep) | set(retire)
    assert not set(registry()) & set(SCOPE["removed_model_ids"])


def test_target_defaults_are_retained_and_support_their_modes():
    models = registry()
    for mode, model_id in SCOPE['target_defaults'].items():
        assert model_id in SCOPE['models']['keep']
        if model_id in models:
            assert mode in models[model_id]['tags']
    assert SCOPE['target_defaults']['outfit_swap'] == 'sd15'
    assert SCOPE['target_defaults']['prompt_inpaint'] == 'sd15_inpaint_prompt'


def test_five_repair_routes_only_reference_retained_models():
    routes = SCOPE['repair_routes']
    assert set(routes) == {'lama', 'opencv', 'sd15_inpaint', 'sdxl_inpaint', 'flux_fill'}
    routed = [model_id for models in routes.values() for model_id in models]
    assert len(routed) == len(set(routed))
    assert set(routed) <= set(SCOPE['models']['keep'])
    assert {'wm_lama', 'wm_cv2', 'wm_sd15', 'wm_sdxl'} <= set(routed)


def test_scope_preserves_unrelated_functionality_and_existing_weights():
    for model in registry().values():
        if model['provider'] in {'rembg', 'realesrgan', 'facexlib'}:
            assert model['id'] in SCOPE['models']['keep']
    assert SCOPE['weights_policy'] == 'preserve_existing_files'
    assert SCOPE['retired_id_policy'] == 'explicit_error_no_silent_remap'


def test_hard_deadline_cannot_be_replaced_with_thread_future_cancellation():
    contract = SCOPE['hard_timeout']
    assert contract['required'] is True
    assert contract['isolation'] == 'supervised_process'
    assert contract['start_method'] == 'spawn'
    assert contract['max_active_workers'] == 1
    assert contract['worker_lifetime'] == 'one_request'
    assert contract['clock'] == 'monotonic'
    assert contract['starts'] == 'before_worker_spawn'
    assert contract['reset_per_region'] is False
    assert contract['reset_per_stage'] is False
    assert {'worker_start', 'local_model_load', 'all_regions', 'postprocess', 'result_transfer'} <= set(contract['includes'])
    assert contract['download_in_worker'] is False


def test_timeout_and_cleanup_failure_have_explicit_contracts():
    contract = SCOPE['hard_timeout']
    assert contract['timeout_seconds'] == {'fast': 300, 'generative': 1800}
    assert contract['timeout_grace_seconds'] == 0
    assert contract['cleanup_wait_seconds'] == 5
    assert 0 < contract['user_cancel_grace_seconds'] <= contract['cleanup_wait_seconds']
    assert contract['kill_process_tree'] is True
    assert contract['release_slot_only_after_exit'] is True
    assert contract['cleanup_failure_policy'] == 'block_inference_until_exit_or_restart'
    assert (contract['timeout_http_status'], contract['timeout_error_code'], contract['timeout_task_status']) == (504, 'INFERENCE_TIMEOUT', 'failed')
    assert (contract['cleanup_failure_http_status'], contract['cleanup_failure_error_code']) == (503, 'INFERENCE_CLEANUP_FAILED')
    assert contract['automatic_retry'] is False


def test_removal_does_not_depend_on_legacy_output_or_quality_benchmarks():
    policy = SCOPE['removal_policy']
    assert policy['primary_goal'] == 'remove_archived_iopaint'
    assert policy['requires_legacy_quality_baseline'] is False
    assert policy['requires_output_equivalence'] is False
    assert policy['requires_new_quality_benchmark'] is False
    assert policy['cancelled_subtasks'] == ['EXIT-02']
    assert policy['quality_work_tracked_separately'] == ['QUALITY-01', 'GEN-01']
    assert policy['preserve_hard_timeout'] is True
    assert policy['requires_functional_smoke'] is True
    assert policy['requires_clean_install_without_iopaint'] is True
