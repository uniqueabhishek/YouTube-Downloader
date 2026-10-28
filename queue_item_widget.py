"""Custom queue item widget with clickable remove button."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget  # type: ignore


class QueueItemWidget(QWidget):
    """Custom widget for queue list items with clickable remove button."""

    # Signal emitted when remove button is clicked
    remove_clicked = pyqtSignal(int)  # Emits the row index

    def __init__(self, row: int, text: str, parent=None):
        """Initialize queue item widget.

        Args:
            row: Row index in the queue
            text: Display text for the item
            parent: Parent widget
        """
        super().__init__(parent)
        self.row = row

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(5)

        # Remove button (×)
        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedSize(16, 16)
        self.remove_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.setStyleSheet("""
            QPushButton {
                color: white;
                background-color: #CC0000;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 14px;
                padding: 0px;
                margin: 0px;
                width: 16px;
                height: 16px;
                min-width: 16px;
                max-width: 16px;
                min-height: 16px;
                max-height: 16px;
                padding-bottom: 3px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #FF0000;
            }
        """)
        self.remove_btn.clicked.connect(
            lambda: self.remove_clicked.emit(self.row))
        layout.addWidget(self.remove_btn, 0, Qt.AlignVCenter)

        # Text label
        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setStyleSheet(
            "color: black; background-color: transparent;")
        layout.addWidget(self.text_label, 1)  # Stretch factor 1

        self.setLayout(layout)

    def update_text(self, text: str):
        """Update the display text.

        Args:
            text: New text to display
        """
        self.text_label.setText(text)
