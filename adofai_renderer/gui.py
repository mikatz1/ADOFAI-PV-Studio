"""Desktop GUI entry point: launches WelcomeCard preloader, then transitions cleanly into MainWindow."""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .preloader import PreloadResult
from .ui.main_window import MainWindow
from .ui.theme import build_studio_qss, get_asset_path
from .ui.welcome_card import WelcomeCard


def _set_windows_app_id() -> None:
    """Bind an explicit Windows AppUserModelID so the taskbar displays our custom symbol icon."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AdofaiPvRenderer.Studio.1")
    except Exception:
        pass


def launch_gui(initial_level: Optional[str] = None) -> int:
    _set_windows_app_id()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ADOFAI PV Renderer")
    app.setStyle("Fusion")
    app.setStyleSheet(build_studio_qss())

    icon_path = get_asset_path("app_icon.ico")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    holder = {"main": None}
    welcome = WelcomeCard()
    if os.path.isfile(icon_path):
        welcome.setWindowIcon(QIcon(icon_path))

    def on_ready(result: PreloadResult) -> None:
        win = MainWindow(result)
        if os.path.isfile(icon_path):
            win.setWindowIcon(QIcon(icon_path))
        holder["main"] = win
        win.show()
        QTimer.singleShot(4000, lambda: win._check_updates(manual=False))
        if initial_level and os.path.exists(initial_level):
            win._import_any_path(initial_level)

    welcome.ready.connect(on_ready)
    welcome.show()
    return app.exec()
