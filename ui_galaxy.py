import sys
import math
import random

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtCore import Qt, QTimer


class MaanavGalaxy(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Maanav AI")
        self.showFullScreen()

        self.stars = []

        # =============================
        # GALAXY SETTINGS
        # =============================

        arms = 4

        # =============================
        # DENSE CENTER STAR CLUSTER
        # =============================

        for i in range(700):

            radius = random.uniform(5, 110)

            angle = random.uniform(
                0,
                math.pi * 2
            )

            self.stars.append({
                "angle": angle,
                "radius": radius,
                "size": random.choice(
                    [1, 1, 1, 1, 2]
                ),
                "speed": random.uniform(
                    0.0003,
                    0.001
                ),
                "brightness": random.randint(
                    105,
                    165
                ),
                "phase": random.uniform(
                    0,
                    math.pi * 2
                )
            })

        # =============================
        # SPIRAL ARMS
        # =============================

        for i in range(1400):

            radius = random.uniform(
                70,
                650
            )

            arm = random.randint(
                0,
                arms - 1
            )

            angle = (
                arm * (math.pi * 2 / arms)
                + radius * 0.012
            )

            angle += random.gauss(
                0,
                0.16
            )

            self.stars.append({
                "angle": angle,
                "radius": radius,
                "size": random.choice(
                    [1, 1, 1, 1, 2, 2]
                ),
                "speed": random.uniform(
                    0.0004,
                    0.0015
                ),
                "brightness": random.randint(
                    75,
                    160
                ),
                "phase": random.uniform(
                    0,
                    math.pi * 2
                )
            })

        # =============================
        # MOUSE
        # =============================

        self.mouse_x = 0
        self.mouse_y = 0

        # =============================
        # ZOOM
        # =============================

        self.zoom = 1.0
        self.target_zoom = 1.0

        # =============================
        # ANIMATION
        # =============================

        self.time = 0

        self.timer = QTimer()

        self.timer.timeout.connect(
            self.animate
        )

        self.timer.start(16)

    # =============================
    # ANIMATION
    # =============================

    def animate(self):

        self.time += 0.03

        for star in self.stars:

            star["angle"] += star["speed"]

        # Smooth zoom
        self.zoom += (
            self.target_zoom - self.zoom
        ) * 0.08

        self.update()

    # =============================
    # DRAW GALAXY
    # =============================

    def paintEvent(self, event):

        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing
        )

        # Black space
        painter.fillRect(
            self.rect(),
            QColor(5, 5, 4)
        )

        center_x = self.width() / 2
        center_y = self.height() / 2

        # =============================
        # DRAW STARS
        # =============================

        for star in self.stars:

            angle = star["angle"]

            radius = (
                star["radius"]
                * self.zoom
            )

            # Spiral shape
            spiral = math.sin(
                radius * 0.018
            ) * 18

            x = (
                center_x
                + math.cos(angle)
                * (radius + spiral)
            )

            y = (
                center_y
                + math.sin(angle)
                * (radius + spiral)
                * 0.58
            )

            # =========================
            # MOUSE REPULSION
            # =========================

            dx = x - self.mouse_x
            dy = y - self.mouse_y

            distance = math.sqrt(
                dx * dx + dy * dy
            )

            if distance < 300 and distance > 1:

                force = (
                    300 - distance
                ) / 300

                # Push stars away
                x += dx * force * 0.9
                y += dy * force * 0.9

                # Curve stars around cursor
                x += -dy * force * 0.35
                y += dx * force * 0.35

            # =========================
            # COLOR
            # =========================

            ratio = min(
                star["radius"] / 650,
                1
            )

            # Warm, low-saturation gold keeps the galaxy visible without
            # turning the whole screen into a bright yellow glow.
            red = int(205 - ratio * 22)

            green = int(174 - ratio * 48)

            blue = int(82 - ratio * 38)

            # =========================
            # TWINKLE
            # =========================

            twinkle = (
                math.sin(
                    self.time * 2
                    + star["phase"]
                )
                + 1
            ) / 2

            alpha = int(
                star["brightness"]
                * (
                    0.7
                    + twinkle * 0.3
                )
            )

            # =========================
            # DRAW STAR
            # =========================

            size = star["size"]

            painter.setPen(
                Qt.PenStyle.NoPen
            )

            painter.setBrush(
                QColor(
                    red,
                    green,
                    blue,
                    alpha
                )
            )

            painter.drawEllipse(
                int(x - size / 2),
                int(y - size / 2),
                size,
                size
            )

        painter.end()

    # =============================
    # MOUSE
    # =============================

    def mouseMoveEvent(self, event):

        target_x = event.position().x()
        target_y = event.position().y()

        # Smooth cursor tracking
        self.mouse_x += (
            target_x - self.mouse_x
        ) * 0.18

        self.mouse_y += (
            target_y - self.mouse_y
        ) * 0.18

        self.update()

    # =============================
    # ZOOM
    # =============================

    def wheelEvent(self, event):

        if event.angleDelta().y() > 0:

            self.target_zoom += 0.15

        else:

            self.target_zoom -= 0.15

        self.target_zoom = max(
            0.5,
            min(
                self.target_zoom,
                2.5
            )
        )

    # =============================
    # RESET
    # =============================

    def mouseDoubleClickEvent(self, event):

        self.target_zoom = 1.0

    # =============================
    # ESC
    # =============================

    def keyPressEvent(self, event):

        if event.key() == Qt.Key.Key_Escape:

            self.close()


# =============================
# START
# =============================

app = QApplication(sys.argv)

window = MaanavGalaxy()

window.show()

sys.exit(
    app.exec()
)