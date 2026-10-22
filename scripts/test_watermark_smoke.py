"""真实 LaMa 推理验收；输出合成样本和可复查指标，不作为照片质量结论。

运行：node scripts/python.js scripts/test_watermark_smoke.py
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def main():
    from app.services.image_processing import inpaint_image
    from core.inpaint_regions import prepare_mask
    from core.paths import TMP_DIR
    output = TMP_DIR / 'watermark-acceptance'
    output.mkdir(parents=True, exist_ok=True)
    height, width = 1800, 2800
    yy, xx = np.mgrid[:height, :width]
    backgrounds = {
        'flat': np.full((height, width, 3), (80, 130, 170), dtype=np.uint8),
        'gradient': np.stack([70 + xx / 40, 100 + yy / 30, 150 + xx / 80], axis=-1).astype(np.uint8),
    }
    reports = []
    for name, clean in backgrounds.items():
        image = clean.copy()
        cv2.putText(image, 'TEST', (2520, 1690), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (245, 245, 245), 2)
        rois = [(2510, 1650, 2630, 1710)]
        mask = prepare_mask(image.shape, rois=rois, dilation=3)
        start = time.monotonic()
        result = inpaint_image(image, 'wm_lama', 'cpu', rois=rois, dilation=3)
        selected = mask > 0
        before = float(np.abs(image.astype(float) - clean)[selected].mean())
        after = float(np.abs(result.astype(float) - clean)[selected].mean())
        outside_equal = bool(np.array_equal(result[~selected], image[~selected]))
        report = dict(name=name, seconds=round(time.monotonic() - start, 2),
                      shape=list(result.shape), masked_mae_before=before,
                      masked_mae_after=after, outside_equal=outside_equal)
        reports.append(report)
        for suffix, pixels in [('input', image), ('result', result), ('clean', clean)]:
            cv2.imwrite(str(output / f'{name}-{suffix}.png'), cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))
        assert result.shape == image.shape and outside_equal
        assert after < before, report
    (output / 'report.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
    print(json.dumps(reports, indent=2))


if __name__ == '__main__':
    main()
