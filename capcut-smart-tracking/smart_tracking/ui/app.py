"""Application entry point."""

import argparse
import sys

from PySide6.QtWidgets import QApplication

from .icons import icon
from .main_window import MainWindow
from .theme import QSS


def main(argv=None):
    parser = argparse.ArgumentParser(prog="smart_tracking", description="CapCut Smart Tracking")
    parser.add_argument("--drafts", help="папка черновиков CapCut (com.lveditor.draft)")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    app = QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("CapCut Smart Tracking")
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    app.setWindowIcon(icon("target", "#2fd3c4", 64))
    w = MainWindow(args.drafts)
    w.show()
    return app.exec()
