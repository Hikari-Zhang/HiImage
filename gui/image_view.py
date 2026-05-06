"""
图像显示组件 - 支持缩放、平移、ROI选择、拖拽打开
"""
import os
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsTextItem
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QMouseEvent, QWheelEvent, QPainter, QPen, QColor, QBrush, QDragEnterEvent, QDropEvent


SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp'}


class ImageView(QGraphicsView):
    """自定义图像显示组件，支持缩放、平移、ROI选择、拖拽打开"""

    # 信号定义
    roi_selected = Signal(QRectF)   # ROI选择完成信号
    roi_changed = Signal()          # ROI变化信号
    file_dropped = Signal(str)      # 拖拽文件路径信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))

        # 设置渲染属性
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)

        # 设置拖拽模式
        self.setDragMode(QGraphicsView.RubberBandDrag)

        # 设置缩放锚点
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

        # 缩放因子
        self.zoom_factor = 1.15
        self.min_zoom = 0.1
        self.max_zoom = 10.0

        # ROI选择相关
        self.drawing_roi = False
        self.start_point = QPointF()
        self.current_rect = None
        self.roi_items = []

        # 启用拖拽接收
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # 拖拽事件
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖入时：包含支持的图片文件则接受"""
        if event.mimeData().hasUrls():
            if any(
                os.path.splitext(u.toLocalFile())[1].lower() in SUPPORTED_EXTENSIONS
                for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        """拖动经过时持续接受（否则 dropEvent 不会触发）"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        """松手时取第一个有效图片文件，通过信号通知主窗口"""
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.splitext(file_path)[1].lower() in SUPPORTED_EXTENSIONS:
                self.file_dropped.emit(file_path)
                event.acceptProposedAction()
                return
        event.ignore()

    # ------------------------------------------------------------------
    # 缩放 / 平移
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent):
        """鼠标滚轮缩放"""
        angle = event.angleDelta().y()

        if angle > 0:
            factor = self.zoom_factor
        else:
            factor = 1.0 / self.zoom_factor

        current_zoom = self.transform().m11()

        if (factor > 1 and current_zoom < self.max_zoom) or \
           (factor < 1 and current_zoom > self.min_zoom):
            self.scale(factor, factor)

    def mousePressEvent(self, event: QMouseEvent):
        """鼠标按下事件 - 开始绘制ROI"""
        if event.button() == Qt.LeftButton:
            self.drawing_roi = True
            self.start_point = self.mapToScene(event.pos())
            event.accept()
            return
        elif event.button() == Qt.MiddleButton:
            self.fit_to_view()

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        """鼠标移动事件 - 更新ROI矩形"""
        if self.drawing_roi:
            current_point = self.mapToScene(event.pos())

            if self.current_rect is None:
                rect = QRectF(self.start_point, current_point)
                self.current_rect = QGraphicsRectItem(rect)
                pen = QPen(QColor(255, 0, 0, 200))
                pen.setWidth(2)
                self.current_rect.setPen(pen)
                brush = QBrush(QColor(255, 0, 0, 50))
                self.current_rect.setBrush(brush)
                self.scene().addItem(self.current_rect)
            else:
                rect = QRectF(self.start_point, current_point).normalized()
                self.current_rect.setRect(rect)

            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        """鼠标释放事件 - 完成ROI绘制"""
        if event.button() == Qt.LeftButton and self.drawing_roi:
            if self.current_rect is not None:
                rect = self.current_rect.rect()
                self.roi_items.append(self.current_rect)
                self.roi_selected.emit(rect)
                self.roi_changed.emit()

            self.drawing_roi = False
            self.current_rect = None
            event.accept()
            return

        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # ROI 管理
    # ------------------------------------------------------------------

    def fit_to_view(self):
        """自适应窗口大小"""
        if not self.scene() or len(self.scene().items()) == 0:
            return
        rect = self.scene().itemsBoundingRect()
        self.fitInView(rect, Qt.KeepAspectRatio)

    def get_all_rois(self):
        """获取所有ROI的矩形坐标（图像坐标）"""
        rois = []
        valid_items = []

        for item in self.roi_items:
            try:
                if item.scene() is not None:
                    rois.append(item.rect())
                    valid_items.append(item)
            except RuntimeError:
                continue

        self.roi_items = valid_items
        return rois

    def clear_scene(self):
        """安全清除场景及所有ROI引用"""
        self.roi_items.clear()
        self.scene().clear()
        self.current_rect = None
        self.drawing_roi = False

    def clear_all_rois(self):
        """清除所有ROI"""
        for item in self.roi_items:
            if item.scene() is not None:
                self.scene().removeItem(item)
        self.roi_items.clear()
        self.roi_changed.emit()

    def remove_roi_at_index(self, index: int):
        """删除指定索引的ROI"""
        if 0 <= index < len(self.roi_items):
            item = self.roi_items[index]
            if item.scene() is not None:
                self.scene().removeItem(item)
            self.roi_items.pop(index)
            self.roi_changed.emit()
            return True
        return False

    def add_roi(self, rect: QRectF):
        """程序化添加一个ROI区域"""
        roi_item = QGraphicsRectItem(rect)
        pen = QPen(QColor(255, 0, 0, 200))
        pen.setWidth(2)
        roi_item.setPen(pen)
        brush = QBrush(QColor(255, 0, 0, 50))
        roi_item.setBrush(brush)
        self.scene().addItem(roi_item)
        self.roi_items.append(roi_item)
        self.roi_changed.emit()
