"""Shared helpers for GUI tests."""

import time

from PyQt6.QtWidgets import QApplication


def _wait_recs(dlg, timeout_iters: int = 600, sleep_sec: float = 0.01):
    """RecommendDialog 의 백그라운드 추천 계산이 끝날 때까지 기다린다."""
    app = QApplication.instance()
    for _ in range(timeout_iters):
        if dlg._worker is None or not dlg._worker.isRunning():
            break
        app.processEvents()
        time.sleep(sleep_sec)
    else:
        raise AssertionError("RecommendDialog worker timeout")
    app.processEvents()
