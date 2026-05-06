"""
预览面板 - 显示原图、处理结果、ROI列表
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QListWidget, QComboBox, QHBoxLayout,
    QGroupBox, QSpinBox, QScrollArea, QToolButton,
    QSlider, QCheckBox
)
from PySide6.QtGui import QPixmap, QWheelEvent, QImage
from PySide6.QtCore import Qt, Signal

from core.inpainter import MODEL_GROUPS
from core.upscaler import UPSCALE_MODEL_LIST


class ZoomablePreviewLabel(QLabel):
    """可缩放的预览标签"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("border: 1px solid gray; padding: 5px;")
        self.original_pixmap = None
        self.zoom_factor = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 5.0

    def set_pixmap(self, pixmap):
        """设置原始pixmap"""
        self.original_pixmap = pixmap
        self.zoom_factor = 1.0
        self._update_pixmap()

    def _update_pixmap(self):
        """根据缩放因子更新显示"""
        if self.original_pixmap is None:
            return

        new_width = int(self.original_pixmap.width() * self.zoom_factor)
        new_height = int(self.original_pixmap.height() * self.zoom_factor)

        scaled_pixmap = self.original_pixmap.scaled(
            new_width, new_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        super().setPixmap(scaled_pixmap)

    def wheelEvent(self, event: QWheelEvent):
        """鼠标滚轮缩放"""
        if self.original_pixmap is None:
            return

        angle = event.angleDelta().y()
        factor = 1.2 if angle > 0 else 0.8
        new_zoom = self.zoom_factor * factor

        if self.min_zoom <= new_zoom <= self.max_zoom:
            self.zoom_factor = new_zoom
            self._update_pixmap()

    def zoom_in(self):
        """放大"""
        if self.original_pixmap is None:
            return
        new_zoom = self.zoom_factor * 1.2
        if new_zoom <= self.max_zoom:
            self.zoom_factor = new_zoom
            self._update_pixmap()

    def zoom_out(self):
        """缩小"""
        if self.original_pixmap is None:
            return
        new_zoom = self.zoom_factor * 0.8
        if new_zoom >= self.min_zoom:
            self.zoom_factor = new_zoom
            self._update_pixmap()

    def fit_to_view(self):
        """适应视图"""
        if self.original_pixmap is None:
            return
        parent_size = self.parent().size() if self.parent() else self.size()
        scale_w = parent_size.width() / self.original_pixmap.width()
        scale_h = parent_size.height() / self.original_pixmap.height()
        self.zoom_factor = min(scale_w, scale_h) * 0.9
        self.zoom_factor = max(self.min_zoom, min(self.max_zoom, self.zoom_factor))
        self._update_pixmap()

    def reset_zoom(self):
        """重置缩放"""
        self.zoom_factor = 1.0
        self._update_pixmap()


class PreviewPanel(QWidget):
    """预览面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)

        # 标题
        title = QLabel("控制面板")
        title.setStyleSheet("font-size: 16px; font-weight: bold; margin: 10px;")
        layout.addWidget(title)

        # ROI列表
        roi_label = QLabel("水印区域：")
        layout.addWidget(roi_label)

        self.roi_list = QListWidget()
        self.roi_list.setMaximumHeight(150)
        self.roi_list.setSelectionMode(QListWidget.MultiSelection)  # 允许多选
        layout.addWidget(self.roi_list)

        # 按钮：自动检测水印
        self.auto_detect_btn = QPushButton("🔍 自动检测水印")
        self.auto_detect_btn.setStyleSheet("background-color: #2196F3; color: white; padding: 8px; font-weight: bold;")
        self.auto_detect_btn.setToolTip("使用CV算法自动检测半透明水印")
        layout.addWidget(self.auto_detect_btn)

        # 敏感度滑块
        sensitivity_layout = QHBoxLayout()
        sensitivity_label = QLabel("检测敏感度:")
        self.sensitivity_slider = QSlider(Qt.Horizontal)
        self.sensitivity_slider.setRange(0, 100)
        self.sensitivity_slider.setValue(50)
        self.sensitivity_slider.setToolTip("调整检测敏感度（越低越严格，越高越宽松）")
        self.sensitivity_value = QLabel("0.50")
        sensitivity_layout.addWidget(sensitivity_label)
        sensitivity_layout.addWidget(self.sensitivity_slider)
        sensitivity_layout.addWidget(self.sensitivity_value)
        layout.addLayout(sensitivity_layout)

        # 分隔线
        layout.addSpacing(10)

        # 按钮：清除所有ROI + 删除选中
        button_layout = QHBoxLayout()
        
        self.clear_btn = QPushButton("清除所有区域")
        button_layout.addWidget(self.clear_btn)
        
        self.delete_btn = QPushButton("删除选中")
        self.delete_btn.setStyleSheet("background-color: #f44336; color: white; padding: 8px;")
        self.delete_btn.setToolTip("删除选中的水印区域")
        button_layout.addWidget(self.delete_btn)
        
        layout.addLayout(button_layout)

        # 分隔线
        layout.addSpacing(10)

        # 高级选项组
        advanced_group = QGroupBox("高级选项")
        advanced_layout = QVBoxLayout()

        # 模型选择
        model_layout = QHBoxLayout()
        model_label = QLabel("AI模型:")
        self.model_combo = QComboBox()
        self.model_combo.setToolTip("选择修复模型，鼠标悬停可查看说明")
        # 按分组填充：分组标题不可选，模型条目可选
        for group_label, models in MODEL_GROUPS:
            # 插入分组标题（置灰、不可选）
            self.model_combo.addItem(group_label)
            idx = self.model_combo.count() - 1
            item = self.model_combo.model().item(idx)
            item.setEnabled(False)
            # 插入该组的模型
            for model_id, display_name, description in models:
                self.model_combo.addItem(f"  {display_name}", userData=model_id)
                item = self.model_combo.model().item(self.model_combo.count() - 1)
                item.setToolTip(description)
        # 默认选中 lama（第一个真实条目，索引 1）
        self.model_combo.setCurrentIndex(1)
        model_layout.addWidget(model_label)
        model_layout.addWidget(self.model_combo)
        advanced_layout.addLayout(model_layout)

        # 设备选择
        device_layout = QHBoxLayout()
        device_label = QLabel("计算设备:")
        self.device_combo = QComboBox()
        self.device_combo.addItems(['mps', 'cpu', 'cuda'])
        self.device_combo.setCurrentText('mps')
        device_layout.addWidget(device_label)
        device_layout.addWidget(self.device_combo)
        advanced_layout.addLayout(device_layout)

        # 遮罩扩张
        dilation_layout = QHBoxLayout()
        dilation_label = QLabel("遮罩扩张:")
        self.dilation_spin = QSpinBox()
        self.dilation_spin.setRange(0, 50)
        self.dilation_spin.setValue(10)
        self.dilation_spin.setSuffix(" px")
        dilation_layout.addWidget(dilation_label)
        dilation_layout.addWidget(self.dilation_spin)
        advanced_layout.addLayout(dilation_layout)

        # 禁用 NSFW 检查（SD 系列模型需要开启）
        self.disable_nsfw_check = QCheckBox("禁用 NSFW 安全检查")
        self.disable_nsfw_check.setChecked(False)
        self.disable_nsfw_check.setToolTip(
            "SD / SDXL / BrushNet / PowerPaint 等扩散模型必须勾选，否则会报错；\n"
            "LaMa、MiGAN 等本地模型无需勾选。"
        )
        advanced_layout.addWidget(self.disable_nsfw_check)

        advanced_group.setLayout(advanced_layout)
        layout.addWidget(advanced_group)

        # 分隔线
        layout.addSpacing(10)

        # ── 超分辨率组 ──────────────────────────────────────────────────
        upscale_group = QGroupBox("超分辨率（增清）")
        upscale_layout = QVBoxLayout()

        # 模型选择
        upscale_model_layout = QHBoxLayout()
        upscale_model_label = QLabel("模型:")
        self.upscale_model_combo = QComboBox()
        self.upscale_model_combo.setToolTip("选择超分模型，鼠标悬停可查看说明")
        for model_name, scale, weight_file, url, display_name, description in UPSCALE_MODEL_LIST:
            self.upscale_model_combo.addItem(display_name, userData=model_name)
            item = self.upscale_model_combo.model().item(self.upscale_model_combo.count() - 1)
            item.setToolTip(description)
        self.upscale_model_combo.setCurrentIndex(0)  # 默认 4x 通用
        upscale_model_layout.addWidget(upscale_model_label)
        upscale_model_layout.addWidget(self.upscale_model_combo)
        upscale_layout.addLayout(upscale_model_layout)

        # 操作对象提示
        self.upscale_source_label = QLabel("对象：当前图像")
        self.upscale_source_label.setStyleSheet("color: gray; font-size: 11px;")
        upscale_layout.addWidget(self.upscale_source_label)

        # 按钮：增加分辨率
        self.upscale_btn = QPushButton("✨ 增加分辨率")
        self.upscale_btn.setStyleSheet(
            "background-color: #9C27B0; color: white; padding: 8px; font-weight: bold;"
        )
        self.upscale_btn.setToolTip(
            "使用 Real-ESRGAN 对当前图像进行 AI 超分辨率放大（2x/4x）\n"
            "首次运行需下载模型（~18-65 MB）\n"
            "大图处理时内存占用较高，建议先去除水印再超分"
        )
        upscale_layout.addWidget(self.upscale_btn)

        upscale_group.setLayout(upscale_layout)
        layout.addWidget(upscale_group)

        # 分隔线
        layout.addSpacing(10)

        # 预览标签
        preview_label = QLabel("预览：")
        layout.addWidget(preview_label)

        # 预览缩放控制按钮
        zoom_layout = QHBoxLayout()
        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setText("🔍+")
        self.zoom_in_btn.setToolTip("放大")
        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setText("🔍-")
        self.zoom_out_btn.setToolTip("缩小")
        self.fit_view_btn = QToolButton()
        self.fit_view_btn.setText("⊞")
        self.fit_view_btn.setToolTip("适应窗口")
        self.reset_zoom_btn = QToolButton()
        self.reset_zoom_btn.setText("1:1")
        self.reset_zoom_btn.setToolTip("原始大小")
        self.fullscreen_btn = QToolButton()
        self.fullscreen_btn.setText("⛶")
        self.fullscreen_btn.setToolTip("全屏预览")

        zoom_layout.addWidget(self.zoom_in_btn)
        zoom_layout.addWidget(self.zoom_out_btn)
        zoom_layout.addWidget(self.fit_view_btn)
        zoom_layout.addWidget(self.reset_zoom_btn)
        zoom_layout.addWidget(self.fullscreen_btn)
        zoom_layout.addStretch()
        layout.addLayout(zoom_layout)

        # 使用可缩放的预览标签（放在滚动区域中）
        self.scroll_area = QScrollArea()
        self.preview_label = ZoomablePreviewLabel()
        self.scroll_area.setWidget(self.preview_label)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setMinimumSize(250, 200)
        layout.addWidget(self.scroll_area)

        # 按钮：去除水印
        self.remove_btn = QPushButton("去除水印")
        self.remove_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px; font-weight: bold;")
        layout.addWidget(self.remove_btn)

        # 按钮：以结果继续处理
        self.use_result_btn = QPushButton("↩ 以结果继续处理")
        self.use_result_btn.setStyleSheet("background-color: #FF9800; color: white; padding: 8px; font-weight: bold;")
        self.use_result_btn.setToolTip("将当前处理结果作为新的原图，继续框选并去除剩余水印")
        self.use_result_btn.setEnabled(False)
        layout.addWidget(self.use_result_btn)

        # 按钮：保存结果
        self.save_btn = QPushButton("保存结果")
        layout.addWidget(self.save_btn)

        # 占位符
        layout.addStretch()

        # 连接缩放按钮信号
        self.zoom_in_btn.clicked.connect(self.preview_label.zoom_in)
        self.zoom_out_btn.clicked.connect(self.preview_label.zoom_out)
        self.fit_view_btn.clicked.connect(self.preview_label.fit_to_view)
        self.reset_zoom_btn.clicked.connect(self.preview_label.reset_zoom)

        # 连接敏感度滑块信号
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity_changed)

    def _on_sensitivity_changed(self, value):
        """敏感度滑块值变化"""
        sensitivity = value / 100.0
        self.sensitivity_value.setText(f"{sensitivity:.2f}")

    def get_settings(self):
        """获取当前水印去除设置"""
        sensitivity = self.sensitivity_slider.value() / 100.0
        # model_id 存在 userData 中，避免受显示文字前缀空格影响
        model_id = self.model_combo.currentData() or self.model_combo.currentText().strip()
        return {
            'model': model_id,
            'device': self.device_combo.currentText(),
            'dilation': self.dilation_spin.value(),
            'sensitivity': sensitivity,
            'disable_nsfw': self.disable_nsfw_check.isChecked(),
        }

    def get_upscale_settings(self):
        """获取当前超分辨率设置"""
        model_name = self.upscale_model_combo.currentData() or 'RealESRGAN_x4plus'
        return {
            'upscale_model': model_name,
            'device': self.device_combo.currentText(),
        }

    def set_upscale_source_hint(self, has_result: bool):
        """更新超分辨率操作对象提示（显示是对原图还是处理结果超分）"""
        if has_result:
            self.upscale_source_label.setText("对象：当前处理结果")
            self.upscale_source_label.setStyleSheet("color: #FF9800; font-size: 11px;")
        else:
            self.upscale_source_label.setText("对象：当前图像")
            self.upscale_source_label.setStyleSheet("color: gray; font-size: 11px;")

    def update_roi_list(self, roi_list):
        """更新ROI列表显示"""
        self.roi_list.clear()
        for i, roi in enumerate(roi_list):
            x1, y1, x2, y2 = roi
            width = x2 - x1
            height = y2 - y1
            text = f"区域 {i+1}: ({int(x1)}, {int(y1)}) - {int(width)}x{int(height)}"
            self.roi_list.addItem(text)

    def update_preview(self, image):
        """更新预览图像"""
        if image is None:
            self.preview_label.setText("预览失败")
            self.use_result_btn.setEnabled(False)
            return

        try:
            height, width, channel = image.shape
            bytes_per_line = 3 * width
            qimage = QImage(image.data, width, height, bytes_per_line, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qimage)

            # 使用自定义方法设置pixmap
            self.preview_label.set_pixmap(pixmap)
            # 初次加载时适应视图
            self.preview_label.fit_to_view()
            self.use_result_btn.setEnabled(True)
        except Exception as e:
            self.preview_label.setText(f"预览错误: {str(e)}")
            self.use_result_btn.setEnabled(False)
