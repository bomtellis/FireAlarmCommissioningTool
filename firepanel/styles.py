APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #f4f7fb;
    color: #172033;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QMenuBar {
    background: #183153;
    color: white;
}
QMenuBar::item:selected { background: #284d78; }
QTabWidget#ribbon::pane {
    border: 0;
    border-bottom: 1px solid #cbd5e1;
    background: white;
}
QTabWidget#ribbon QTabBar::tab {
    background: #e9eff7;
    border: 0;
    padding: 7px 18px;
    color: #334155;
}
QTabWidget#ribbon QTabBar::tab:selected {
    background: white;
    color: #0f4c81;
    border-top: 3px solid #0d6efd;
}
QFrame#ribbonGroup {
    background: white;
    border-right: 1px solid #e2e8f0;
}
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 5px 9px;
}
QToolButton:hover { background: #e7f1ff; border-color: #b6d4fe; }
QToolButton:pressed { background: #cfe2ff; }
QListWidget#navigation {
    background: #14283f;
    color: #dbeafe;
    border: 0;
    padding: 8px;
    font-size: 10.5pt;
}
QListWidget#navigation::item {
    padding: 11px 10px;
    border-radius: 5px;
    margin: 2px 0;
}
QListWidget#navigation::item:selected {
    background: #0d6efd;
    color: white;
}
QListWidget#navigation::item:hover { background: #244b70; }
QFrame#card {
    background: white;
    border: 1px solid #dbe3ec;
    border-radius: 9px;
}
QLabel#cardValue {
    color: #0f4c81;
    font-size: 22pt;
    font-weight: 600;
}
QLabel#pageTitle {
    font-size: 19pt;
    font-weight: 600;
    color: #172033;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
    background: white;
    border: 1px solid #b8c4d1;
    border-radius: 5px;
    padding: 6px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {
    border: 2px solid #0d6efd;
}
QPushButton {
    background: #0d6efd;
    color: white;
    border: 0;
    border-radius: 5px;
    padding: 7px 13px;
    font-weight: 600;
}
QPushButton:hover { background: #0b5ed7; }
QPushButton[secondary="true"] {
    background: white;
    color: #334155;
    border: 1px solid #b8c4d1;
}
QHeaderView::section {
    background: #e9eff7;
    color: #26364a;
    border: 0;
    border-right: 1px solid #d2dbe6;
    border-bottom: 1px solid #c1ccd8;
    padding: 7px;
    font-weight: 600;
}
QTableWidget, QTableView {
    background: white;
    alternate-background-color: #f7f9fc;
    border: 1px solid #d4dde7;
    gridline-color: #e5eaf0;
    selection-background-color: #cfe2ff;
    selection-color: #172033;
}
QGraphicsView {
    background: #eef2f7;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
}
QStatusBar { background: #183153; color: white; }
"""
