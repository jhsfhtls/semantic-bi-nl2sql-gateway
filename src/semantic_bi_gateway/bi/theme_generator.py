"""WCAG 2.1 AA corporate Power BI JSON theme generator and contrast validator."""

from __future__ import annotations

import json
import re
from typing import Any

_HEX_RE = re.compile(r"^#([0-9A-Fa-f]{6})$")


def _srgb_channel_to_linear(channel_255: int) -> float:
    srgb = channel_255 / 255.0
    if srgb <= 0.04045:
        return srgb / 12.92
    return ((srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """Compute WCAG 2.1 relative luminance for a #RRGGBB hex color."""
    match = _HEX_RE.match(hex_color.strip())
    if not match:
        raise ValueError(f"Invalid hex color '{hex_color}'; expected #RRGGBB.")
    raw = match.group(1)
    r = _srgb_channel_to_linear(int(raw[0:2], 16))
    g = _srgb_channel_to_linear(int(raw[2:4], 16))
    b = _srgb_channel_to_linear(int(raw[4:6], 16))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Compute WCAG 2.1 contrast ratio between two #RRGGBB colors (1.0 to 21.0)."""
    lum_a = relative_luminance(fg_hex)
    lum_b = relative_luminance(bg_hex)
    lighter = max(lum_a, lum_b)
    darker = min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def generate_corporate_theme(project_name: str) -> dict[str, Any]:
    """Generate a deterministic WCAG 2.1 AA compliant Power BI corporate theme."""
    return {
        "name": f"{project_name} WCAG AA Corporate Theme",
        "dataColors": [
            "#0B4F8C",
            "#9A3412",
            "#0F766E",
            "#6D28D9",
            "#B91C1C",
            "#1E293B",
            "#0369A1",
            "#7C2D12",
        ],
        "background": "#FFFFFF",
        "foreground": "#0F172A",
        "secondaryBackground": "#F8FAFC",
        "tableAccent": "#0B4F8C",
        "good": "#166534",
        "neutral": "#92400E",
        "bad": "#B91C1C",
        "maximum": "#0B4F8C",
        "center": "#475569",
        "minimum": "#B91C1C",
        "textClasses": {
            "callout": {
                "fontSize": 28,
                "fontFace": "Segoe UI Semibold",
                "color": "#0F172A",
            },
            "title": {
                "fontSize": 14,
                "fontFace": "Segoe UI Semibold",
                "color": "#0F172A",
            },
            "header": {
                "fontSize": 12,
                "fontFace": "Segoe UI Semibold",
                "color": "#1E293B",
            },
            "label": {
                "fontSize": 10,
                "fontFace": "Segoe UI",
                "color": "#334155",
            },
        },
        "visualStyles": {
            "*": {
                "*": {
                    "background": [{"color": {"solid": {"color": "#FFFFFF"}}}],
                    "border": [
                        {
                            "show": True,
                            "color": {"solid": {"color": "#475569"}},
                            "radius": 6,
                        }
                    ],
                }
            }
        },
    }


def validate_wcag_aa_theme(theme: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate WCAG 2.1 AA contrast ratios (>= 4.5:1 text, >= 3.0:1 data/UI colors)."""
    errors: list[str] = []
    bg = str(theme.get("background", "#FFFFFF"))
    secondary_bg = str(theme.get("secondaryBackground", bg))

    fg = str(theme.get("foreground", ""))
    for surface_name, surface_hex in (("background", bg), ("secondaryBackground", secondary_bg)):
        try:
            ratio = contrast_ratio(fg, surface_hex)
            if ratio < 4.5:
                errors.append(
                    f"foreground ({fg}) on {surface_name} ({surface_hex}) has ratio {ratio:.2f}:1 (< 4.50:1)."
                )
        except ValueError as exc:
            errors.append(str(exc))

    text_classes = theme.get("textClasses", {})
    if not isinstance(text_classes, dict) or not text_classes:
        errors.append("Theme missing 'textClasses'.")
    else:
        for class_name, spec in text_classes.items():
            color = str(spec.get("color", "")) if isinstance(spec, dict) else ""
            for surface_name, surface_hex in (("background", bg), ("secondaryBackground", secondary_bg)):
                try:
                    ratio = contrast_ratio(color, surface_hex)
                    if ratio < 4.5:
                        errors.append(
                            f"textClasses.{class_name} ({color}) on {surface_name} ({surface_hex}) "
                            f"has ratio {ratio:.2f}:1 (< 4.50:1)."
                        )
                except ValueError as exc:
                    errors.append(str(exc))

    for status_key in ("tableAccent", "good", "neutral", "bad"):
        val = str(theme.get(status_key, ""))
        try:
            ratio = contrast_ratio(val, bg)
            if ratio < 4.5:
                errors.append(f"{status_key} ({val}) on {bg} has ratio {ratio:.2f}:1 (< 4.50:1).")
        except ValueError as exc:
            errors.append(str(exc))

    data_colors = theme.get("dataColors", [])
    if not isinstance(data_colors, list) or len(data_colors) < 4:
        errors.append("Theme must define at least 4 dataColors.")
    else:
        for idx, color in enumerate(data_colors):
            try:
                ratio = contrast_ratio(str(color), bg)
                if ratio < 3.0:
                    errors.append(
                        f"dataColors[{idx}] ({color}) on {bg} has ratio {ratio:.2f}:1 (< 3.00:1)."
                    )
            except ValueError as exc:
                errors.append(str(exc))

    return len(errors) == 0, errors


def render_corporate_theme_json(project_name: str) -> str:
    """Serialize corporate theme deterministically with trailing LF."""
    theme = generate_corporate_theme(project_name)
    return json.dumps(theme, indent=2) + "\n"
