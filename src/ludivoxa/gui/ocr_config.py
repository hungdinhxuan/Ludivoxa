"""OCR configuration dialog for tuning OCR settings per window."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..capture.convert import bgra_to_rgb_pil
from ..config import normalize_ocr_region, normalize_ocr_regions
from .i18n import tr
from .theme import ACCENT


class ExclusionZoneItem(QGraphicsRectItem):
    """Interactive rectangle item for exclusion zones."""

    HANDLE_SIZE = 8
    MIN_SIZE = 20

    def __init__(self, rect: QRectF, color=None):
        super().__init__(rect)
        self._color = QColor(color or "red")
        self._resize_handle = None
        self._drag_start = None

        # Semi-transparent red fill
        fill, border = QColor(self._color), QColor(self._color)
        fill.setAlpha(60)
        self.setBrush(QBrush(fill))
        self.setPen(QPen(border, 2))

        # Ensure zones appear above the background pixmap
        self.setZValue(2)

        # Make selectable and movable
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)

    def paint(self, painter, option, widget=None):
        """Paint the rectangle with resize handles when selected."""
        super().paint(painter, option, widget)

        if self.isSelected():
            # Draw resize handles at corners
            handle_brush = QBrush(QColor(255, 255, 255))
            handle_pen = QPen(QColor(0, 0, 0), 1)
            painter.setBrush(handle_brush)
            painter.setPen(handle_pen)

            rect = self.rect()
            handles = self._get_handle_rects(rect)
            for handle_rect in handles.values():
                painter.drawRect(handle_rect)

    def _get_handle_rects(self, rect: QRectF) -> dict:
        """Get rectangles for resize handles at each corner."""
        hs = self.HANDLE_SIZE
        return {
            "top_left": QRectF(rect.left() - hs / 2, rect.top() - hs / 2, hs, hs),
            "top_right": QRectF(rect.right() - hs / 2, rect.top() - hs / 2, hs, hs),
            "bottom_left": QRectF(rect.left() - hs / 2, rect.bottom() - hs / 2, hs, hs),
            "bottom_right": QRectF(rect.right() - hs / 2, rect.bottom() - hs / 2, hs, hs),
        }

    def hoverMoveEvent(self, event):
        """Change cursor when hovering over resize handles."""
        if self.isSelected():
            handles = self._get_handle_rects(self.rect())
            pos = event.pos()
            for name, handle_rect in handles.items():
                if handle_rect.contains(pos):
                    if name in ("top_left", "bottom_right"):
                        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                    else:
                        self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                    return
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        """Start resize if clicking on a handle."""
        if self.isSelected() and event.button() == Qt.MouseButton.LeftButton:
            handles = self._get_handle_rects(self.rect())
            pos = event.pos()
            for name, handle_rect in handles.items():
                if handle_rect.contains(pos):
                    self._resize_handle = name
                    self._drag_start = pos
                    event.accept()
                    return
        self._resize_handle = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle resize or move."""
        if self._resize_handle:
            rect = self.rect()
            pos = event.pos()

            if self._resize_handle == "top_left":
                new_rect = QRectF(pos.x(), pos.y(), rect.right() - pos.x(), rect.bottom() - pos.y())
            elif self._resize_handle == "top_right":
                new_rect = QRectF(rect.left(), pos.y(), pos.x() - rect.left(), rect.bottom() - pos.y())
            elif self._resize_handle == "bottom_left":
                new_rect = QRectF(pos.x(), rect.top(), rect.right() - pos.x(), pos.y() - rect.top())
            else:  # bottom_right
                new_rect = QRectF(rect.left(), rect.top(), pos.x() - rect.left(), pos.y() - rect.top())

            # Enforce minimum size
            if new_rect.width() >= self.MIN_SIZE and new_rect.height() >= self.MIN_SIZE:
                self.setRect(new_rect.normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """End resize."""
        if self._resize_handle:
            self._resize_handle = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        """Handle item changes like selection."""
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            # Update appearance
            if value:
                self.setPen(QPen(self._color.lighter(125), 3))
            else:
                self.setPen(QPen(self._color, 2))
        return super().itemChange(change, value)


class OCRConfigDialog(QDialog):
    """Dialog for configuring OCR settings per window.

    This dialog receives OCR results from the main worker (via update_ocr_results)
    rather than running its own OCR, ensuring consistency between what's shown
    in the dialog and what's used for translation.
    """

    MAX_PREVIEW_WIDTH = 800
    MAX_PREVIEW_HEIGHT = 600

    # Emitted when confidence slider changes (float: new threshold)
    confidence_changed = Signal(float)

    def __init__(
        self,
        parent=None,
        window_title: str = "",
        initial_confidence: float = 0.6,
        initial_zones: list | None = None,
        initial_region: dict | list[dict] | None = None,
        selection_only: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("Configure OCR: {0}", window_title) if window_title else tr("Configure OCR"))
        available = self.screen().availableGeometry().adjusted(12, 36, -12, -12)
        self.setMinimumSize(min(480, available.width()), min(320, available.height()))
        self.resize(min(1000, available.width()), min(700, available.height()))

        self._window_title = window_title
        self._selection_only = selection_only
        self._confidence = initial_confidence
        self._zones: list[ExclusionZoneItem] = []
        self._region_items = []
        self._pending_regions = normalize_ocr_regions(initial_region)
        if selection_only:
            self._pending_regions = self._pending_regions[:1]
        self._ocr_boxes: list[QGraphicsRectItem] = []  # OCR detection boxes (green)
        self._drawing = False
        self._draw_start = None
        self._current_draw_rect = None
        self._image_size = (0, 0)  # Original image size
        self._preview_size = (0, 0)  # Scaled preview size
        self._ocr_results: list = []  # Store latest OCR results

        self._setup_ui()
        self._scan_mode.setCurrentIndex(1 if self._pending_regions or selection_only else 0)
        self._scan_mode.setEnabled(not selection_only)
        self._on_scan_mode_changed()

        # Load initial zones
        if initial_zones:
            self._load_zones(initial_zones)

    def _setup_ui(self):
        """Set up the dialog UI."""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)
        # Keep actions outside the scrollable editor, even on a small RDP desktop.
        self._editor_scroll = QScrollArea()
        self._editor_scroll.setWidgetResizable(True)
        self._editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        self._editor_scroll.setWidget(editor)
        outer_layout.addWidget(self._editor_scroll, 1)

        # Instructions
        scan_row = QHBoxLayout()
        scan_label = QLabel(tr("Scan:"))
        self._scan_mode = QComboBox()
        self._scan_mode.addItem(tr("Full window (use exclusions)"))
        self._scan_mode.addItem(tr("Only selected box") if self._selection_only else tr("Only selected boxes"))
        self._scan_mode.setAccessibleName(tr("OCR scan area"))
        scan_label.setBuddy(self._scan_mode)
        scan_row.addWidget(scan_label)
        scan_row.addWidget(self._scan_mode)
        scan_row.addStretch()
        self._preset_btn = QPushButton(tr("Use bottom dialogue box"))
        self._preset_btn.clicked.connect(self._use_bottom_box)
        scan_row.addWidget(self._preset_btn)
        layout.addLayout(scan_row)
        self._instructions = QLabel()
        self._instructions.setWordWrap(True)
        layout.addWidget(self._instructions)

        # A keyboard/numeric alternative to dragging, in normalized coordinates.
        self._region_controls = QWidget()
        fields = QHBoxLayout(self._region_controls)
        fields.setContentsMargins(0, 0, 0, 0)
        self._region_fields = {}
        for key, label, value in (("x", "X", 0), ("y", "Y", 60), ("width", "Width", 100), ("height", "Height", 35)):
            spin = QDoubleSpinBox()
            spin.setRange(0 if key in ("x", "y") else 0.1, 100)
            spin.setDecimals(1)
            spin.setSuffix(" %")
            spin.setValue(value)
            spin.setAccessibleName(tr("Translation box {0}", tr(label)))
            spin.editingFinished.connect(self._region_from_fields)
            field_label = QLabel(tr(label) + ":")
            field_label.setBuddy(spin)
            fields.addWidget(field_label)
            fields.addWidget(spin)
            self._region_fields[key] = spin
        fields.addStretch()
        layout.addWidget(self._region_controls)

        # Main content: preview on left, detected text on right
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side: Graphics scene and view for preview
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._scene = QGraphicsScene()
        self._view = QGraphicsView(self._scene)
        self._view.setRenderHints(self._view.renderHints())
        self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._view.setMouseTracking(True)
        self._view.setMinimumSize(240, 120)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.viewport().installEventFilter(self)
        left_layout.addWidget(self._view)

        splitter.addWidget(left_widget)

        # Right side: Detected text panel
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        detected_label = QLabel(tr("Detected Text:"))
        detected_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(detected_label)

        self._detected_text = QTextEdit()
        self._detected_text.setReadOnly(True)
        self._detected_text.setFont(QFont("sans-serif", 14))
        self._detected_text.setStyleSheet("background-color: #2a2a2a; color: #fff; border: 1px solid #555;")
        self._detected_text.setMinimumWidth(180)
        right_layout.addWidget(self._detected_text)

        splitter.addWidget(right_widget)

        # Set initial splitter sizes (70% preview, 30% text)
        splitter.setSizes([700, 300])

        layout.addWidget(splitter, 1)

        confidence_layout = QHBoxLayout()

        # Confidence slider
        confidence_title = QLabel(tr("Confidence:"))
        confidence_layout.addWidget(confidence_title)
        self._confidence_slider = QSlider(Qt.Orientation.Horizontal)
        self._confidence_slider.setRange(0, 100)
        self._confidence_slider.setValue(int(self._confidence * 100))
        self._confidence_slider.valueChanged.connect(self._on_confidence_changed)
        self._confidence_slider.setMinimumWidth(100)
        confidence_layout.addWidget(self._confidence_slider, 1)
        self._confidence_label = QLabel(f"{self._confidence:.0%}")
        self._confidence_label.setMinimumWidth(40)
        confidence_layout.addWidget(self._confidence_label)
        if self._selection_only:
            for control in (confidence_title, self._confidence_slider, self._confidence_label):
                control.hide()

        self._action_bar = QWidget()
        actions = QHBoxLayout(self._action_bar)
        actions.setContentsMargins(0, 0, 0, 0)

        # Clear exclusions button
        self._clear_btn = QPushButton(tr("Clear Exclusions"))
        self._clear_btn.clicked.connect(self._clear_current)
        actions.addWidget(self._clear_btn)
        actions.addStretch()

        # OK/Cancel buttons
        self._ok_btn = QPushButton(tr("OK"))
        self._ok_btn.setDefault(True)
        self._ok_btn.clicked.connect(self.accept)
        actions.addWidget(self._ok_btn)

        self._cancel_btn = QPushButton(tr("Cancel"))
        self._cancel_btn.clicked.connect(self.reject)
        actions.addWidget(self._cancel_btn)
        for button in (self._clear_btn, self._ok_btn, self._cancel_btn):
            button.setMinimumHeight(44)

        outer_layout.insertWidget(0, self._action_bar)
        outer_layout.insertLayout(1, confidence_layout)
        self.setTabOrder(self._clear_btn, self._ok_btn)
        self.setTabOrder(self._ok_btn, self._cancel_btn)
        self.setTabOrder(self._cancel_btn, self._confidence_slider)
        self.setTabOrder(self._confidence_slider, self._scan_mode)
        self._scan_mode.currentIndexChanged.connect(self._on_scan_mode_changed)

    def _on_scan_mode_changed(self):
        selected = self._scan_mode.currentIndex() == 1
        self._region_controls.setVisible(selected)
        self._preset_btn.setVisible(selected and not self._selection_only)
        self._clear_btn.setText(
            tr(("Clear Box" if self._selection_only else "Remove Selected Box") if selected else "Clear Exclusions")
        )
        self._clear_btn.setEnabled(not selected or bool(self._region_item))
        self._instructions.setText(
            tr(
                (
                    "Draw one box around the speaker name or use the percentage fields."
                    if self._selection_only
                    else "Drag on the preview to add a box. Select a box to move, resize, or remove it. "
                    "The percentages apply to the selected box. Green boxes show OCR detections."
                )
                if selected
                else "Draw red exclusion zones to block unwanted detections. Green boxes show OCR detections. "
                "Choose Only selected boxes to scan just the dialogue areas."
            )
        )
        for zone in self._zones:
            zone.setVisible(not selected)
        for item in self._region_items:
            item.setVisible(selected)
        self._ok_btn.setEnabled(not selected or bool(self.get_scan_region()))

    @property
    def _region_item(self):
        return next((item for item in self._region_items if item.isSelected()), None)

    def _set_region(self, rect, *, append=False):
        rect = rect.intersected(self._scene.sceneRect())
        if rect.isEmpty():
            return
        item = None if append else self._region_item
        if self._selection_only and self._region_items:
            item = self._region_items[0]
        if item is None:
            item = ExclusionZoneItem(rect, ACCENT)
            self._scene.addItem(item)
            self._region_items.append(item)
        else:
            item.setPos(0, 0)
            item.setRect(rect)
        self._scene.clearSelection()
        item.setVisible(self._scan_mode.currentIndex() == 1)
        item.setSelected(True)
        self._sync_region_fields()
        self._on_scan_mode_changed()

    def _use_bottom_box(self):
        bounds = self._scene.sceneRect()
        self._set_region(QRectF(0, bounds.height() * 0.6, bounds.width(), bounds.height() * 0.35), append=True)

    def _region_from_fields(self):
        bounds = self._scene.sceneRect()
        values = {key: field.value() / 100 for key, field in self._region_fields.items()}
        self._set_region(
            QRectF(
                values["x"] * bounds.width(),
                values["y"] * bounds.height(),
                values["width"] * bounds.width(),
                values["height"] * bounds.height(),
            )
        )

    def _sync_region_fields(self):
        region = self._region_coordinates(self._region_item)
        if region:
            for key, value in region.items():
                self._region_fields[key].setValue(value * 100)
        selected = self._scan_mode.currentIndex() == 1
        self._clear_btn.setEnabled(not selected or bool(region))
        self._ok_btn.setEnabled(not selected or bool(self.get_scan_region()))

    def _region_coordinates(self, item):
        if item is None:
            return None
        bounds = self._scene.sceneRect()
        rect = item.mapRectToScene(item.rect()).intersected(bounds)
        if rect.isEmpty() or bounds.isEmpty():
            return None
        return normalize_ocr_region(
            {
                "x": rect.x() / bounds.width(),
                "y": rect.y() / bounds.height(),
                "width": rect.width() / bounds.width(),
                "height": rect.height() / bounds.height(),
            }
        )

    def get_scan_region(self):
        """Get selected rectangles, keeping the original format for one box."""
        if self._scan_mode.currentIndex() == 0:
            return None
        boxes = [region for item in self._region_items if (region := self._region_coordinates(item))]
        return boxes[0] if len(boxes) == 1 else boxes or {}

    def accept(self):
        if self._scan_mode.currentIndex() == 1 and not self.get_scan_region():
            return
        super().accept()

    def _clear_current(self):
        if self._scan_mode.currentIndex() == 0:
            self._clear_all()
        elif (item := self._region_item) is not None:
            self._region_items.remove(item)
            self._scene.removeItem(item)
            if self._region_items:
                self._region_items[-1].setSelected(True)
            self._sync_region_fields()
        self._on_scan_mode_changed()

    def eventFilter(self, obj, event):
        """Handle mouse events on the graphics view."""
        from PySide6.QtCore import QEvent

        if obj == self._view.viewport():
            if event.type() == QEvent.Type.Resize:
                self._fit_preview()
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    scene_pos = self._view.mapToScene(event.position().toPoint())
                    if not self._scene.sceneRect().contains(scene_pos):
                        return False
                    # Check if clicking on an existing zone
                    if any(isinstance(item, ExclusionZoneItem) for item in self._scene.items(scene_pos)):
                        # Let the item handle selection
                        return False
                    # Start drawing new zone
                    self._drawing = True
                    self._draw_start = scene_pos
                    self._current_draw_rect = QGraphicsRectItem()
                    color = QColor(ACCENT if self._scan_mode.currentIndex() == 1 else "red")
                    fill = QColor(color)
                    fill.setAlpha(40)
                    self._current_draw_rect.setBrush(QBrush(fill))
                    self._current_draw_rect.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
                    self._current_draw_rect.setZValue(3)  # Above zones during drawing
                    self._scene.addItem(self._current_draw_rect)
                    return True

            elif event.type() == QEvent.Type.MouseMove:
                if self._drawing and self._draw_start:
                    scene_pos = self._view.mapToScene(event.position().toPoint())
                    rect = QRectF(self._draw_start, scene_pos).normalized().intersected(self._scene.sceneRect())
                    self._current_draw_rect.setRect(rect)
                    return True

            elif event.type() == QEvent.Type.MouseButtonRelease:
                if self._drawing and event.button() == Qt.MouseButton.LeftButton:
                    self._drawing = False
                    if self._current_draw_rect:
                        rect = self._current_draw_rect.rect()
                        self._scene.removeItem(self._current_draw_rect)
                        self._current_draw_rect = None
                        # Only create zone if large enough
                        if rect.width() >= ExclusionZoneItem.MIN_SIZE and rect.height() >= ExclusionZoneItem.MIN_SIZE:
                            if self._scan_mode.currentIndex() == 1:
                                self._set_region(rect, append=not self._selection_only)
                            else:
                                self._add_zone(rect)
                    self._draw_start = None
                    return True
                self._sync_region_fields()

        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        """Handle key presses."""
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._region_item and self._region_item.isSelected() and self._scan_mode.currentIndex() == 1:
                self._clear_current()
            else:
                self._delete_selected()
        else:
            super().keyPressEvent(event)

    def update_frame(self, frame):
        """Update the preview with a new frame.

        Args:
            frame: BGRA numpy array (H, W, 4)
        """
        if frame is None:
            return

        # Store original image size
        h, w = frame.shape[:2]
        self._image_size = (w, h)

        # Convert to PIL and scale for preview
        pil_image = bgra_to_rgb_pil(frame)

        # Scale to fit preview area while maintaining aspect ratio
        scale_x = self.MAX_PREVIEW_WIDTH / w
        scale_y = self.MAX_PREVIEW_HEIGHT / h
        scale = min(scale_x, scale_y)

        new_w = int(w * scale)
        new_h = int(h * scale)

        pil_image = pil_image.resize((new_w, new_h))

        # Convert to QPixmap
        data = pil_image.tobytes("raw", "RGB")
        qimg = QImage(data, new_w, new_h, new_w * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        # Update scene background - remove old pixmap
        for item in list(self._scene.items()):
            if (
                not isinstance(item, ExclusionZoneItem)
                and item not in self._ocr_boxes
                and item != self._current_draw_rect
            ):
                self._scene.removeItem(item)

        pixmap_item = self._scene.addPixmap(pixmap)
        pixmap_item.setZValue(0)  # Keep pixmap behind everything
        self._scene.setSceneRect(0, 0, new_w, new_h)

        # Auto-size dialog to fit preview on first frame
        if self._preview_size != (new_w, new_h):
            old_w, old_h = self._preview_size
            if old_w and old_h:
                for item in self._zones + self._region_items:
                    rect = item.mapRectToScene(item.rect())
                    item.setPos(0, 0)
                    item.setRect(
                        QRectF(
                            rect.x() * new_w / old_w,
                            rect.y() * new_h / old_h,
                            rect.width() * new_w / old_w,
                            rect.height() * new_h / old_h,
                        )
                    )
            self._preview_size = (new_w, new_h)
        self.apply_pending_zones()
        self._sync_region_fields()
        self._fit_preview()

    def _fit_preview(self):
        if not self._scene.sceneRect().isEmpty():
            self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def update_ocr_results(self, results: list):
        """Update with OCR results from the worker.

        Args:
            results: List of OCRResult objects from the worker.
        """
        self._ocr_results = results
        self._update_ocr_boxes()
        self._update_detected_text()

    def _update_ocr_boxes(self):
        """Update the green OCR detection boxes on the preview."""
        # Remove old OCR boxes
        for box in self._ocr_boxes:
            self._scene.removeItem(box)
        self._ocr_boxes.clear()

        if not self._ocr_results:
            return

        # Get scale factor
        w, h = self._image_size
        if w == 0 or h == 0:
            return

        scene_rect = self._scene.sceneRect()
        scale_x = scene_rect.width() / w
        scale_y = scene_rect.height() / h

        # Draw boxes for each detection
        for result in self._ocr_results:
            if result.bbox:
                x = result.bbox["x"] * scale_x
                y = result.bbox["y"] * scale_y
                box_w = result.bbox["width"] * scale_x
                box_h = result.bbox["height"] * scale_y

                rect_item = QGraphicsRectItem(QRectF(x, y, box_w, box_h))
                rect_item.setPen(QPen(QColor(0, 200, 0, 200), 2))
                rect_item.setBrush(QBrush(QColor(0, 200, 0, 30)))
                rect_item.setZValue(1)  # Above pixmap, below exclusion zones
                self._scene.addItem(rect_item)
                self._ocr_boxes.append(rect_item)

    def _update_detected_text(self):
        """Update the detected text panel."""
        if not self._ocr_results:
            self._detected_text.setPlainText("")
            return

        # Combine all detected text
        texts = [result.text for result in self._ocr_results if result.text]
        self._detected_text.setPlainText("\n".join(texts))

    def _on_confidence_changed(self, value: int):
        """Handle confidence slider change."""
        self._confidence = value / 100.0
        self._confidence_label.setText(f"{self._confidence:.0%}")
        # Emit signal so main window can update the worker
        self.confidence_changed.emit(self._confidence)

    def _add_zone(self, rect: QRectF):
        """Add a new exclusion zone."""
        zone_item = ExclusionZoneItem(rect)
        self._scene.addItem(zone_item)
        self._zones.append(zone_item)
        zone_item.setVisible(self._scan_mode.currentIndex() == 0)

    def _load_zones(self, zones: list):
        """Load zones from normalized coordinates."""
        self._pending_zones = zones

    def apply_pending_zones(self):
        """Apply pending zones after image is loaded."""
        if self._pending_regions and not self._scene.sceneRect().isEmpty():
            bounds = self._scene.sceneRect()
            for region in self._pending_regions:
                self._set_region(
                    QRectF(
                        region["x"] * bounds.width(),
                        region["y"] * bounds.height(),
                        region["width"] * bounds.width(),
                        region["height"] * bounds.height(),
                    ),
                    append=True,
                )
            self._pending_regions = []
        if not hasattr(self, "_pending_zones") or not self._pending_zones:
            return

        w, h = self._image_size
        if w == 0 or h == 0:
            return

        # Get current scene size (scaled preview)
        scene_rect = self._scene.sceneRect()
        scale_x = scene_rect.width() / w if w > 0 else 1
        scale_y = scene_rect.height() / h if h > 0 else 1

        for zone_data in self._pending_zones:
            x = zone_data.get("x", 0) * w * scale_x
            y = zone_data.get("y", 0) * h * scale_y
            width = zone_data.get("width", 0.1) * w * scale_x
            height = zone_data.get("height", 0.1) * h * scale_y
            rect = QRectF(x, y, width, height)
            self._add_zone(rect)

        self._pending_zones = []

    def _delete_selected(self):
        """Delete selected zones."""
        selected_items = [z for z in self._zones if z.isSelected()]
        for zone_item in selected_items:
            self._scene.removeItem(zone_item)
            self._zones.remove(zone_item)

    def _clear_all(self):
        """Remove all exclusion zones."""
        for zone_item in self._zones:
            self._scene.removeItem(zone_item)
        self._zones.clear()

    def get_zones(self) -> list[dict]:
        """Get zones as normalized coordinates.

        Returns:
            List of zone dicts with x, y, width, height as floats (0.0-1.0).
        """
        w, h = self._image_size
        if w == 0 or h == 0:
            return []

        # Get current scene size to calculate scale
        scene_rect = self._scene.sceneRect()
        scale_x = w / scene_rect.width() if scene_rect.width() > 0 else 1
        scale_y = h / scene_rect.height() if scene_rect.height() > 0 else 1

        zones = []
        for zone_item in self._zones:
            rect = zone_item.rect()
            # Convert scene coordinates back to normalized
            pos = zone_item.pos()  # Position offset from item transformations
            zones.append(
                {
                    "x": (rect.x() + pos.x()) * scale_x / w,
                    "y": (rect.y() + pos.y()) * scale_y / h,
                    "width": rect.width() * scale_x / w,
                    "height": rect.height() * scale_y / h,
                }
            )
        return zones

    def get_confidence(self) -> float:
        """Get the current confidence threshold.

        Returns:
            Confidence threshold (0.0-1.0).
        """
        return self._confidence
