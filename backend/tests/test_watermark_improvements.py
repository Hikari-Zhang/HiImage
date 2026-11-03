"""水印修复的参数、局部处理及检测契约回归测试。"""
import asyncio
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.services import image_processing
@pytest.mark.parametrize('position', [(0, 0), (1200, 800), (2930, 1930)])
def test_high_resolution_repairs_are_local_and_preserve_every_unmasked_pixel(position):
    from core.inpaint_regions import prepare_mask, repair_regions
    rng = np.random.default_rng(42)
    image = rng.integers(0, 256, (2000, 3000, 3), dtype=np.uint8)
    x, y = position
    mask = prepare_mask(image.shape, rois=[(x, y, x + 50, y + 50)], dilation=3)
    seen = []
    def repair(crop, local_mask):
        seen.append(crop.shape)
        crop[:] = 200  # 模拟会改写整幅输入的模型。
        return crop
    result = repair_regions(image, mask, repair)
    assert result.shape == image.shape
    assert len(seen) == 1
    assert max(seen[0][:2]) <= 312
    assert np.array_equal(result[mask == 0], image[mask == 0])
    assert result[y + 20, x + 20].tolist() == [200, 200, 200]


def test_disconnected_watermarks_are_processed_separately_and_empty_mask_skips_model():
    from core.inpaint_regions import prepare_mask, repair_regions
    image = np.zeros((800, 1600, 3), np.uint8)
    mask = prepare_mask(image.shape, rois=[(10, 10, 20, 20), (1500, 700, 1520, 720)])
    calls = []
    def repair(crop, local_mask):
        calls.append(crop.shape)
        return np.full_like(crop, 100)
    result = repair_regions(image, mask, repair)
    assert len(calls) == 2
    assert result[15, 15, 0] == 100
    assert np.array_equal(repair_regions(image, mask * 0, repair), image)
    assert len(calls) == 2


def test_nearby_letters_share_context_and_wrong_model_dimensions_fail():
    from core.inpaint_regions import context_boxes, repair_regions
    mask = np.zeros((200, 400), np.uint8)
    mask[50:60, 50:60] = 255
    mask[50:60, 90:100] = 255
    assert len(context_boxes(mask)) == 1
    with pytest.raises(ValueError, match='模型输出尺寸'):
        repair_regions(np.zeros((200, 400, 3), np.uint8), mask, lambda image, _: image[:1])


def test_ocr_candidates_distinguish_text_confidence_from_watermark_signals():
    from core.watermark_ocr import rank_candidates
    rows = [
        ([[200, 200], [400, 200], [400, 240], [200, 240]], '商品名称', .99),
        ([[0, 0], [120, 0], [120, 30], [0, 30]], 'watermark', .85),
        ([[10, 700], [130, 700], [130, 730], [10, 730]], 'watermark', .87),
    ]
    candidates = rank_candidates(rows, 1000, 800)
    assert candidates[0]['text'] == 'watermark'
    assert candidates[0]['watermark_score'] > candidates[-1]['watermark_score']
    assert '重复文字' in candidates[0]['reasons']
    assert candidates[-1]['reasons'] == ['普通文字，需人工确认']
    assert candidates[-1]['text_confidence'] == .99


def test_ocr_unavailable_reports_fallback_instead_of_pretending_ocr_succeeded(monkeypatch):
    from core import watermark_ocr
    def fail():
        raise ImportError('missing')
    monkeypatch.setattr(watermark_ocr, 'get_ocr', fail)
    result = watermark_ocr.detect_candidates(np.zeros((200, 200, 3), np.uint8))
    assert result['engine'] == 'cv'
    assert 'OCR 暂不可用' in result['warning']


