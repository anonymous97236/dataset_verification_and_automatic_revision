"""Pillow-based screenshot transformations for the Actor–Arbiter–Gatekeeper pipeline.

All functions receive and return RGB ``PIL.Image.Image`` objects. Candidate mappings
use an already anonymized label (for example ``Model 1`` or ``Model X``) mapped to an
action mapping with the reproducibility material's ``type`` and ``parameter`` fields.
"""

from __future__ import annotations

import ast
import colorsys
import math
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

_CANDIDATE_COLORS = ("#dc2626", "#1d4ed8", "#059669", "#d97706", "#7c3aed")

# These are the principal colors of the three action icons in the Actor Test
# page.  Keeping the overlay colors here makes the screenshot and its action
# cards unambiguous without introducing model-anonymization labels.
_ACTOR_TEST_OUTPUTS = (
    ("Actor 1", "#006cda", False),
    ("Actor 2", "#ab52fc", False),
    ("Reference", "#028d3b", True),
)

_ARBITER_TEST_OUTPUTS = (
    ("Arbiter 1", "#006cda", False),
    ("Arbiter 2", "#ab52fc", False),
    ("Reference", "#028d3b", True),
)


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = False):
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ) if bold else (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, max(8, int(size)))
        except OSError:
            continue
    return ImageFont.load_default()


def _action_type(action: Mapping[str, Any]) -> str:
    raw = str(action.get("type") or "").strip().lower()
    return {"tap": "click", "long_touch": "long_press", "longpress": "long_press"}.get(raw, raw)


def _parameter(action: Mapping[str, Any]) -> Any:
    return action.get("parameter")


def _parse_points(value: Any) -> list[tuple[float, float]]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            parsed = None
    if isinstance(parsed, (list, tuple)):
        if len(parsed) >= 2 and not isinstance(parsed[0], (list, tuple)):
            try:
                return [(float(parsed[0]), float(parsed[1]))]
            except (TypeError, ValueError):
                return []
        points: list[tuple[float, float]] = []
        for item in parsed:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    points.append((float(item[0]), float(item[1])))
                except (TypeError, ValueError):
                    pass
        if points:
            return points[:2]
    values = [float(token) for token in re.findall(r"-?\d+(?:\.\d+)?", str(value or ""))]
    if len(values) >= 4:
        return [(values[0], values[1]), (values[2], values[3])]
    return [(values[0], values[1])] if len(values) >= 2 else []


def _normalized_points(value: Any) -> list[tuple[float, float]]:
    points = _parse_points(value)
    if any(abs(coordinate) > 1.5 for point in points for coordinate in point):
        return [(x / 1000.0, y / 1000.0) for x, y in points]
    return points


def _normalized_bbox(value: Any) -> tuple[float, float, float, float] | None:
    """Parse one ``[x1, y1, x2, y2]`` box into screen-relative coordinates."""

    parsed = value
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            parsed = None
    if not isinstance(parsed, (list, tuple)) or len(parsed) < 4:
        return None
    try:
        x1, y1, x2, y2 = (float(part) for part in parsed[:4])
    except (TypeError, ValueError):
        return None
    if any(abs(coordinate) > 1.5 for coordinate in (x1, y1, x2, y2)):
        x1, y1, x2, y2 = (coordinate / 1000.0 for coordinate in (x1, y1, x2, y2))
    return x1, y1, x2, y2


def _rgba(color: str, alpha: int) -> tuple[int, int, int, int]:
    try:
        red, green, blue = ImageColor.getrgb(color)
    except ValueError:
        red, green, blue = (59, 130, 246)
    return red, green, blue, max(0, min(255, alpha))


def _draw_circle(draw: ImageDraw.ImageDraw, x: float, y: float, radius: float, color: str) -> None:
    draw.ellipse(
        [x - radius, y - radius, x + radius, y + radius],
        fill=_rgba(color, 95), outline=_rgba(color, 220), width=max(2, int(radius * 0.16)),
    )


