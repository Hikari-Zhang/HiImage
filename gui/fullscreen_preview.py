"""
全屏预览窗口 - 用于查看大图
"""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QPushButton, QLabel,
                               QGraphicsView, QGraphicsScene, QGraphicsPixmapItem)
from PySide6.QtGui import QPixmap, QImage, QWheelEvent, QKeyEvent, QShowEvent
from PySide6.QtCore import Qt, QRectF


class FullScreenPreview(QDialog):
    """全屏预览对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_data = None
        self._first_show = True
        self.init_ui()
        
    def init_ui(self):
        """初始化UI"""
        # 设置全屏
        self.setWindowState(Qt.WindowFullScreen)
        self.setStyleSheet("background-color: black;")
        
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # 使用 QGraphicsView 替代 QLabel（支持锚点缩放）
        self.graphics_view = QGraphicsView()
        self.graphics_view.setStyleSheet("background-color: black; border: none;")
        self.graphics_view.setRenderHint(QGraphicsView.RenderHint.Antialiasing)
        self.graphics_view.setRenderHint(QGraphicsView.RenderHint.SmoothPixmapTransform)
        
        # 关键修复：设置缩放锚点为鼠标位置（解决缩放时乱飘的问题）
        self.graphics_view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.graphics_view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        
        # 启用鼠标拖拽平移
        self.graphics_view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        
        # 隐藏滚动条
        self.graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # 创建场景
        self.scene = QGraphicsScene()
        self.graphics_view.setScene(self.scene)
        
        # 图片项
        self.pixmap_item = None
        
        layout.addWidget(self.graphics_view)
        
        # 提示标签
        self.hint_label = QLabel("按 ESC 或点击关闭按钮退出全屏 | 鼠标滚轮缩放 | 拖拽平移")
        self.hint_label.setStyleSheet("color: white; padding: 10px; font-size: 14px;")
        self.hint_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint_label)
        
        # 关闭按钮
        self.close_btn = QPushButton("关闭全屏 (ESC)")
        self.close_btn.setStyleSheet(
            "background-color: #f44336; color: white; padding: 10px; "
            "font-size: 16px; font-weight: bold;"
        )
        self.close_btn.clicked.connect(self.close)
        layout.addWidget(self.close_btn)
        
    def set_image(self, image):
        """
        设置要显示的图像（延迟到首次显示时再渲染）
        
        :param image: numpy array (RGB格式)
        """
        self._image_data = image
        self._first_show = True
        
    def showEvent(self, event: QShowEvent):
        """窗口显示事件 - 首次显示时渲染图像"""
        super().showEvent(event)
        
        # 首次显示时，渲染图像并适应屏幕
        if self._first_show and self._image_data is not None:
            self._first_show = False
            self._render_image()
            self.fit_to_view()
            
    def _render_image(self):
        """渲染图像数据到pixmap"""
        if self._image_data is None:
            return
            
        try:
            # 转换numpy array为QImage
            height, width, channel = self._image_data.shape
            bytes_per_line = 3 * width
            qimage = QImage(self._image_data.data, width, height, bytes_per_line, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qimage)
            
            # 清除旧图片
            if self.pixmap_item:
                self.scene.removeItem(self.pixmap_item)
            
            # 添加新图片到场景
            self.pixmap_item = QGraphicsPixmapItem(pixmap)
            self.scene.addItem(self.pixmap_item)
            
            # 设置场景范围
            self.scene.setSceneRect(QRectF(pixmap.rect()))
            
        except Exception as e:
            print(f"预览错误: {str(e)}")
        
    def fit_to_view(self):
        """适应屏幕（计算并应用最佳缩放比例）"""
        if self.pixmap_item is None:
            return
            
        # 适应 graphics_view 的大小
        self.graphics_view.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        
    def keyPressEvent(self, event: QKeyEvent):
        """键盘事件"""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        elif event.key() == Qt.Key.Key_Plus or event.key() == Qt.Key.Key_Equal:
            # 放大
            self.zoom_in()
        elif event.key() == Qt.Key.Key_Minus:
            # 缩小
            self.zoom_out()
        elif event.key() == Qt.Key.Key_0 or event.key() == Qt.Key.Key_1:
            # 重置缩放
            self.fit_to_view()
        else:
            super().keyPressEvent(event)
            
    def wheelEvent(self, event: QWheelEvent):
        """鼠标滚轮缩放"""
        if self.pixmap_item is None:
            return
            
        angle = event.angleDelta().y()
        factor = 1.2 if angle > 0 else 0.8
        
        # 应用缩放（锚点已设置为鼠标位置，不会乱飘）
        self.graphics_view.scale(factor, factor)
            
    def zoom_in(self):
        """放大"""
        if self.pixmap_item is None:
            return
        self.graphics_view.scale(1.2, 1.2)
            
    def zoom_out(self):
        """缩小"""
        if self.pixmap_item is None:
            return
        self.graphics_view.scale(0.8, 0.8)
        
    def resizeEvent(self, event):
        """窗口大小变化事件"""
        super().resizeEvent(event)
        # 窗口大小变化时，重新适应
        if self.pixmap_item is not None:
            self.fit_to_view()
