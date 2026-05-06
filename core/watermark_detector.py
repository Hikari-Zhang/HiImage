"""
水印自动检测模块 - 支持CV算法和ML模型
"""
import cv2
import numpy as np
from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class WatermarkDetector:
    """
    水印检测器 - 可扩展架构
    
    支持多种检测策略：
    1. CV算法检测（当前实现）
    2. ML模型检测（预留接口）
    """
    
    def __init__(self, sensitivity: float = 0.5, use_ml: bool = False):
        """
        初始化检测器
        
        :param sensitivity: 检测敏感度 (0.0-1.0)
                         越低 = 越严格（少但准）
                         越高 = 越宽松（多但可能误判）
        :param use_ml: 是否使用ML模型（如果可用）
        """
        self.sensitivity = sensitivity
        self.use_ml = use_ml
        self.ml_detector = None  # 预留ML模型接口
        
    def detect(self, image: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        检测水印区域（主入口）
        
        :param image: 输入图像 (BGR或RGB格式)
        :return: 检测到的水印区域列表 [(x1, y1, x2, y2), ...]
        """
        if self.use_ml and self.ml_detector is not None:
            return self.detect_watermark_ml(image)
        else:
            return self.detect_watermark_cv(image)
    
    def detect_watermark_cv(self, image: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        基于CV算法的半透明水印检测
        
        检测策略（按优先级）：
        1. 位置优先检测 - 检查常见水印位置（四角、边缘）
        2. 边缘检测 - 检测低对比度边缘（文字/logo特征）
        3. 频域分析 - 检测重复图案（平铺水印）
        
        :param image: 输入图像 (BGR格式，OpenCV默认)
        :return: 检测到的水印区域列表 [(x1, y1, x2, y2), ...]
        """
        logger.info("开始CV算法水印检测...")
        
        # 调用方传入的图像已是 RGB（main_window 在 load_image 时已转换），
        # 直接使用，不做二次颜色空间转换。
        image_rgb = image
            
        height, width = image.shape[:2]
        
        # 存储所有检测到的区域
        all_regions = []
        
        # 策略1: 位置优先检测（半透明水印常在四角/边缘）
        position_regions = self._detect_by_position(image_rgb, height, width)
        all_regions.extend(position_regions)
        logger.info(f"位置检测找到 {len(position_regions)} 个候选区域")
        
        # 策略2: 边缘检测（检测文字/logo的边缘）
        edge_regions = self._detect_by_edges(image_rgb)
        all_regions.extend(edge_regions)
        logger.info(f"边缘检测找到 {len(edge_regions)} 个候选区域")
        
        # 合并重叠区域
        if all_regions:
            merged_regions = self._merge_regions(all_regions, iou_threshold=0.3,
                                                 img_width=width, img_height=height)
            logger.info(f"合并后共 {len(merged_regions)} 个水印区域")
            return merged_regions
        else:
            logger.warning("未检测到水印区域")
            return []
    
    def _detect_by_position(self, image: np.ndarray, height: int, width: int) -> List[Tuple[int, int, int, int]]:
        """
        位置优先检测 - 检查常见水印位置
        
        半透明水印常出现在：
        - 右下角（最常见）
        - 左下角
        - 右上角
        - 左上角
        - 底部中央
        - 顶部中央
        
        :param image: RGB图像
        :param height: 图像高度
        :param width: 图像宽度
        :return: 候选区域列表
        """
        regions = []
        
        # 定义候选区域（相对于图像大小的比例）
        # 敏感度越高，搜索区域越大
        search_ratio = 0.3 + self.sensitivity * 0.2  # 0.3-0.5
        
        candidate_positions = [
            # (x1_ratio, y1_ratio, x2_ratio, y2_ratio)
            (1 - search_ratio, 1 - search_ratio, 1.0, 1.0),  # 右下角
            (0.0, 1 - search_ratio, search_ratio, 1.0),        # 左下角
            (1 - search_ratio, 0.0, 1.0, search_ratio),        # 右上角
            (0.0, 0.0, search_ratio, search_ratio),            # 左上角
            (0.5 - search_ratio/2, 1 - search_ratio/2, 0.5 + search_ratio/2, 1.0),  # 底部中央
            (0.5 - search_ratio/2, 0.0, 0.5 + search_ratio/2, search_ratio/2),      # 顶部中央
        ]
        
        for x1_ratio, y1_ratio, x2_ratio, y2_ratio in candidate_positions:
            x1 = int(width * x1_ratio)
            y1 = int(height * y1_ratio)
            x2 = int(width * x2_ratio)
            y2 = int(height * y2_ratio)
            
            # 在该区域内检测是否有水印特征
            region = image[y1:y2, x1:x2]
            if self._has_watermark_features(region):
                regions.append((x1, y1, x2, y2))
                
        return regions
    
    def _detect_by_edges(self, image: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        边缘检测 - 检测低对比度的文字/logo边缘
        
        使用多尺度边缘检测：
        1. LAB颜色空间的L通道（对低对比度更敏感）
        2. 自适应Canny边缘检测
        3. 形态学操作连接断裂的边缘
        4. 查找包含边缘的连通区域
        5. 合并同一行的文字轮廓
        
        :param image: RGB图像
        :return: 候选区域列表
        """
        # 转换为LAB颜色空间（L通道对低对比度更敏感）
        lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
        l_channel = lab[:, :, 0]
        
        # 使用自适应阈值（根据敏感度调整）
        block_size = 11
        c_value = int(5 + (1 - self.sensitivity) * 10)  # 敏感度越高，c_value越小
        
        # 自适应阈值化
        binary = cv2.adaptiveThreshold(
            l_channel, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, block_size, c_value
        )
        
        # 形态学操作 - 连接断裂的边缘（文字笔画）
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        
        # 查找轮廓
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        height, width = image.shape[:2]
        min_area = int(width * height * 0.0001)  # 最小面积（图像面积的0.01%）
        max_area = int(width * height * 0.3)      # 最大面积（图像面积的30%）
        
        # 收集有效的边界框
        boxes = []
        for contour in contours:
            area = cv2.contourArea(contour)
            
            # 过滤太小或太大的区域
            if area < min_area or area > max_area:
                continue
                
            # 获取边界框
            x, y, w, h = cv2.boundingRect(contour)
            
            # 过滤极端长宽比（可能是误检测）
            aspect_ratio = w / h if h > 0 else 0
            if aspect_ratio > 10 or aspect_ratio < 0.1:
                continue
                
            boxes.append((x, y, x + w, y + h))
            
        # 合并同一行的文字轮廓
        merged_boxes = self._merge_text_contours(boxes)
        
        # 扩展边界框（确保完全覆盖水印）
        regions = []
        padding = 10
        for (x1, y1, x2, y2) in merged_boxes:
            x1 = max(0, x1 - padding)
            y1 = max(0, y1 - padding)
            x2 = min(width, x2 + padding)
            y2 = min(height, y2 + padding)
            regions.append((x1, y1, x2, y2))
            
        return regions
    
    def _merge_text_contours(self, boxes: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
        """
        合并同一行的文字轮廓
        
        文字水印的特点：
        1. 同一行的文字y坐标相近
        2. 字符之间距离较近
        3. 整体形成一个矩形区域
        
        :param boxes: 边界框列表 [(x1, y1, x2, y2), ...]
        :return: 合并后的边界框列表
        """
        if not boxes:
            return []
        
        # 按y坐标分组（检测同一行的文字）
        # 使用聚类方法：将y坐标相近的框分为一组
        boxes_with_center = [(x1, y1, x2, y2, (y1 + y2) // 2) for (x1, y1, x2, y2) in boxes]
        
        # 按y中心坐标排序
        boxes_with_center.sort(key=lambda b: b[4])
        
        # 分组：y坐标相差小于阈值的分为一组
        height_threshold = 20  # 同一行文字的y坐标最大差值
        
        groups = []
        current_group = [boxes_with_center[0]]
        
        for i in range(1, len(boxes_with_center)):
            _, y1_prev, _, y2_prev, y_center_prev = current_group[-1]
            x1_curr, y1_curr, x2_curr, y2_curr, y_center_curr = boxes_with_center[i]
            
            # 如果y中心坐标相近，则分为同一组
            if abs(y_center_curr - y_center_prev) < height_threshold:
                current_group.append(boxes_with_center[i])
            else:
                # 完成当前组，开始新组
                groups.append(current_group)
                current_group = [boxes_with_center[i]]
        
        # 添加最后一组
        groups.append(current_group)
        
        # 对每组合并边界框
        merged = []
        for group in groups:
            # 找到组的最小和最大坐标
            min_x = min(b[0] for b in group)
            min_y = min(b[1] for b in group)
            max_x = max(b[2] for b in group)
            max_y = max(b[3] for b in group)
            
            merged.append((min_x, min_y, max_x, max_y))
        
        return merged
    
    def _has_watermark_features(self, region: np.ndarray) -> bool:
        """
        判断区域是否包含水印特征
        
        :param region: 图像区域 (RGB)
        :return: 是否包含水印特征
        """
        if region.size == 0:
            return False
            
        # 转换为灰度
        gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
        
        # 特征1: 边缘密度（文字/logo有大量边缘）
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / edges.size
        
        # 特征2: 颜色方差（水印通常与背景不同）
        color_std = np.std(region)
        
        # 特征3: 是否存在浅色像素（许多水印是白色/浅色）
        light_pixels = np.sum(np.mean(region, axis=2) > 200)
        light_ratio = light_pixels / region.size * 3  # *3 因为region.size是像素数*3
        
        # 综合判断（根据敏感度调整阈值）
        edge_threshold = 0.01 + (1 - self.sensitivity) * 0.05
        color_threshold = 20 + (1 - self.sensitivity) * 30
        light_threshold = 0.05 + (1 - self.sensitivity) * 0.15
        
        if edge_density > edge_threshold and (color_std > color_threshold or light_ratio > light_threshold):
            return True
            
        return False
    
    def _merge_regions(self, regions: List[Tuple[int, int, int, int]],
                        iou_threshold: float = 0.3,
                        img_width: int = 0, img_height: int = 0) -> List[Tuple[int, int, int, int]]:
        """
        合并重叠的候选区域

        :param regions: 候选区域列表
        :param iou_threshold: IoU阈值（重叠度大于此值则合并）
        :param img_width: 图像宽度，用于边界保护（0 表示不限制）
        :param img_height: 图像高度，用于边界保护（0 表示不限制）
        :return: 合并后的区域列表
        """
        if not regions:
            return []
        
        # 转换为numpy数组方便计算
        boxes = np.array(regions)
        
        # 计算所有框的面积
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        
        # 按面积排序（从大到小）
        order = areas.argsort()[::-1]
        
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            
            # 计算当前框与其余框的IoU
            xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
            yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
            xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
            yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
            
            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h
            
            union = areas[i] + areas[order[1:]] - inter
            iou = inter / union
            
            # 保留IoU小于阈值的框
            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]
            
        # 返回合并后的框
        merged = boxes[keep].astype(int).tolist()

        # 对合并后的框进行扩张（确保完全覆盖水印），同时夹住边界
        # image_shape 在此方法里不可直接获得，用一个足够大的哨兵值；
        # create_mask / 调用方会再次做边界保护。
        dilated = []
        for x1, y1, x2, y2 in merged:
            padding = 15
            dilated.append((
                max(0, x1 - padding),
                max(0, y1 - padding),
                x2 + padding if img_width == 0 else min(img_width, x2 + padding),
                y2 + padding if img_height == 0 else min(img_height, y2 + padding),
            ))
            
        return dilated
    
    def detect_watermark_ml(self, image: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        基于ML模型的水印检测（预留接口）
        
        未来可以集成：
        - YOLO (训练水印检测数据集)
        - Segment Anything (SAM)
        - 专用水印检测模型
        
        :param image: 输入图像
        :return: 检测到的水印区域列表
        """
        if self.ml_detector is None:
            logger.warning("ML检测器未初始化，回退到CV算法")
            return self.detect_watermark_cv(image)
        
        # TODO: 实现ML模型推理
        # 示例：
        # results = self.ml_detector.predict(image)
        # regions = self._parse_ml_results(results)
        # return regions
        
        raise NotImplementedError("ML检测模型尚未实现")
    
    def load_ml_model(self, model_path: str):
        """
        加载ML检测模型（预留接口）
        
        :param model_path: 模型文件路径
        """
        # TODO: 实现模型加载
        # 示例：
        # self.ml_detector = YOLO(model_path)
        # self.use_ml = True
        
        logger.info(f"加载ML模型: {model_path}")
        raise NotImplementedError("ML模型加载尚未实现")


def auto_detect_watermark(image: np.ndarray, sensitivity: float = 0.5) -> List[Tuple[int, int, int, int]]:
    """
    便捷函数：自动检测水印
    
    :param image: 输入图像
    :param sensitivity: 检测敏感度 (0.0-1.0)
    :return: 检测到的水印区域列表
    """
    detector = WatermarkDetector(sensitivity=sensitivity, use_ml=False)
    return detector.detect(image)
