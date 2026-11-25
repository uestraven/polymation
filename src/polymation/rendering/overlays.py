"""Turning timeline overlays into rendered images and frame positions.

Every overlay becomes an RGBA PNG plus an integer ``(x, y)`` placement. Text
overlays go through :mod:`polymation.rendering.text`, so arbitrary text is
already pixels before FFmpeg is involved.

Placement respects the timeline's safe-area inset: an anchor positions the
overlay against the *safe* rectangle, not the frame edge, so a title does not
sit flush against the bezel on a TV that overscans.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from polymation.config.channel import ChannelBrand
from polymation.errors import RenderError
from polymation.rendering.profiles import RenderProfile
from polymation.rendering.text import RenderedText, render_color_panel, render_text_image
from polymation.schemas.timeline import (
    Anchor,
    CompositionTimeline,
    Overlay,
    OverlayKind,
)

#: Default size, as a fraction of the frame, for each text overlay kind. Used
#: only to bound wrapping; the rendered box shrinks to its content.
_TEXT_WIDTH_FRACTION: dict[OverlayKind, float] = {
    OverlayKind.TITLE: 0.80,
    OverlayKind.SUBTITLE: 0.70,
    OverlayKind.LOWER_THIRD: 0.55,
    OverlayKind.INFO_TEXT: 0.45,
    OverlayKind.COMPARISON_LABEL: 0.40,
}


@dataclass(frozen=True)
class PlacedOverlay:
    """A rendered overlay with its resolved position and timing."""

    overlay_id: str
    image: RenderedText
    x: int
    y: int
    start_frame: int
    end_frame: int
    opacity: float

    @property
    def used_fallback_font(self) -> bool:
        """Whether text was drawn with a substitute typeface."""
        return self.image.used_fallback_font

    @property
    def truncated(self) -> bool:
        """Whether text had to be cut to fit its line budget."""
        return self.image.truncated


def _anchor_position(
    anchor: Anchor,
    *,
    overlay_width: int,
    overlay_height: int,
    frame_width: int,
    frame_height: int,
    inset_x: int,
    inset_y: int,
) -> tuple[int, int]:
    """Top-left position for ``anchor`` inside the safe rectangle."""
    left = inset_x
    centre_x = (frame_width - overlay_width) // 2
    right = frame_width - inset_x - overlay_width

    top = inset_y
    middle_y = (frame_height - overlay_height) // 2
    bottom = frame_height - inset_y - overlay_height

    horizontal = {
        Anchor.TOP_LEFT: left,
        Anchor.MIDDLE_LEFT: left,
        Anchor.BOTTOM_LEFT: left,
        Anchor.TOP_CENTER: centre_x,
        Anchor.MIDDLE_CENTER: centre_x,
        Anchor.BOTTOM_CENTER: centre_x,
        Anchor.TOP_RIGHT: right,
        Anchor.MIDDLE_RIGHT: right,
        Anchor.BOTTOM_RIGHT: right,
    }[anchor]

    vertical = {
        Anchor.TOP_LEFT: top,
        Anchor.TOP_CENTER: top,
        Anchor.TOP_RIGHT: top,
        Anchor.MIDDLE_LEFT: middle_y,
        Anchor.MIDDLE_CENTER: middle_y,
        Anchor.MIDDLE_RIGHT: middle_y,
        Anchor.BOTTOM_LEFT: bottom,
        Anchor.BOTTOM_CENTER: bottom,
        Anchor.BOTTOM_RIGHT: bottom,
    }[anchor]

    return horizontal, vertical


def render_overlay(
    overlay: Overlay,
    timeline: CompositionTimeline,
    profile: RenderProfile,
    brand: ChannelBrand,
    *,
    work_dir: Path,
    image_paths: dict[str, Path],
    max_lines: int | None = None,
) -> PlacedOverlay:
    """Render one overlay to an image and resolve its position.

    Args:
        overlay: the overlay to render.
        timeline: supplies the safe-area inset.
        profile: supplies the output geometry.
        brand: resolves ``palette:`` colours.
        work_dir: directory for the rendered PNG.
        image_paths: asset id to on-disk path, for image overlays.
        max_lines: line budget for text; ``None`` means unlimited.

    Raises:
        RenderError: if an image overlay's asset is not available.
    """
    destination = work_dir / f"overlay-{overlay.overlay_id}.png"

    if overlay.kind is OverlayKind.IMAGE:
        if overlay.image_asset_id is None:  # pragma: no cover - schema guarantees
            raise RenderError(f"overlay {overlay.overlay_id!r} has no image asset")
        source = image_paths.get(overlay.image_asset_id)
        if source is None or not source.is_file():
            raise RenderError(
                f"overlay {overlay.overlay_id!r} needs image asset "
                f"{overlay.image_asset_id!r}, which is not available on disk"
            )
        image = _prepare_image_overlay(overlay, profile, source, destination)
    elif overlay.kind is OverlayKind.COLOR_PANEL:
        if overlay.fill_color is None or overlay.width_fraction is None:
            raise RenderError(  # pragma: no cover - schema guarantees
                f"overlay {overlay.overlay_id!r} is a colour panel without fill or size"
            )
        image = render_color_panel(
            width=max(1, int(profile.width * overlay.width_fraction)),
            height=max(1, int(profile.height * (overlay.height_fraction or 0.1))),
            fill_color=overlay.fill_color,
            opacity=1.0,
            destination=destination,
            brand=brand,
        )
    else:
        if overlay.text is None:  # pragma: no cover - schema guarantees
            raise RenderError(f"overlay {overlay.overlay_id!r} is a text overlay with no text")
        style = overlay.style
        if overlay.width_fraction is None:
            style = style.model_copy(
                update={
                    "max_width_fraction": _TEXT_WIDTH_FRACTION.get(
                        overlay.kind, style.max_width_fraction
                    )
                }
            )
        else:
            style = style.model_copy(update={"max_width_fraction": overlay.width_fraction})

        image = render_text_image(
            overlay.text,
            style,
            frame_width=profile.width,
            frame_height=profile.height,
            destination=destination,
            brand=brand,
            max_lines=max_lines,
        )

    inset_x = int(timeline.safe_area_inset * profile.width)
    inset_y = int(timeline.safe_area_inset * profile.height)
    base_x, base_y = _anchor_position(
        overlay.anchor,
        overlay_width=image.width,
        overlay_height=image.height,
        frame_width=profile.width,
        frame_height=profile.height,
        inset_x=inset_x,
        inset_y=inset_y,
    )

    # Scale the authored offset if the timeline was written for a different
    # resolution at the same aspect ratio (preview vs final).
    scale = profile.width / timeline.width if timeline.width else 1.0
    x_position = base_x + round(overlay.offset_x_px * scale)
    y_position = base_y + round(overlay.offset_y_px * scale)

    return PlacedOverlay(
        overlay_id=overlay.overlay_id,
        image=image,
        x=x_position,
        y=y_position,
        start_frame=overlay.span.start_frame,
        end_frame=overlay.span.end_frame,
        opacity=overlay.opacity,
    )


def _prepare_image_overlay(
    overlay: Overlay,
    profile: RenderProfile,
    source: Path,
    destination: Path,
) -> RenderedText:
    """Resize an image asset to the overlay's requested fraction of the frame."""
    from PIL import Image

    from polymation.rendering.fixtures import FontResolution

    with Image.open(source) as opened:
        image = opened.convert("RGBA")

        if overlay.width_fraction is not None:
            target_width = max(1, int(profile.width * overlay.width_fraction))
            if overlay.height_fraction is not None:
                target_height = max(1, int(profile.height * overlay.height_fraction))
            else:
                # Preserve the asset's aspect ratio.
                target_height = max(1, round(image.height * target_width / image.width))
            image = image.resize((target_width, target_height), Image.LANCZOS)

        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="PNG")
        width, height = image.size

    return RenderedText(
        path=destination,
        width=width,
        height=height,
        lines=(),
        font=FontResolution(requested_family="(image)", path=None, is_fallback=False),
    )


def render_overlays(
    overlays: list[Overlay],
    timeline: CompositionTimeline,
    profile: RenderProfile,
    brand: ChannelBrand,
    *,
    work_dir: Path,
    image_paths: dict[str, Path],
    max_lines: int | None = None,
) -> list[PlacedOverlay]:
    """Render a list of overlays, preserving order."""
    return [
        render_overlay(
            overlay,
            timeline,
            profile,
            brand,
            work_dir=work_dir,
            image_paths=image_paths,
            max_lines=max_lines,
        )
        for overlay in overlays
    ]
