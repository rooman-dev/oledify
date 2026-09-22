"""Before/after view with a draggable vertical split line."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

HANDLE_GRAB_PX = 12
DEFAULT_PLACEHOLDER = "Open or drop an image to start"


class CompareView(QWidget):
    """Shows the original left of the split line and the processed image right of it.

    When focus picking is enabled, a click on the processed side emits
    focus_clicked(x, y) with the position inside the image frame (0-1).
    """

    focus_clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None, placeholder: str = DEFAULT_PLACEHOLDER):
        super().__init__(parent)
        self._placeholder = placeholder
        self._original: QImage | None = None
        self._processed: QImage | None = None
        self._split = 0.5
        self._dragging = False
        self._focus_enabled = False
        self._cache: dict[str, tuple[QSize, QPixmap]] = {}
        self.setMouseTracking(True)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_original(self, image: QImage | None) -> None:
        self._original = image
        self._processed = None
        self._cache.clear()
        self.update()

    def set_processed(self, image: QImage | None) -> None:
        self._processed = image
        self._cache.pop("processed", None)
        self.update()

    def set_images(self, original: QImage, processed: QImage) -> None:
        """Replace both sides at once (they must share the same size)."""
        self._original = original
        self._processed = processed
        self._cache.clear()
        self.update()

    def set_placeholder(self, text: str) -> None:
        """Text shown while no image is set."""
        self._placeholder = text
        self.update()

    @property
    def split(self) -> float:
        """Split line position across the image, 0 (left edge) to 1 (right edge)."""
        return self._split

    @split.setter
    def split(self, value: float) -> None:
        self._split = min(1.0, max(0.0, float(value)))
        self.update()

    def set_focus_enabled(self, enabled: bool) -> None:
        self._focus_enabled = enabled
        if not enabled and not self._dragging:
            self.unsetCursor()

    def _image_rect(self) -> QRect:
        """Largest rect with the image's aspect ratio that fits the widget, centred."""
        if self._original is None:
            return QRect()
        size = self._original.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - size.width()) // 2
        y = (self.height() - size.height()) // 2
        return QRect(QPoint(x, y), size)

    def _scaled(self, key: str, image: QImage, size: QSize) -> QPixmap:
        """Pixmap scaled to size in device pixels, cached until the size or image changes."""
        dpr = self.devicePixelRatioF()
        device_size = QSize(round(size.width() * dpr), round(size.height() * dpr))
        cached = self._cache.get(key)
        if cached is None or cached[0] != device_size:
            scaled = image.scaled(
                device_size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            pixmap = QPixmap.fromImage(scaled)
            pixmap.setDevicePixelRatio(dpr)
            cached = (device_size, pixmap)
            self._cache[key] = cached
        return cached[1]

    def _split_x(self, rect: QRect) -> int:
        return rect.left() + round(rect.width() * self._split)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)

        if self._original is None:
            painter.setPen(QColor(140, 140, 140))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return

        rect = self._image_rect()
        split_x = self._split_x(rect)
        painter.drawPixmap(rect.topLeft(), self._scaled("original", self._original, rect.size()))

        if self._processed is not None:
            painter.save()
            painter.setClipRect(QRect(QPoint(split_x, rect.top()), rect.bottomRight()))
            painter.drawPixmap(rect.topLeft(), self._scaled("processed", self._processed, rect.size()))
            painter.restore()

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 2))
        painter.drawLine(split_x, rect.top(), split_x, rect.bottom())
        painter.setBrush(QColor(255, 255, 255, 220))
        painter.drawEllipse(QPoint(split_x, rect.center().y()), 7, 7)

        painter.setPen(QColor(255, 255, 255, 200))
        labels = rect.adjusted(8, 8, -8, -8)
        painter.drawText(labels, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "Original")
        painter.drawText(labels, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, "OLED")

    def _near_handle(self, pos: QPoint) -> bool:
        rect = self._image_rect()
        return rect.isValid() and abs(pos.x() - self._split_x(rect)) <= HANDLE_GRAB_PX

    def _move_split(self, x: int) -> None:
        rect = self._image_rect()
        if rect.width() > 0:
            self._split = min(1.0, max(0.0, (x - rect.left()) / rect.width()))
            self.update()

    def _on_focus_side(self, pos: QPoint) -> bool:
        """True if pos is a focus-picking click: processed side, inside the image, off the handle."""
        if not self._focus_enabled or self._processed is None or self._near_handle(pos):
            return False
        rect = self._image_rect()
        return rect.contains(pos) and pos.x() > self._split_x(rect)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._original is None:
            return
        pos = event.position().toPoint()
        if self._on_focus_side(pos):
            rect = self._image_rect()
            x = (pos.x() - rect.left()) / max(1, rect.width() - 1)
            y = (pos.y() - rect.top()) / max(1, rect.height() - 1)
            self.focus_clicked.emit(min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))
            return
        self._dragging = True
        self._move_split(pos.x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self._dragging:
            self._move_split(pos.x())
        elif self._near_handle(pos):
            self.setCursor(Qt.CursorShape.SplitHCursor)
        elif self._on_focus_side(pos):
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
