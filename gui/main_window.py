"""
主窗口 - ClearWaterMark GUI
"""
import sys
import os
import cv2
import numpy as np
from PySide6.QtWidgets import (QMainWindow, QVBoxLayout, QHBoxLayout,
                             QWidget, QToolBar, QFileDialog,
                             QMessageBox, QApplication, QProgressBar)
from PySide6.QtCore import Qt, QRectF, QThread, Signal
from PySide6.QtGui import QAction, QPixmap, QImage

from .image_view import ImageView
from .preview_panel import PreviewPanel
from .fullscreen_preview import FullScreenPreview
from core.inpainter import Inpainter
from core.watermark_detector import auto_detect_watermark, WatermarkDetector
from core.upscaler import Upscaler


class WorkerThread(QThread):
    """工作线程 - 用于异步执行水印去除"""
    finished = Signal(object)  # 结果图像
    error = Signal(str)  # 错误信息

    def __init__(self, inpainter, image, rois):
        super().__init__()
        self.inpainter = inpainter
        self.image = image
        self.rois = rois

    def run(self):
        try:
            result = self.inpainter.remove_watermark(self.image, self.rois)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class UpscaleWorkerThread(QThread):
    """工作线程 - 用于异步执行超分辨率放大"""
    finished = Signal(object)  # 结果图像
    error = Signal(str)        # 错误信息

    def __init__(self, upscaler: Upscaler, image: np.ndarray):
        super().__init__()
        self.upscaler = upscaler
        self.image = image

    def run(self):
        try:
            result = self.upscaler.upscale(self.image)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    """主窗口类"""

    def __init__(self):
        super().__init__()
        self.image = None            # 原始图像 (numpy array, RGB)
        self.current_image = None   # 当前显示的图像
        self.result_image = None    # 去除水印后的图像
        self._current_file_path = None  # 当前打开的文件路径
        self.inpainter = Inpainter()
        self.worker = None
        self.upscale_worker = None
        self.init_ui()

    # ------------------------------------------------------------------
    # UI 初始化
    # ------------------------------------------------------------------

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("ClearWaterMark - 图片水印清除工具")
        self.setGeometry(100, 100, 1200, 800)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.main_layout = QHBoxLayout(self.central_widget)

        self.create_image_view()
        self.create_right_panel()

        self.main_layout.addWidget(self.image_view)
        self.main_layout.addWidget(self.right_panel)

        self.create_menu_bar()
        self.create_tool_bar()

        # 进度条（处理时显示）
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)   # 不确定进度（动画条）
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)

        self.statusBar().showMessage("就绪")

        self.connect_signals()

    def create_image_view(self):
        self.image_view = ImageView()
        self.image_view.setMinimumSize(600, 400)

    def create_right_panel(self):
        self.right_panel = PreviewPanel()
        self.right_panel.setMinimumWidth(300)
        self.right_panel.setMaximumWidth(400)

    def connect_signals(self):
        self.image_view.roi_selected.connect(self.on_roi_selected)
        self.image_view.roi_changed.connect(self.on_roi_changed)
        self.image_view.file_dropped.connect(self.load_image)

        self.right_panel.clear_btn.clicked.connect(self.clear_all_rois)
        self.right_panel.remove_btn.clicked.connect(self.remove_watermark)
        self.right_panel.save_btn.clicked.connect(self.save_result)
        self.right_panel.auto_detect_btn.clicked.connect(self.auto_detect_watermark)
        self.right_panel.delete_btn.clicked.connect(self.delete_selected_roi)
        self.right_panel.fullscreen_btn.clicked.connect(self.open_fullscreen_preview)
        self.right_panel.use_result_btn.clicked.connect(self.use_result_as_source)
        self.right_panel.upscale_btn.clicked.connect(self.upscale_image)

    def create_menu_bar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件")

        open_action = QAction("打开", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_image)
        file_menu.addAction(open_action)

        save_action = QAction("保存结果", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_result)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        exit_action = QAction("退出", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 工具菜单
        tools_menu = menubar.addMenu("工具")

        upscale_action = QAction("增加分辨率", self)
        upscale_action.setShortcut("Ctrl+U")
        upscale_action.setToolTip("使用 Real-ESRGAN 对当前图像进行超分辨率放大")
        upscale_action.triggered.connect(self.upscale_image)
        tools_menu.addAction(upscale_action)

    def create_tool_bar(self):
        toolbar = QToolBar("主工具栏")
        self.addToolBar(toolbar)

        open_action = QAction("打开图片", self)
        open_action.triggered.connect(self.open_image)
        toolbar.addAction(open_action)

    # ------------------------------------------------------------------
    # 文件操作
    # ------------------------------------------------------------------

    def open_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "打开图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.tiff)"
        )
        if file_path:
            self.load_image(file_path)

    def load_image(self, file_path):
        try:
            img = cv2.imread(file_path)
            if img is None:
                QMessageBox.warning(self, "错误", "无法加载图片！")
                return

            self.image = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            self.current_image = self.image.copy()
            self.result_image = None
            self._current_file_path = file_path

            self.display_image(self.current_image)
            self.image_view.clear_all_rois()
            self.right_panel.update_preview(None)
            self.statusBar().showMessage(f"已加载: {file_path}")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载图片失败: {str(e)}")

    def display_image(self, image):
        try:
            self.image_view.clear_scene()

            height, width, channel = image.shape
            bytes_per_line = 3 * width
            qimage = QImage(image.data, width, height, bytes_per_line, QImage.Format_RGB888)

            pixmap = QPixmap.fromImage(qimage)
            self.image_view.scene().addPixmap(pixmap)
            self.image_view.scene().setSceneRect(QRectF(pixmap.rect()))
            self.image_view.fitInView(self.image_view.scene().sceneRect(), Qt.KeepAspectRatio)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"显示图片失败: {str(e)}")

    def save_result(self):
        """保存处理结果（Ctrl+S）"""
        if self.result_image is None:
            QMessageBox.warning(self, "警告", "没有处理结果可保存！请先执行水印去除。")
            return

        # 默认保存到项目 output 目录，保留原文件名
        project_output = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output'
        )
        os.makedirs(project_output, exist_ok=True)

        if self._current_file_path:
            base_name = os.path.splitext(os.path.basename(self._current_file_path))[0]
            default_name = base_name + "_result.png"
        else:
            default_name = "result.png"

        default_path = os.path.join(project_output, default_name)

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存结果", default_path,
            "PNG文件 (*.png);;JPEG文件 (*.jpg *.jpeg);;所有文件 (*.*)"
        )

        if file_path:
            try:
                save_image = cv2.cvtColor(self.result_image, cv2.COLOR_RGB2BGR)
                cv2.imwrite(file_path, save_image)
                self.statusBar().showMessage(f"已保存: {file_path}")
                QMessageBox.information(self, "成功", "结果保存成功！")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存结果失败: {str(e)}")

    # ------------------------------------------------------------------
    # ROI 操作
    # ------------------------------------------------------------------

    def _get_roi_tuples(self):
        """将 ImageView 中的 QRectF 列表转换为 (x1, y1, x2, y2) 元组列表"""
        return [
            (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())
            for rect in self.image_view.get_all_rois()
        ]

    def on_roi_selected(self, rect):
        pass

    def on_roi_changed(self):
        self.right_panel.update_roi_list(self._get_roi_tuples())

    def clear_all_rois(self):
        self.image_view.clear_all_rois()
        self.right_panel.update_roi_list([])

    def delete_selected_roi(self):
        selected_items = self.right_panel.roi_list.selectedItems()

        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择要删除的水印区域！")
            return

        selected_rows = sorted(
            [self.right_panel.roi_list.row(item) for item in selected_items],
            reverse=True
        )

        for row in selected_rows:
            if not self.image_view.remove_roi_at_index(row):
                QMessageBox.warning(self, "错误", f"删除区域 {row + 1} 失败！")
                break

        self.right_panel.update_roi_list(self._get_roi_tuples())
        self.statusBar().showMessage(f"已删除 {len(selected_rows)} 个水印区域")

    # ------------------------------------------------------------------
    # 水印操作
    # ------------------------------------------------------------------

    def auto_detect_watermark(self):
        if self.current_image is None:
            QMessageBox.warning(self, "警告", "请先打开图片！")
            return

        settings = self.right_panel.get_settings()
        self.statusBar().showMessage("正在自动检测水印...")

        try:
            detector = WatermarkDetector(sensitivity=settings['sensitivity'])
            detected_regions = detector.detect(self.current_image)

            if not detected_regions:
                QMessageBox.information(self, "检测结果", "未检测到水印区域。\n\n尝试提高敏感度后重试。")
                self.statusBar().showMessage("未检测到水印")
                return

            self.image_view.clear_all_rois()
            for (x1, y1, x2, y2) in detected_regions:
                self.image_view.add_roi(QRectF(x1, y1, x2 - x1, y2 - y1))

            self.on_roi_changed()
            self.statusBar().showMessage(f"检测到 {len(detected_regions)} 个水印区域")
            QMessageBox.information(
                self, "检测完成",
                f"检测到 {len(detected_regions)} 个水印区域。\n\n"
                "可以手动调整或删除不准确的选择，然后点击'去除水印'。"
            )

        except Exception as e:
            QMessageBox.critical(self, "错误", f"自动检测失败: {str(e)}")
            self.statusBar().showMessage("检测失败")

    def remove_watermark(self):
        if self.current_image is None:
            QMessageBox.warning(self, "警告", "请先打开图片！")
            return

        roi_tuples = self._get_roi_tuples()
        if not roi_tuples:
            QMessageBox.warning(self, "警告", "请先选择水印区域！")
            return

        settings = self.right_panel.get_settings()
        print(f"使用设置: 模型={settings['model']}, 设备={settings['device']}, 遮罩扩张={settings['dilation']}px, 禁用NSFW={settings['disable_nsfw']}")

        self.inpainter = Inpainter(
            model_name=settings['model'],
            device=settings['device'],
            dilation=settings['dilation'],
            disable_nsfw=settings['disable_nsfw'],
        )

        # 禁用按钮，显示进度条
        self.right_panel.remove_btn.setEnabled(False)
        self.right_panel.remove_btn.setText("处理中...")
        self.progress_bar.setVisible(True)
        self.statusBar().showMessage("正在去除水印，请稍候...")

        self.worker = WorkerThread(self.inpainter, self.current_image, roi_tuples)
        self.worker.finished.connect(self.on_removal_finished)
        self.worker.error.connect(self.on_removal_error)
        self.worker.start()

    def on_removal_finished(self, result):
        self.result_image = result

        self.right_panel.remove_btn.setEnabled(True)
        self.right_panel.remove_btn.setText("去除水印")
        self.progress_bar.setVisible(False)

        self.right_panel.update_preview(self.result_image)
        self.right_panel.set_upscale_source_hint(has_result=True)
        self.statusBar().showMessage("水印去除完成！")

    def use_result_as_source(self):
        """将当前处理结果提升为新的原图，继续处理"""
        if self.result_image is None:
            return
        self.current_image = self.result_image.copy()
        self.result_image = None
        self.display_image(self.current_image)
        self.image_view.clear_all_rois()
        self.right_panel.update_preview(None)
        self.right_panel.update_roi_list([])
        self.right_panel.set_upscale_source_hint(has_result=False)
        self.statusBar().showMessage("已将处理结果作为新原图，可继续框选水印区域")

    def on_removal_error(self, error_msg):
        self.right_panel.remove_btn.setEnabled(True)
        self.right_panel.remove_btn.setText("去除水印")
        self.progress_bar.setVisible(False)

        QMessageBox.critical(self, "错误", f"水印去除失败: {error_msg}")
        self.statusBar().showMessage("水印去除失败")

    # ------------------------------------------------------------------
    # 预览
    # ------------------------------------------------------------------

    def open_fullscreen_preview(self):
        image_to_preview = self.result_image if self.result_image is not None else self.current_image
        if image_to_preview is None:
            QMessageBox.warning(self, "警告", "没有可预览的图像！")
            return

        try:
            preview_dialog = FullScreenPreview(self)
            preview_dialog.set_image(image_to_preview)
            preview_dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开全屏预览失败: {str(e)}")

    # ------------------------------------------------------------------
    # 超分辨率
    # ------------------------------------------------------------------

    def upscale_image(self):
        """触发超分辨率放大（优先对处理结果超分，否则对当前图像超分）"""
        # 优先对已有处理结果超分，否则对当前图像超分
        source = self.result_image if self.result_image is not None else self.current_image
        if source is None:
            QMessageBox.warning(self, "警告", "请先打开图片！")
            return

        settings = self.right_panel.get_upscale_settings()
        model_name = settings['upscale_model']
        device = settings['device']
        h, w = source.shape[:2]
        print(f"[超分] 模型={model_name}, 设备={device}, 原图尺寸={w}x{h}")

        upscaler = Upscaler(model_name=model_name, device=device)

        # 禁用按钮，显示进度
        self.right_panel.upscale_btn.setEnabled(False)
        self.right_panel.upscale_btn.setText("超分处理中...")
        self.progress_bar.setVisible(True)
        self.statusBar().showMessage("正在增加分辨率，请稍候（首次运行需下载模型）...")

        self.upscale_worker = UpscaleWorkerThread(upscaler, source)
        self.upscale_worker.finished.connect(self.on_upscale_finished)
        self.upscale_worker.error.connect(self.on_upscale_error)
        self.upscale_worker.start()

    def on_upscale_finished(self, result: np.ndarray):
        """超分完成回调"""
        self.result_image = result
        h, w = result.shape[:2]

        self.right_panel.upscale_btn.setEnabled(True)
        self.right_panel.upscale_btn.setText("✨ 增加分辨率")
        self.progress_bar.setVisible(False)

        self.right_panel.update_preview(self.result_image)
        self.right_panel.set_upscale_source_hint(has_result=True)
        self.statusBar().showMessage(f"超分完成！输出尺寸: {w}x{h}")

    def on_upscale_error(self, error_msg: str):
        """超分失败回调"""
        self.right_panel.upscale_btn.setEnabled(True)
        self.right_panel.upscale_btn.setText("✨ 增加分辨率")
        self.progress_bar.setVisible(False)

        QMessageBox.critical(self, "超分失败", f"增加分辨率失败:\n{error_msg}")
        self.statusBar().showMessage("超分失败")
