"""Resolving timeline colour specifications against a channel's brand palette.

A timeline stores either an explicit ``#RRGGBB`` or a ``palette:<name>``
reference. References keep brand values out of project data, so changing the
palette does not require rewriting every project's timeline.
"""

from __future__ import annotations

from polymation.config.channel import ChannelBrand
from polymation.errors import ConfigurationError
from polymation.schemas.timeline import HEX_COLOR_PATTERN, PALETTE_REFERENCE_PATTERN

#: Fallback used only when no brand is available (for example in unit tests of
#: pure geometry). Never used on a render path, which always has a brand.
_FALLBACK_RGB = (0, 0, 0)


def resolve_rgb(spec: str, brand: ChannelBrand | None = None) -> tuple[int, int, int]:
    """Resolve a colour specification to an ``(r, g, b)`` tuple.

    Raises:
        ConfigurationError: if the spec is malformed, or names a palette colour
            that the brand does not define.
    """
    if HEX_COLOR_PATTERN.match(spec):
        digits = spec.lstrip("#")
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))

    if PALETTE_REFERENCE_PATTERN.match(spec):
        name = spec.split(":", 1)[1]
        if brand is None:
            return _FALLBACK_RGB
        available = sorted(type(brand.palette).model_fields)
        if name not in available:
            raise ConfigurationError(
                f"colour {spec!r} refers to palette entry {name!r}, which channel "
                f"{brand.channel_id!r} does not define. Available: {', '.join(available)}."
            )
        return brand.palette.as_rgb(name)

    raise ConfigurationError(f"colour {spec!r} is neither '#RRGGBB' nor 'palette:<name>'.")


def resolve_rgba(
    spec: str, opacity: float, brand: ChannelBrand | None = None
) -> tuple[int, int, int, int]:
    """Resolve a colour plus an opacity in ``[0, 1]`` to an RGBA tuple."""
    if not 0.0 <= opacity <= 1.0:
        raise ValueError(f"opacity must be in [0, 1], got {opacity}")
    red, green, blue = resolve_rgb(spec, brand)
    return (red, green, blue, round(opacity * 255))


def to_ffmpeg_color(spec: str, brand: ChannelBrand | None = None) -> str:
    """Resolve a colour to FFmpeg's ``0xRRGGBB`` notation.

    Only ever applied to palette-resolved or hex-validated values, so the result
    is always a fixed-format literal and never operator-supplied text.
    """
    red, green, blue = resolve_rgb(spec, brand)
    return f"0x{red:02X}{green:02X}{blue:02X}"
