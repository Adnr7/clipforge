"""Create the small, dependency-free ICO used by the Windows build."""

from __future__ import annotations

import math
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'assets' / 'clipforge.ico'
SIZES = (16, 24, 32, 48, 64, 128, 256)

CHARCOAL = (16, 19, 23, 255)
STEEL = (62, 86, 101, 255)
BLUE = (184, 215, 232, 255)
CYAN = (136, 192, 208, 255)
CORAL = (229, 143, 138, 255)
TRANSPARENT = (0, 0, 0, 0)


def _rounded_rect(x: float, y: float, left: float, top: float, right: float, bottom: float, radius: float) -> bool:
    if left + radius <= x <= right - radius and top <= y <= bottom:
        return True
    if left <= x <= right and top + radius <= y <= bottom - radius:
        return True
    for cx, cy in ((left + radius, top + radius), (right - radius, top + radius),
                   (left + radius, bottom - radius), (right - radius, bottom - radius)):
        if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
            return True
    return False


def _distance_to_segment(x: float, y: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(x - ax, y - ay)
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy))


def _triangle(x: float, y: float, points: tuple[tuple[float, float], ...]) -> bool:
    sign = None
    for index, (ax, ay) in enumerate(points):
        bx, by = points[(index + 1) % len(points)]
        cross = (x - bx) * (ay - by) - (ax - bx) * (y - by)
        current = cross >= 0
        if sign is None:
            sign = current
        elif current != sign:
            return False
    return True


def _pixel(x: float, y: float) -> tuple[int, int, int, int]:
    if not _rounded_rect(x, y, 0.04, 0.04, 0.96, 0.96, 0.22):
        return TRANSPARENT
    color = CHARCOAL
    if _rounded_rect(x, y, 0.04, 0.04, 0.96, 0.96, 0.22) and not _rounded_rect(x, y, 0.075, 0.075, 0.925, 0.925, 0.19):
        color = STEEL

    # The open arc is the C in ClipForge; the coral slash is the decisive cut.
    cx, cy, radius = 0.52, 0.5, 0.235
    distance = abs(math.hypot(x - cx, y - cy) - radius)
    angle = abs(math.degrees(math.atan2(y - cy, x - cx)))
    if distance <= 0.037 and angle >= 58:
        color = BLUE
    if _distance_to_segment(x, y, 0.46, 0.66, 0.69, 0.34) <= 0.041:
        color = CORAL
    if _triangle(x, y, ((0.61, 0.49), (0.72, 0.51), (0.63, 0.61))):
        color = CORAL

    # Three editing perforations keep the mark anchored to the film/studio idea.
    for hole_y in (0.31, 0.5, 0.69):
        if (x - 0.22) ** 2 + (y - hole_y) ** 2 <= 0.032 ** 2:
            color = CYAN
    return color


def _render(size: int, scale: int = 4) -> bytes:
    pixels = []
    for py in range(size):
        for px in range(size):
            samples = []
            for sy in range(scale):
                for sx in range(scale):
                    samples.append(_pixel((px + (sx + 0.5) / scale) / size,
                                          (py + (sy + 0.5) / scale) / size))
            pixels.append(tuple(sum(sample[channel] for sample in samples) // len(samples)
                               for channel in range(4)))

    bgra = bytearray()
    for py in range(size - 1, -1, -1):
        for px in range(size):
            red, green, blue, alpha = pixels[py * size + px]
            bgra.extend((blue, green, red, alpha))
    mask_row = ((size + 31) // 32) * 4
    return (
        struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0,
                    size * size * 4, 0, 0, 0, 0)
        + bytes(bgra)
        + bytes(mask_row * size)
    )


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    images = [_render(size) for size in SIZES]
    header = struct.pack('<HHH', 0, 1, len(images))
    entries = bytearray()
    offset = 6 + 16 * len(images)
    for size, image in zip(SIZES, images):
        dimension = 0 if size == 256 else size
        entries.extend(struct.pack('<BBBBHHII', dimension, dimension, 0, 0, 1, 32,
                                   len(image), offset))
        offset += len(image)
    OUTPUT.write_bytes(header + entries + b''.join(images))
    print(f'Created {OUTPUT}')


if __name__ == '__main__':
    main()
