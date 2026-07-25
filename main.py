from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from firepanel import __version__
from firepanel.ui import MainWindow


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("FirePanel Commissioning")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("FirePanel")
    icon_path = Path(__file__).resolve().parent / "firepanel" / "assets" / "firepanel.ico"
    application.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