def _draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], color: str, *, radius: float) -> None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return
    unit_x, unit_y = dx / length, dy / length
    start_trim = end_trim = radius + 2
    max_trim = max(0.0, length - 2.0)
    if start_trim + end_trim > max_trim:
        ratio = max_trim / max(1e-6, start_trim + end_trim)
        start_trim *= ratio
        end_trim *= ratio
    sx, sy = start[0] + unit_x * start_trim, start[1] + unit_y * start_trim
    ex, ey = end[0] - unit_x * end_trim, end[1] - unit_y * end_trim
    if math.hypot(ex - sx, ey - sy) <= 1.0:
        return
    draw.line([(sx, sy), (ex, ey)], fill=_rgba(color, 165), width=10)
    head = min(90, max(48, math.hypot(ex - sx, ey - sy) * 0.38))
    angle = math.atan2(dy, dx)
    delta = math.pi / 2.9
    draw.polygon(
        [(ex, ey), (ex - head * math.cos(angle - delta), ey - head * math.sin(angle - delta)), (ex - head * math.cos(angle + delta), ey - head * math.sin(angle + delta))],
        fill=_rgba(color, 165),
    )


def _draw_dashed_connector(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    color: str,
    *,
    width: int = 10,
    dash_length: float = 30.0,
    gap_length: float = 20.0,
) -> None:
    """Draw the arbitration side-label connector with its source parameters."""

    dx, dy = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(dx, dy)
    if distance <= 1e-6:
        return
    unit_x, unit_y = dx / distance, dy / distance
    offset = 0.0
    while offset < distance:
        segment_end = min(offset + dash_length, distance)
        draw.line(
            [
                (start[0] + unit_x * offset, start[1] + unit_y * offset),
                (start[0] + unit_x * segment_end, start[1] + unit_y * segment_end),
            ],
            fill=_rgba(color, 230),
            width=max(1, int(width)),
        )
        offset += dash_length + gap_length


def overlay_action_parameter(image: Image.Image, action: Mapping[str, Any], *, color: str = "#dc2626", marker_scale: float = 1.0) -> Image.Image:
    """Overlay a single action parameter: a point or two-point motion arrow."""

    base = image.convert("RGB")
    action_type, points = _action_type(action), _normalized_points(_parameter(action))
    if action_type not in {"click", "long_press", "scroll", "swipe", "drag"} or not points:
        return base
    width, height = base.size
    radius = max(8.0, min(width, height) * 0.04 * max(marker_scale, 0.1))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    pixel_points = [(max(radius, min(width - radius, x * width)), max(radius, min(height - radius, y * height))) for x, y in points]
    _draw_circle(draw, *pixel_points[0], radius, color)
    if action_type in {"scroll", "swipe", "drag"} and len(pixel_points) >= 2:
        _draw_circle(draw, *pixel_points[1], radius, color)
        _draw_arrow(draw, pixel_points[0], pixel_points[1], color, radius=radius)
    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")


def append_previous_footer(image: Image.Image, operation: str = "", *, label: str = "Previous") -> Image.Image:
    """Add the white previous-screen footer used by all three pipeline roles."""

    base = image.convert("RGB")
    width, height = base.size
    footer_height = max(96, int(height * 0.14))
    output = Image.new("RGB", (width, height + footer_height), "white")
    output.paste(base, (0, 0))
    draw = ImageDraw.Draw(output)
    label_font, operation_font = _font(footer_height * 0.26, True), _font(footer_height * 0.18)
    operation = str(operation or "").strip() or "N/A"
    words, lines, current = operation.split(), [], ""
    for word in words or [operation]:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=operation_font)[2] > width * 0.92:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    y = height + 6
    for text, font in [(label, label_font), *[(line, operation_font) for line in lines]]:
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) / 2, y), text, fill="black", font=font)
        y += max(16, bbox[3] - bbox[1])
    return output


