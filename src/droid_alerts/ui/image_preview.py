from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from ..qt_images import bgr_to_qimage


class ImagePreviewDialog(QDialog):
    def __init__(self, title: str, caption: str, frame: np.ndarray) -> None:
        super().__init__(None)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(
            """
            QDialog {
                background: #0e151d;
                color: #e9f1f7;
            }
            QLabel {
                color: #e9f1f7;
                font-size: 12px;
            }
            QLabel#previewImage {
                background: #080d13;
                border: 1px solid #243140;
                border-radius: 8px;
                padding: 4px;
            }
            QPushButton {
                min-width: 86px;
                min-height: 32px;
                background: #182330;
                color: #e9f1f7;
                border: 1px solid #243140;
                border-radius: 7px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: #1d2a38;
                border-color: #39c6d8;
            }
            QPushButton:pressed {
                background: #121b25;
            }
            """
        )
        pixmap = QPixmap.fromImage(bgr_to_qimage(frame))
        if pixmap.width() > 1100 or pixmap.height() > 650:
            pixmap = pixmap.scaled(
                1100,
                650,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        caption_label = QLabel(caption)
        caption_label.setWordWrap(True)
        caption_label.setStyleSheet(
            "font-weight: 600; color: #8ba0ae;"
        )
        layout.addWidget(caption_label)
        image_label = QLabel()
        image_label.setObjectName("previewImage")
        image_label.setPixmap(pixmap)
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(image_label)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
