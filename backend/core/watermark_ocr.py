"""PaddleOCR ONNX 文字候选；启发式分数只用于排序，不代表水印识别准确率。"""
from collections import Counter
from functools import lru_cache

import cv2
import numpy as np


@lru_cache(maxsize=1)
def get_ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)


def rank_candidates(rows, width, height):
    repeats = Counter(str(row[1]).strip().lower() for row in rows)
    candidates = []
    for polygon, text, confidence in rows:
        points = np.asarray(polygon, dtype=float).reshape(-1, 2)
        if len(points) != 4 or not np.isfinite(points).all():
            continue
        points[:, 0] = points[:, 0].clip(0, width)
        points[:, 1] = points[:, 1].clip(0, height)
        x1, y1 = np.floor(points.min(axis=0)).astype(int)
        x2, y2 = np.ceil(points.max(axis=0)).astype(int)
        if x2 <= x1 or y2 <= y1:
            continue
        normalized = str(text).strip().lower()
        reasons = []
        score = 0.15
        if x1 < width * .15 or x2 > width * .85 or y1 < height * .15 or y2 > height * .85:
            score += .2
            reasons.append('靠近边缘')
        if normalized and repeats[normalized] > 1:
            score += .35
            reasons.append('重复文字')
        if any(word in normalized for word in ('watermark', 'copyright', '©', '水印', '版权所有', 'www.', 'http')):
            score += .35
            reasons.append('包含署名或水印特征')
        if abs(points[1, 1] - points[0, 1]) > abs(points[1, 0] - points[0, 0]) * .15:
            score += .1
            reasons.append('倾斜文字')
        candidates.append({
            'region': [int(x1), int(y1), int(x2), int(y2)],
            'polygon': points.round().astype(int).tolist(), 'text': str(text),
            'text_confidence': round(float(confidence), 3),
            'watermark_score': round(min(score, 1), 2),
            'reasons': reasons or ['普通文字，需人工确认'],
        })
    return sorted(candidates, key=lambda item: item['watermark_score'], reverse=True)[:128]


def rotate_view(image, angle):
    """扩展画布避免切掉角落水印，并返回坐标逆变换。"""
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
    new_width = int(np.ceil(height * abs(matrix[0, 1]) + width * abs(matrix[0, 0])))
    new_height = int(np.ceil(height * abs(matrix[0, 0]) + width * abs(matrix[0, 1])))
    matrix[0, 2] += (new_width - width) / 2
    matrix[1, 2] += (new_height - height) / 2
    view = cv2.warpAffine(image, matrix, (new_width, new_height), borderValue=(127, 127, 127))
    return view, cv2.invertAffineTransform(matrix)


def merge_detections(rows):
    """合并多视角重复框，优先保留完整文字行，避免重复擦除同一块背景。"""
    accepted = []
    for row in sorted(rows, key=lambda row: cv2.contourArea(np.asarray(row[0], np.float32)), reverse=True):
        polygon = cv2.convexHull(np.asarray(row[0], np.float32))
        area = cv2.contourArea(polygon)
        if area <= 1:
            continue
        duplicate = False
        for existing in accepted:
            other = cv2.convexHull(np.asarray(existing[0], np.float32))
            overlap, _ = cv2.intersectConvexConvex(polygon, other)
            if overlap / min(area, cv2.contourArea(other)) > .65:
                duplicate = True
                break
        if not duplicate:
            accepted.append(row)
    return accepted


def scan_text(engine, image, sensitivity):
    height, width = image.shape[:2]
    scale = min(1., 1600 / max(height, width))
    work = cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image
    options = {'use_cls': False, 'box_thresh': .7 - sensitivity * .4, 'text_score': .3}
    rows, _ = engine(work, **options)
    detections = list(rows or [])
    tilted = any(abs(row[0][1][1] - row[0][0][1]) > abs(row[0][1][0] - row[0][0][0]) * .15
                 for row in detections)
    # 常规文字保留快速路径；空结果、倾斜文字和高敏感度使用增强搜索。
    if (not detections or tilted or sensitivity >= .7) and float(cv2.cvtColor(work, cv2.COLOR_BGR2GRAY).std()) > 2:
        lab = cv2.cvtColor(work, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(clipLimit=3., tileGridSize=(8, 8)).apply(lab[:, :, 0])
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        extra, _ = engine(enhanced, **options)
        detections.extend(extra or [])
        for view_source, angle in ((work, -20), (enhanced, -20), (work, 20), (enhanced, 20)):
            rotated, inverse = rotate_view(view_source, angle)
            extra, _ = engine(rotated, **options)
            for polygon, text, confidence in extra or []:
                points = cv2.transform(np.asarray([polygon], np.float32), inverse)[0]
                center = points.mean(axis=0)
                if 0 <= center[0] < work.shape[1] and 0 <= center[1] < work.shape[0]:
                    detections.append([points.tolist(), text, confidence])
    # 使用实际缩放比例（尺寸取整可能使横纵比例有微小差异）。
    mapped = []
    for polygon, text, confidence in detections:
        points = np.asarray(polygon, np.float32) * [width / work.shape[1], height / work.shape[0]]
        points[:, 0] = points[:, 0].clip(0, width)
        points[:, 1] = points[:, 1].clip(0, height)
        mapped.append([points.tolist(), text, confidence])
    return merge_detections(mapped)


def detect_candidates(image, sensitivity=.5):
    from core.watermark_detector import WatermarkDetector
    try:
        engine = get_ocr()
        rows = scan_text(engine, cv2.cvtColor(image, cv2.COLOR_RGB2BGR), sensitivity)
    except (ImportError, OSError, RuntimeError) as error:
        regions = WatermarkDetector(sensitivity=sensitivity).detect(image)
        return {
            'engine': 'cv', 'warning': f'OCR 暂不可用，已使用规则检测：{type(error).__name__}；可手动圈选。',
            'candidates': [{'region': list(region), 'polygon': None, 'text': '',
                            'text_confidence': None, 'watermark_score': None,
                            'reasons': ['规则检测，需人工确认']} for region in regions[:128]],
            'regions': [list(region) for region in regions[:128]],
        }
    height, width = image.shape[:2]
    candidates = rank_candidates(rows or [], width, height)
    warning = None if candidates else '未找到可靠的文字候选，不代表没有水印。可手动圈选；残缺文字、Logo 和低对比度水印仍可能漏检。'
    return {'engine': 'paddleocr-onnx', 'warning': warning,
            'candidates': candidates, 'regions': [item['region'] for item in candidates]}