def build_missing_screenshot(width: int, height: int, message: str) -> Image.Image:
    """Create the unavailable-screenshot placeholder used for a first step.

    The absent previous image is represented explicitly, rather than duplicating
    the current screenshot.
    """

    canvas = Image.new("RGB", (max(320, int(width)), max(320, int(height))), "white")
    text = str(message or "").strip()
    if not text:
        return canvas
    draw = ImageDraw.Draw(canvas)
    font = _font(max(24, int(min(canvas.size) * 0.075)))
    # Keep the short fallback message legible without relying on a fixed image
    # resolution or a system-specific font.
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > canvas.width * 0.8:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    line_height = max(1, draw.textbbox((0, 0), "Ag", font=font)[3])
    total_height = line_height * len(lines)
    y = max(10, (canvas.height - total_height) // 2)
    for line in lines:
        bounds = draw.textbbox((0, 0), line, font=font)
        x = max(10, (canvas.width - (bounds[2] - bounds[0])) // 2)
        draw.text((x, y), line, fill=(20, 20, 20), font=font)
        y += line_height
    return canvas


def build_previous_screenshot(
    previous_image: Image.Image,
    previous_action: Mapping[str, Any] | None = None,
    previous_operation: str = "",
    *,
    marker_scale: float = 1.0,
) -> Image.Image:
    """Build the common previous screenshot: action overlay followed by footer."""

    transformed = overlay_action_parameter(previous_image, previous_action or {}, marker_scale=marker_scale)
    return append_previous_footer(transformed, previous_operation)


def _grid_colors(image: Image.Image) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    thumbnail = image.convert("RGB").resize((max(1, min(128, image.width)), max(1, min(128, image.height))))
    pixels = list(thumbnail.getdata())
    selected = sorted(pixels, key=lambda pixel: colorsys.rgb_to_hsv(*(channel / 255.0 for channel in pixel))[1], reverse=True)[:max(1, len(pixels) // 20)]
    representative = tuple(sum(pixel[index] for pixel in selected) // len(selected) for index in range(3))
    major = tuple(255 - channel for channel in representative)
    hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255.0 for channel in representative))
    minor = tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb((hue + 0.5) % 1.0, saturation, value))
    return major, minor


def overlay_coordinate_grid(image: Image.Image, *, minor_step: float = 0.05) -> Image.Image:
    """Draw the Actor's in-screen 0--1000 integer coordinate grid."""

    canvas = image.convert("RGB").copy()
    width, height = canvas.size
    major, minor = _grid_colors(canvas)
    draw = ImageDraw.Draw(canvas)
    # This follows the source grid's placement: 100-unit major labels appear
    # on both axes, while 50-unit minor labels appear on the vertical axis.
    base_font_size = min(width, height) * 0.032
    major_font, minor_font = _font(base_font_size), _font(base_font_size * 0.78)
    steps = int(round(1.0 / minor_step))
    for index in range(steps + 1):
        value = index * minor_step
        x, y = min(width - 1, round(value * width)), min(height - 1, round(value * height))
        is_major = abs(value * 10 - round(value * 10)) <= 1e-6
        color, line_width = (major, 2) if is_major else (minor, 1)
        draw.line([(x, 0), (x, height - 1)], fill=color, width=line_width)
        draw.line([(0, y), (width - 1, y)], fill=color, width=line_width)
        if index and is_major:
            text = str(round(value * 1000))
            bbox = draw.textbbox((0, 0), text, font=major_font)
            text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            # x-axis labels sit at the top; y-axis labels sit at the left.
            draw.text((max(1, min(width - text_width - 1, x + 3)), 1), text, fill=major, font=major_font)
            draw.text((2, max(1, min(height - text_height - 1, y + 3))), text, fill=major, font=major_font)
        elif index:
            text = str(round(value * 1000))
            bbox = draw.textbbox((0, 0), text, font=minor_font)
            text_height = bbox[3] - bbox[1]
            draw.text((2, max(1, min(height - text_height - 1, y + 2))), text, fill=minor, font=minor_font)
    draw.text((2, 2), "0", fill=major, font=major_font)
    return canvas


def overlay_axis_ticks(image: Image.Image, axis: str, *, minor_step: float = 0.05) -> Image.Image:
    """Add the border ticks used for an independently inferred bbox edge.

    Top/bottom uses y ticks on the side margins; left/right uses x ticks on the
    top and bottom margins. The screenshot remains unscaled.
    """

    axis = str(axis or "").strip().lower()
    if axis not in {"top", "bottom", "left", "right"}:
        raise ValueError(f"Unsupported bbox axis: {axis!r}")
    base = image.convert("RGB")
    width, height = base.size
    if width < 2 or height < 2:
        return base
    vertical = axis in {"top", "bottom"}
    major_font, minor_font = _font(max(10, min(width, height) * 0.034)), _font(max(8, min(width, height) * 0.029))
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
    def extent(text, font):
        box = probe.textbbox((0, 0), text, font=font)
        return box[2] - box[0] if vertical else box[3] - box[1]
    pad = max(30, int(max(extent("900", major_font), extent("950", minor_font)) + min(width, height) * 0.03 + 8))
    canvas = Image.new("RGB", (width + 2 * pad, height), "white") if vertical else Image.new("RGB", (width, height + 2 * pad), "white")
    canvas.paste(base, (pad, 0) if vertical else (0, pad))
    draw = ImageDraw.Draw(canvas)
    major_tick, minor_tick = max(12, int(min(width, height) * 0.03)), max(8, int(min(width, height) * 0.02))
    for index in range(1, int(round(1.0 / minor_step))):
        value = index * minor_step
        major = abs(value * 10 - round(value * 10)) <= 1e-6
        color, line_width, tick, font = ((0, 0, 0), 5, major_tick, major_font) if major else ((64, 64, 64), 3, minor_tick, minor_font)
        text = str(round(value * 1000))
        box = draw.textbbox((0, 0), text, font=font)
        text_width, text_height = box[2] - box[0], box[3] - box[1]
        if vertical:
            y = min(height - 1, max(0, round(value * (height - 1))))
            left, right = pad - 1, pad + width
            draw.line([(left, y), (max(0, left - tick), y)], fill=color, width=line_width)
            draw.line([(right, y), (min(canvas.width - 1, right + tick), y)], fill=color, width=line_width)
            text_y = max(0, min(height - text_height, y - text_height / 2))
            draw.text((max(0, left - tick - text_width - 2), text_y), text, fill=color, font=font)
            draw.text((min(canvas.width - text_width, right + tick + 2), text_y), text, fill=color, font=font)
        else:
            x = min(width - 1, max(0, round(value * (width - 1))))
            top, bottom = pad - 1, pad + height
            draw.line([(x, top), (x, max(0, top - tick))], fill=color, width=line_width)
            draw.line([(x, bottom), (x, min(canvas.height - 1, bottom + tick))], fill=color, width=line_width)
            text_x = max(0, min(width - text_width, x - text_width / 2))
            draw.text((text_x, max(0, top - tick - text_height - 2)), text, fill=color, font=font)
            draw.text((text_x, min(canvas.height - text_height, bottom + tick + 2)), text, fill=color, font=font)
    return canvas


def actor_screenshots(previous_image: Image.Image, current_image: Image.Image, *, previous_action: Mapping[str, Any] | None = None, previous_operation: str = "") -> list[Image.Image]:
    """Return Actor inputs in paper order: previous, current plain, current grid."""

    return [build_previous_screenshot(previous_image, previous_action, previous_operation), current_image.convert("RGB"), overlay_coordinate_grid(current_image)]


def _label_box(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    label: str,
    color: str,
    font,
    *,
    border_width: int = 10,
) -> tuple[float, float, float, float]:
    bbox = draw.textbbox((0, 0), label, font=font)
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    box_width, box_height = text_width + 24, max(68, text_height + 36)
    left, top = x - box_width / 2, y - box_height / 2
    draw.rectangle(
        [left, top, left + box_width, top + box_height],
        fill=(255, 255, 255, 235),
        outline=_rgba(color, 255),
        width=max(1, int(border_width)),
    )
    draw.text((left + 12 - bbox[0], top + (box_height - text_height) / 2 - bbox[1]), label, fill=_rgba(color, 255), font=font)
    return left, top, left + box_width, top + box_height


def _free_label_y(target: float, box_height: float, image_height: int, occupied: list[tuple[float, float]]) -> float:
    minimum, maximum = box_height / 2 + 10, image_height - box_height / 2 - 10
    for offset in [0, *[direction * max(20, box_height * 0.72) * index for index in range(1, 16) for direction in (-1, 1)]]:
        center = max(minimum, min(maximum, target + offset))
        if all(center + box_height / 2 + 6 < top or center - box_height / 2 - 6 > bottom for top, bottom in occupied):
            return center
    return max(minimum, min(maximum, target))


def overlay_candidate_actions(current_image: Image.Image, candidates: Mapping[str, Mapping[str, Any]]) -> Image.Image:
    """Draw colored candidate actions, label boxes, and dashed label connectors.

    Use this same function for Arbiter candidates and Gatekeeper's ``REFERENCE`` plus
    alternative candidates. Candidate order is preserved and determines color.
    """

    base = current_image.convert("RGB")
    width, height = base.size
    font = _font(64, True)
    labels = [str(label).upper() for label in candidates]
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
    side_padding = max((probe.textbbox((0, 0), label, font=font)[2] + 44 for label in labels), default=120)
    canvas = Image.new("RGB", (width + side_padding * 2, height), "white")
    canvas.paste(base, (side_padding, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    radius = max(24.0, min(48.0, width * 0.04))
    occupied = {"left": [], "right": []}

    for index, (label, action) in enumerate(candidates.items()):
        points = _normalized_points(_parameter(action))
        if not points:
            continue
        color = _CANDIDATE_COLORS[index % len(_CANDIDATE_COLORS)]
        pixel_points = [
            (max(0.0, min(float(width), x * width)) + side_padding, max(0.0, min(float(height), y * height)))
            for x, y in points
        ]
        _draw_circle(draw, *pixel_points[0], radius, color)
        action_type = _action_type(action)
        if action_type in {"scroll", "swipe", "drag"} and len(pixel_points) >= 2:
            _draw_circle(draw, *pixel_points[1], radius, color)
            _draw_arrow(draw, pixel_points[0], pixel_points[1], color, radius=radius)
            anchor_x, anchor_y = ((pixel_points[0][0] + pixel_points[1][0]) / 2, (pixel_points[0][1] + pixel_points[1][1]) / 2)
        else:
            anchor_x, anchor_y = pixel_points[0]
        side = "left" if anchor_x - side_padding <= width / 2 else "right"
        label_box = probe.textbbox((0, 0), str(label).upper(), font=font)
        box_height = max(68, label_box[3] - label_box[1] + 36)
        label_y = _free_label_y(anchor_y, box_height, height, occupied[side])
        label_x = side_padding / 2 if side == "left" else side_padding + width + side_padding / 2
        left, top, right, bottom = _label_box(draw, label_x, label_y, str(label).upper(), color, font)
        occupied[side].append((top, bottom))
        line_end_x = right if side == "left" else left
        _draw_dashed_connector(draw, (anchor_x, anchor_y), (line_end_x, label_y), color)
    return canvas


def _output_action_visualization(
    current_image: Image.Image,
    outputs: tuple[tuple[str, str, bool, Mapping[str, Any] | None], ...],
) -> Image.Image:
    """Draw the completed test-page actions on an unmodified current screenshot."""

    base = current_image.convert("RGB")
    width, height = base.size
    # Match arbitration_common._build_arbitration_current_overlay_image.
    font = _font(64, True)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1), "white"))
    drawable_outputs: list[tuple[str, str, bool, Mapping[str, Any], list[tuple[float, float]], tuple[float, float, float, float] | None]] = []
    for label, color, show_bbox, action in outputs:
        if not isinstance(action, Mapping):
            continue
        points = _normalized_points(_parameter(action))
        bbox = _normalized_bbox(action.get("bbox")) if show_bbox else None
        action_type = _action_type(action)
        has_parameter = action_type in {"click", "long_press", "scroll", "swipe", "drag"} and bool(points)
        if has_parameter or bbox is not None:
            drawable_outputs.append((label, color, show_bbox, action, points, bbox))

    labels = [label.upper() for label, _color, _show_bbox, _action, _points, _bbox in drawable_outputs]
    side_padding = max((probe.textbbox((0, 0), label, font=font)[2] + 44 for label in labels), default=160)
    canvas = Image.new("RGB", (width + side_padding * 2, height), "white")
    canvas.paste(base, (side_padding, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    radius = max(24.0, min(48.0, width * 0.04))
    occupied = {"left": [], "right": []}

    for label, color, _show_bbox, action, points, bbox in drawable_outputs:
        action_type = _action_type(action)
        has_parameter = action_type in {"click", "long_press", "scroll", "swipe", "drag"} and bool(points)
        anchor_x: float
        anchor_y: float
        if has_parameter:
            pixel_points = [
                (max(0.0, min(float(width), x * width)) + side_padding, max(0.0, min(float(height), y * height)))
                for x, y in points
            ]
            _draw_circle(draw, *pixel_points[0], radius, color)
            if action_type in {"scroll", "swipe", "drag"} and len(pixel_points) >= 2:
                _draw_circle(draw, *pixel_points[1], radius, color)
                _draw_arrow(draw, pixel_points[0], pixel_points[1], color, radius=radius)
                anchor_x = (pixel_points[0][0] + pixel_points[1][0]) / 2
                anchor_y = (pixel_points[0][1] + pixel_points[1][1]) / 2
            else:
                anchor_x, anchor_y = pixel_points[0]
        else:
            anchor_x = anchor_y = 0.0

        if bbox is not None:
            x1, y1, x2, y2 = bbox
            left = max(0.0, min(float(width), min(x1, x2) * width)) + side_padding
            top = max(0.0, min(float(height), min(y1, y2) * height))
            right = max(0.0, min(float(width), max(x1, x2) * width)) + side_padding
            bottom = max(0.0, min(float(height), max(y1, y2) * height))
            draw.rectangle([left, top, right, bottom], outline=_rgba(color, 255), width=10)
            # A reference bbox is its most useful connector target even when
            # the accompanying click point is also available.
            anchor_x, anchor_y = (left + right) / 2, (top + bottom) / 2

        side = "left" if anchor_x - side_padding <= width / 2 else "right"
        label_box = probe.textbbox((0, 0), label, font=font)
        box_height = max(68, label_box[3] - label_box[1] + 36)
        label_y = _free_label_y(anchor_y, box_height, height, occupied[side])
        label_x = side_padding / 2 if side == "left" else side_padding + width + side_padding / 2
        left, top, right, bottom = _label_box(draw, label_x, label_y, label.upper(), color, font, border_width=10)
        occupied[side].append((top, bottom))
        line_end_x = right if side == "left" else left
        _draw_dashed_connector(draw, (anchor_x, anchor_y), (line_end_x, label_y), color)
    return canvas


def actor_test_output_visualization(
    current_image: Image.Image,
    *,
    actor1_action: Mapping[str, Any] | None = None,
    actor2_action: Mapping[str, Any] | None = None,
    reference_action: Mapping[str, Any] | None = None,
) -> Image.Image:
    """Visualize completed Actor outputs and the reference action."""

    return _output_action_visualization(
        current_image,
        tuple(
            (*output, action)
            for output, action in zip(_ACTOR_TEST_OUTPUTS, (actor1_action, actor2_action, reference_action))
        ),
    )


def arbiter_test_output_visualization(
    current_image: Image.Image,
    *,
    arbiter1_action: Mapping[str, Any] | None = None,
    arbiter2_action: Mapping[str, Any] | None = None,
    reference_action: Mapping[str, Any] | None = None,
) -> Image.Image:
    """Visualize completed Arbiter selections and the reference action."""

    return _output_action_visualization(
        current_image,
        tuple(
            (*output, action)
            for output, action in zip(_ARBITER_TEST_OUTPUTS, (arbiter1_action, arbiter2_action, reference_action))
        ),
    )


def arbiter_screenshots(previous_image: Image.Image, current_image: Image.Image, candidates: Mapping[str, Mapping[str, Any]], *, previous_action: Mapping[str, Any] | None = None, previous_operation: str = "") -> list[Image.Image]:
    """Return Arbiter inputs: common previous image and anonymized candidate overlay."""

    return [
        build_previous_screenshot(previous_image, previous_action, previous_operation, marker_scale=0.5),
        overlay_candidate_actions(current_image, candidates),
    ]


def gatekeeper_screenshots(previous_image: Image.Image, current_image: Image.Image, reference_action: Mapping[str, Any], alternatives: Mapping[str, Mapping[str, Any]], *, previous_action: Mapping[str, Any] | None = None, previous_operation: str = "") -> list[Image.Image]:
    """Return Gatekeeper inputs with ``REFERENCE`` plus alternative overlays."""

    candidates: dict[str, Mapping[str, Any]] = {"REFERENCE": reference_action}
    candidates.update(alternatives)
    return [
        build_previous_screenshot(previous_image, previous_action, previous_operation, marker_scale=0.5),
        overlay_candidate_actions(current_image, candidates),
    ]
