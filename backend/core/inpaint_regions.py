"""原分辨率遮罩处理、上下文裁剪与限定区域合成。"""
import cv2
import numpy as np


def prepare_mask(shape, mask=None, rois=None, dilation=0):
    height, width = shape[:2]
    if mask is None:
        mask = np.zeros((height, width), np.uint8)
        for x1, y1, x2, y2 in rois or []:
            x1, x2 = sorted((max(0, min(width, int(x1))), max(0, min(width, int(x2)))))
            y1, y2 = sorted((max(0, min(height, int(y1))), max(0, min(height, int(y2)))))
            mask[y1:y2, x1:x2] = 255
    elif mask.shape != (height, width):
        raise ValueError('遮罩与原图尺寸不一致')
    else:
        mask = np.array(mask, dtype=np.uint8, copy=True)
    if not 0 <= dilation <= 100:
        raise ValueError('遮罩膨胀范围应为 0–100')
    if dilation:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilation * 2 + 1,) * 2)
        mask = cv2.dilate(mask, kernel)
    return mask


def context_boxes(mask, margin=128):
    height, width = mask.shape
    contours, _ = cv2.findContours((mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        box = (max(0, x - margin), max(0, y - margin),
               min(width, x + w + margin), min(height, y + h + margin))
        # 重叠上下文合并，避免同一行文字的每个笔画单独加载模型。
        index = 0
        while index < len(boxes):
            other = boxes[index]
            if box[0] <= other[2] and other[0] <= box[2] and box[1] <= other[3] and other[1] <= box[3]:
                box = (min(box[0], other[0]), min(box[1], other[1]),
                       max(box[2], other[2]), max(box[3], other[3]))
                boxes.pop(index)
                index = 0
            else:
                index += 1
        boxes.append(box)
    return sorted(boxes, key=lambda box: (box[1], box[0]))


def repair_regions(image, mask, repair, margin=128, feather=2):
    """repair 接收局部 RGB 图和遮罩；不缩放，融合严格限制在遮罩内部。"""
    if mask.shape != image.shape[:2]:
        raise ValueError('遮罩与原图尺寸不一致')
    result = image.copy()
    for x1, y1, x2, y2 in context_boxes(mask, margin):
        original = image[y1:y2, x1:x2].copy()
        local_mask = mask[y1:y2, x1:x2].copy()
        repaired = repair(original.copy(), local_mask.copy())
        if repaired.shape != original.shape:
            raise ValueError('模型输出尺寸与修复区域不一致')
        alpha = local_mask.astype(np.float32) / 255
        if feather:
            distance = cv2.distanceTransform((local_mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
            alpha *= np.minimum(distance / feather, 1)
        selected = local_mask > 0
        blended = np.rint(repaired * alpha[..., None] + original * (1 - alpha[..., None])).clip(0, 255).astype(np.uint8)
        target = result[y1:y2, x1:x2]
        target[selected] = blended[selected]
    return result