def test_actual_ocr_detects_text_and_returns_no_candidates_for_flat_negative():
    pytest.importorskip('rapidocr_onnxruntime')
    import cv2
    from core.watermark_ocr import detect_candidates
    blank = np.full((320, 640, 3), 120, np.uint8)
    assert detect_candidates(blank)['candidates'] == []
    image = blank.copy()
    cv2.putText(image, 'WATERMARK', (300, 280), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    result = detect_candidates(image)
    assert result['engine'] == 'paddleocr-onnx'
    assert any('WATERMARK' in c['text'].upper() for c in result['candidates'])
    assert all(len(c['polygon']) == 4 for c in result['candidates'])


@pytest.mark.parametrize('angle', [-20, 20])
def test_rotation_preserves_corners_and_maps_coordinates_back(angle):
    import cv2
    from core.watermark_ocr import rotate_view
    image = np.zeros((100, 200, 3), np.uint8)
    rotated, inverse = rotate_view(image, angle)
    forward = cv2.invertAffineTransform(inverse)
    corners = np.array([[[0, 0], [200, 0], [200, 100], [0, 100]]], np.float32)
    transformed = cv2.transform(corners, forward)
    assert transformed.min() >= -.001
    assert transformed[0, :, 0].max() <= rotated.shape[1] + .001
    assert transformed[0, :, 1].max() <= rotated.shape[0] + .001
    assert np.allclose(cv2.transform(transformed, inverse), corners, atol=.001)


def test_multi_view_boxes_merge_fragments_but_keep_separate_lines():
    from core.watermark_ocr import merge_detections
    full = [[[0, 0], [100, 0], [100, 20], [0, 20]], 'FULL', .9]
    partial = [[[10, 0], [40, 0], [40, 20], [10, 20]], 'PART', .99]
    separate = [[[0, 30], [100, 30], [100, 50], [0, 50]], 'OTHER', .9]
    assert merge_detections([partial, separate, full]) == [separate, full]


def test_bounded_detector_maps_large_image_coordinates_and_skips_flat_extra_passes():
    from core.watermark_ocr import scan_text
    shapes = []
    def engine(image, **kwargs):
        shapes.append(image.shape)
        return [[[[100, 100], [200, 100], [200, 120], [100, 120]], 'TEST', .9]], None
    rows = scan_text(engine, np.full((2000, 4000, 3), (80, 130, 170), np.uint8), .9)
    assert shapes == [(800, 1600, 3)]
    assert rows[0][0] == [[250, 250], [500, 250], [500, 300], [250, 300]]


def test_remaining_semitransparent_watermarks_after_first_repair_are_detected():
    pytest.importorskip('rapidocr_onnxruntime')
    import cv2
    from pathlib import Path
    from core.watermark_ocr import detect_candidates
    image = cv2.imread(str(Path(__file__).parent / 'fixtures' / 'watermark-residual.jpg'))
    assert image is not None
    result = detect_candidates(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), .9)
    assert len(result['candidates']) >= 3
    assert any(len(item['text']) >= 5 for item in result['candidates'])
    for item in result['candidates']:
        x1, y1, x2, y2 = item['region']
        assert 0 <= x1 < x2 <= image.shape[1]
        assert 0 <= y1 < y2 <= image.shape[0]


@pytest.mark.parametrize('handler', ['models_list', 'models_health', 'model_health'])
def test_slow_model_checks_do_not_block_detection_or_websocket_event_loop(monkeypatch, handler):
    import threading
    from app.routers import models
    entered, release = threading.Event(), threading.Event()
    threads = []
    class Checker:
        def check_all(self):
            threads.append(threading.get_ident())
            entered.set()
            assert release.wait(2)
            return []
        def check_model(self, model):
            self.check_all()
            return SimpleNamespace(model_id=model, name=model, status='ok',
                                   message='', size_bytes=0, path='', files=[])
    monkeypatch.setattr(models, 'ModelChecker', Checker)
    monkeypatch.setattr(models, '_result_to_dict', lambda result: {'id': result.model_id})
    async def scenario():
        call = getattr(models, handler)
        task = asyncio.create_task(call('wm_lama') if handler == 'model_health' else call())
        try:
            for _ in range(50):
                if entered.is_set():
                    break
                await asyncio.sleep(.01)
            assert entered.is_set()
            assert threads[0] != threading.get_ident()
        finally:
            release.set()
            await task
    asyncio.run(scenario())
