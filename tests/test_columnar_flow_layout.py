import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets
from PyQt6.QtCore import QRect, QSize, Qt

from appGUI.ColumnarFlowLayout import ColumnarFlowLayout


class Box(QtWidgets.QWidget):
    def __init__(self, width, height, min_width=None):
        super().__init__()
        self._hint = QSize(width, height)
        self.setMinimumWidth(min_width if min_width is not None else width)

    def sizeHint(self):
        return self._hint


class TestColumnarFlowLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def make_layout(self, boxes, margin=0, spacing=10):
        host = QtWidgets.QWidget()
        layout = ColumnarFlowLayout(parent=host, margin=margin, spacing=spacing)
        for box in boxes:
            layout.addWidget(box)
        self.addCleanup(host.deleteLater)
        return layout

    def test_items_start_inside_the_contents_margins(self):
        boxes = [Box(100, 50), Box(100, 50)]
        layout = self.make_layout(boxes, margin=7)
        layout.setGeometry(QRect(20, 30, 500, 200))

        self.assertEqual(boxes[0].geometry().topLeft().x(), 27)
        self.assertEqual(boxes[0].geometry().topLeft().y(), 37)
        self.assertEqual(boxes[1].geometry().x(), 27 + 100 + 10)

    def test_columns_do_not_grow_wider_than_the_widest_preferred_width(self):
        boxes = [Box(150, 50), Box(200, 50)]
        layout = self.make_layout(boxes)
        layout.setGeometry(QRect(0, 0, 2000, 200))

        self.assertEqual(boxes[0].geometry().width(), 200)
        self.assertEqual(boxes[1].geometry().width(), 200)
        self.assertEqual(boxes[1].geometry().x(), 210)

    def test_one_wide_item_does_not_reduce_the_column_count(self):
        boxes = [Box(200, 50, min_width=150) for _ in range(4)]
        boxes.append(Box(600, 50, min_width=150))
        layout = self.make_layout(boxes)
        layout.setGeometry(QRect(0, 0, 1000, 400))

        columns = {box.geometry().x() for box in boxes}
        self.assertEqual(len(columns), 4)

    def test_unused_columns_give_their_width_to_the_used_columns(self):
        # the short items fit next to the tall item in 1 column, so only 2 of the 4 columns get items
        boxes = [Box(600, 500, min_width=100)] + [Box(300, 100, min_width=100) for _ in range(3)]
        layout = self.make_layout(boxes)
        layout.setGeometry(QRect(0, 0, 2000, 600))

        self.assertEqual({box.geometry().x() for box in boxes}, {0, 610})
        self.assertEqual({box.geometry().width() for box in boxes}, {600})

    def test_hidden_widget_takes_no_room(self):
        boxes = [Box(100, 50), Box(100, 80), Box(100, 50)]
        boxes[1].setHidden(True)
        layout = self.make_layout(boxes)

        self.assertEqual(layout.heightForWidth(100), 50 + 10 + 50)

    def test_height_holds_the_margins_and_no_trailing_spacing(self):
        boxes = [Box(100, 50), Box(100, 60)]
        layout = self.make_layout(boxes, margin=3)

        # one column only: 2 items, 1 spacing between them, a margin above and below
        self.assertEqual(layout.heightForWidth(150), 3 + 50 + 10 + 60 + 3)

    def test_expanding_directions_is_none(self):
        layout = self.make_layout([Box(100, 50)])

        self.assertEqual(layout.expandingDirections(), Qt.Orientation(0))


if __name__ == '__main__':
    unittest.main()
