from __future__ import annotations


DARK_STYLESHEET = """
QMainWindow,
QWidget {
    background: #0b1020;
    color: #e5e7eb;
    font-family: "Segoe UI", "Microsoft YaHei UI", "Arial";
    font-size: 13px;
}

QToolBar {
    background: #111827;
    border-bottom: 1px solid #263244;
    spacing: 8px;
    padding: 6px;
}

QToolButton,
QPushButton {
    background: #1f2937;
    color: #f8fafc;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 7px 11px;
}

QToolButton:hover,
QPushButton:hover {
    background: #263244;
    border-color: #60a5fa;
}

QToolButton:pressed,
QPushButton:pressed {
    background: #172033;
}

QToolButton:disabled,
QPushButton:disabled {
    background: #111827;
    color: #64748b;
    border-color: #263244;
}

QLabel {
    background: transparent;
    color: #e5e7eb;
}

QLabel#Title {
    font-size: 18px;
    font-weight: 600;
    color: #f8fafc;
}

QLabel#PanelTitle {
    font-size: 16px;
    font-weight: 600;
    color: #f8fafc;
    margin-bottom: 8px;
}

QListWidget {
    background: #111827;
    color: #e5e7eb;
    border: 1px solid #263244;
    border-radius: 6px;
    padding: 4px;
    outline: 0;
}

QListWidget::item {
    border-radius: 4px;
    padding: 8px;
}

QListWidget::item:selected {
    background: #1d4ed8;
    color: #ffffff;
}

QListWidget::item:hover {
    background: #1f2937;
}

QComboBox,
QDoubleSpinBox {
    background: #111827;
    color: #f8fafc;
    border: 1px solid #334155;
    border-radius: 5px;
    min-height: 28px;
    padding: 3px 8px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

QComboBox:hover,
QDoubleSpinBox:hover {
    border-color: #60a5fa;
}

QComboBox:disabled,
QDoubleSpinBox:disabled {
    background: #0f172a;
    color: #64748b;
    border-color: #263244;
}

QComboBox QAbstractItemView {
    background: #111827;
    color: #e5e7eb;
    border: 1px solid #334155;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

QCheckBox {
    color: #e5e7eb;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid #475569;
    background: #111827;
}

QCheckBox::indicator:checked {
    background: #38bdf8;
    border-color: #38bdf8;
}

QFormLayout,
QScrollArea {
    background: transparent;
    border: 0;
}

QScrollArea > QWidget > QWidget {
    background: #0b1020;
}

QSplitter::handle {
    background: #263244;
}

QStatusBar {
    background: #111827;
    color: #cbd5e1;
    border-top: 1px solid #263244;
}

QMessageBox {
    background: #111827;
    color: #e5e7eb;
}
"""
