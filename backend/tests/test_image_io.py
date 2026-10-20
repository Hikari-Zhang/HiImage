"""图片文件读写工具测试。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from core.image_io import write_image


class WriteImageTests(unittest.TestCase):
    """验证图片写入的 Unicode 路径和失败处理。"""

    def test_write_image_supports_chinese_path(self) -> None:
        """Windows 中文路径下应能写入并读取图片。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "企业微信截图_upscaled.png"
            image = np.zeros((2, 3, 3), dtype=np.uint8)
            image[:, :, 1] = 255

            write_image(output_path, image)

            self.assertTrue(output_path.is_file())
            decoded = cv2.imdecode(
                np.fromfile(output_path, dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.shape, image.shape)
            self.assertTrue(np.array_equal(decoded, image))

    def test_write_image_raises_when_encoding_fails(self) -> None:
        """OpenCV 编码失败时必须抛错，不能假装文件已保存。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "result.png"
            image = np.zeros((1, 1, 3), dtype=np.uint8)

            with patch("cv2.imencode", return_value=(False, None)):
                with self.assertRaisesRegex(OSError, "图片编码失败"):
                    write_image(output_path, image)

            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
