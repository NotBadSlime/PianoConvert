"""Small reusable widgets for the main window."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui import icons
from app.ui.theme import LIGHT, RADIUS


def repolish(widget: QWidget) -> None:
    """Re-apply the style sheet after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget: QWidget, name: str, value) -> None:
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    repolish(widget)
    for child in widget.findChildren(QWidget):
        repolish(child)


def tab_focus(*widgets: QWidget) -> None:
    """Keyboard-only focus: clicking does not leave a focus ring behind, Tab still reaches the control."""
    for widget in widgets:
        widget.setFocusPolicy(Qt.FocusPolicy.TabFocus)


def chip(text: str, tone: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("chip")
    label.setProperty("tone", tone)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setFixedHeight(20)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return label


def paint_soft_shadow(painter: QPainter, rect: QRectF, radius: float, color: str, alpha: float,
                      blur: int = 12, offset_y: float = 3.0) -> None:
    """A cheap soft shadow: stacked translucent rounded rectangles that fade out over ``blur`` px."""
    if alpha <= 0 or blur <= 0:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    base = QColor(color)
    step_alpha = alpha / blur
    for i in range(blur, 0, -1):
        c = QColor(base)
        c.setAlphaF(min(1.0, step_alpha * 1.0))
        painter.setBrush(c)
        grown = rect.adjusted(-i * 0.75, -i * 0.5 + offset_y, i * 0.75, i * 0.75 + offset_y)
        painter.drawRoundedRect(grown, radius + i * 0.6, radius + i * 0.6)
    painter.restore()


class ShadowRoot(QWidget):
    """The window background; paints soft shadows under the registered cards."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("root")
        self._targets: list[QWidget] = []
        self._tokens = LIGHT

    def add_shadow(self, widget: QWidget) -> None:
        self._targets.append(widget)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._tokens["bg"]))
        for widget in self._targets:
            if not widget.isVisible():
                continue
            top_left = widget.mapTo(self, QPoint(0, 0))
            rect = QRectF(QRect(top_left, widget.size()))
            paint_soft_shadow(painter, rect.adjusted(1, 1, -1, -1), RADIUS["xl"], self._tokens["shadow"],
                              self._tokens["shadow_alpha"], blur=14, offset_y=4)
        painter.end()


class ElidedLabel(QLabel):
    """Single-line label that elides with "…" when it is narrower than its text."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        self._refresh()

    def full_text(self) -> str:
        return self._full

    def text(self) -> str:  # noqa: D401 - Python callers get the full, un-elided text
        return self._full

    def _refresh(self) -> None:
        if not self.isVisible() or self.width() <= 0:
            super().setText(self._full)
            self.setToolTip("")
            return
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        super().setText(elided)
        self.setToolTip(self._full if elided != self._full else "")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._refresh()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(16, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4, super().sizeHint().height())


class Segmented(QFrame):
    """A pill-shaped group of radio buttons rendered as toggle segments."""

    def __init__(self, buttons: list[QRadioButton], parent: QWidget | None = None, *,
                 compact: bool = False, tabs: bool = False):
        super().__init__(parent)
        self.setObjectName("segmented")
        self.setProperty("compact", compact)
        self.setProperty("tabs", tabs)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.group = QButtonGroup(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        for button in buttons:
            button.setObjectName("segment")
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self.group.addButton(button)
            layout.addWidget(button)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()


class DropZone(QFrame):
    """Clickable frame; the main window drives its drag-highlight state."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setProperty("state", "empty")
        self.setProperty("drag", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("选择文件")
        self._enabled = True

    def set_interactive(self, enabled: bool) -> None:
        self._enabled = enabled
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        set_prop(self, "disabled", not enabled)

    def set_dragging(self, on: bool) -> None:
        set_prop(self, "drag", bool(on))

    def set_state(self, state: str) -> None:
        set_prop(self, "state", state)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._enabled and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._enabled and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class SmoothProgressBar(QProgressBar):
    """Rounded progress bar that glides to each new value and shimmers while work is running."""

    SCALE = 1000

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setRange(0, self.SCALE)
        self.setValue(0)
        self.setTextVisible(False)
        self.setFixedHeight(6)
        self._tokens = LIGHT
        self._shown = 0.0
        self._phase = 0.0
        self._running = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(420)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self._shimmer = QTimer(self)
        self._shimmer.setInterval(33)
        self._shimmer.timeout.connect(self._tick)

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    def ratio(self) -> float:
        return self.value() / self.SCALE

    def shown_ratio(self) -> float:
        return self._shown / self.SCALE

    def set_ratio(self, ratio: float, animate: bool = True) -> None:
        target = int(round(max(0.0, min(1.0, float(ratio))) * self.SCALE))
        self.setValue(target)
        self._anim.stop()
        if not animate or target < self._shown:
            self._shown = float(target)
            self.update()
            return
        self._anim.setStartValue(float(self._shown))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def set_running(self, on: bool) -> None:
        self._running = bool(on)
        if self._running:
            self._shimmer.start()
        else:
            self._shimmer.stop()
        self.update()

    def _on_anim(self, value) -> None:
        self._shown = float(value)
        self.update()

    def _tick(self) -> None:
        if not self.isVisible():
            return
        self._phase = (self._phase + 0.018) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._tokens["track"]))
        painter.drawRoundedRect(rect, radius, radius)
        width = rect.width() * self._shown / self.SCALE
        if width > 0.5:
            chunk = QRectF(rect.left(), rect.top(), max(width, rect.height()), rect.height())
            gradient = QLinearGradient(chunk.topLeft(), chunk.topRight())
            gradient.setColorAt(0.0, QColor(self._tokens["primary"]))
            gradient.setColorAt(1.0, QColor(self._tokens["primary_hover"]))
            path = QPainterPath()
            path.addRoundedRect(chunk, radius, radius)
            painter.fillPath(path, gradient)
            if self._running:
                band = max(40.0, chunk.width() * 0.25)
                x = chunk.left() - band + (chunk.width() + 2 * band) * self._phase
                shine = QLinearGradient(x, 0, x + band, 0)
                light = QColor(255, 255, 255, 0)
                peak = QColor(255, 255, 255, 90)
                shine.setColorAt(0.0, light)
                shine.setColorAt(0.5, peak)
                shine.setColorAt(1.0, light)
                painter.save()
                painter.setClipPath(path)
                painter.fillRect(chunk, shine)
                painter.restore()
        painter.end()


class EmptyState(QWidget):
    """Centered icon + title + one line of help; ``text()`` / ``setText()`` use "title\\nhelp"."""

    def __init__(self, glyph_name: str, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.icon_label = icons.glyph_label(glyph_name, "emptyIcon", 56)
        self.title_label = QLabel("")
        self.title_label.setObjectName("emptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        self.body_label = QLabel("")
        self.body_label.setObjectName("emptyText")
        self.body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.action_button = QPushButton("")
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.action_button.hide()
        self._action = None
        self.action_button.clicked.connect(self._run_action)
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 24, 24, 24)
        column.setSpacing(8)
        column.addStretch(1)
        column.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(6)
        column.addWidget(self.title_label)
        column.addWidget(self.body_label)
        column.addSpacing(4)
        column.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(1)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        title, _sep, rest = (text or "").partition("\n")
        self.title_label.setText(title)
        self.body_label.setText(rest)
        self.body_label.setVisible(bool(rest))

    def text(self) -> str:
        body = self.body_label.text()
        return self.title_label.text() + (f"\n{body}" if body else "")

    def set_glyph(self, name: str, tone: str = "") -> None:
        icons.set_glyph(self.icon_label, name)
        set_prop(self.icon_label, "tone", tone)
        repolish(self.icon_label)

    def set_action(self, text: str | None, callback=None) -> None:
        self._action = callback
        self.action_button.setText(text or "")
        self.action_button.setVisible(bool(text) and callback is not None)

    def _run_action(self) -> None:
        if self._action is not None:
            self._action()


class Toast(QWidget):
    """Snackbar floating at the bottom centre of its parent; fades in, then out after a few seconds."""

    SHADOW = 12
    durations = {"info": 2600, "success": 2600, "error": 5200}

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        self._tokens = LIGHT
        self._tone = "info"
        self._action = None
        m = self.SHADOW
        row = QHBoxLayout(self)
        row.setContentsMargins(m + 14, m + 9, m + 8, m + 9)
        row.setSpacing(10)
        self.icon_label = icons.glyph_label("info", "toastIcon")
        self.icon_label.setFixedWidth(18)
        self.text_label = QLabel("")
        self.text_label.setObjectName("toastText")
        self.text_label.setWordWrap(True)
        self.text_label.setMaximumWidth(380)
        self.action_button = QPushButton("")
        self.action_button.setObjectName("toastAction")
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_button.clicked.connect(self._run_action)
        self.action_button.hide()
        self.close_button = QPushButton(icons.glyph("close"))
        self.close_button.setObjectName("toastClose")
        self.close_button.setProperty("glyph", icons.has_icon_font())
        self.close_button.setToolTip("关闭")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.dismiss)
        tab_focus(self.action_button, self.close_button)
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.text_label, 1)
        row.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.finished.connect(self._fade_done)
        self._fading_out = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        parent.installEventFilter(self)
        self.hide()

    # -- public
    def show_message(self, text: str, tone: str = "info", action_text: str | None = None, action=None,
                     duration_ms: int | None = None) -> None:
        self._tone = tone if tone in self.durations else "info"
        self._action = action
        glyph = {"success": "completed", "error": "error_badge"}.get(self._tone, "info")
        icons.set_glyph(self.icon_label, glyph)
        set_prop(self.icon_label, "tone", self._tone)
        repolish(self.icon_label)
        self.text_label.setText(text)
        # a word-wrapped label asks for a narrow width; give it its natural width up to the cap
        self.text_label.ensurePolished()
        metrics = self.text_label.fontMetrics()
        natural = max((metrics.horizontalAdvance(line) for line in (text or " ").splitlines()), default=0) + 6
        self.text_label.setMinimumWidth(min(natural, self.text_label.maximumWidth()))
        self.action_button.setText(action_text or "")
        self.action_button.setVisible(bool(action_text) and action is not None)
        self._fading_out = False
        was_hidden = self.isHidden()
        self._fade.stop()
        self.adjustSize()
        self._reposition()
        self.raise_()
        if was_hidden:
            self._effect.setOpacity(0.0)
        self.show()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        if duration_ms is None:
            duration_ms = self.durations[self._tone] + (1600 if self.action_button.isVisibleTo(self) else 0)
        self._timer.start(max(800, int(duration_ms)))

    def text(self) -> str:
        return self.text_label.text()

    def tone(self) -> str:
        return self._tone

    def dismiss(self) -> None:
        self._timer.stop()
        if self.isHidden():
            return
        self._fading_out = True
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def set_tokens(self, tokens: dict) -> None:
        self._tokens = tokens
        self.update()

    # -- internals
    def _fade_done(self) -> None:
        if self._fading_out:
            self._fading_out = False
            self.hide()
            self._effect.setOpacity(1.0)

    def _run_action(self) -> None:
        action, self._action = self._action, None
        self.dismiss()
        if action is not None:
            action()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        hint = self.sizeHint()
        width = min(hint.width(), max(200, parent.width() - 32))
        height = self.heightForWidth(width) if self.hasHeightForWidth() else hint.height()
        height = max(height, hint.height())
        x = (parent.width() - width) // 2
        y = parent.height() - height - 10
        self.setGeometry(x, y, width, height)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and not self.isHidden():
            self._reposition()
        return super().eventFilter(watched, event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self.isHidden() and not self._fading_out:
            self._timer.start(1500)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismiss()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.SHADOW
        body = QRectF(self.rect()).adjusted(m, m, -m, -m)
        paint_soft_shadow(painter, body, 12, self._tokens["shadow"], min(0.5, self._tokens["shadow_alpha"] * 3),
                          blur=m, offset_y=3)
        painter.setPen(QPen(QColor(self._tokens["toast_border"]), 1))
        painter.setBrush(QColor(self._tokens["toast"]))
        painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        painter.end()
