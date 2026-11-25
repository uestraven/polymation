"""Channel brand and editorial configuration.

Brand values live in ``channels/<channel_id>/*.yaml`` and are loaded as data.
No channel-specific colour, font or duration is hardcoded in the rendering or
CLI code: the renderer is handed a :class:`ChannelBrand` and reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError, field_validator

from polymation.config.paths import Workspace
from polymation.errors import ConfigurationError
from polymation.schemas.base import SCHEMA_VERSION, PolymationModel

HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: Default channel used when the operator does not name one.
DEFAULT_CHANNEL_ID = "polymation"


class ColorPalette(PolymationModel):
    """Named brand colours, stored as 6-digit hex."""

    crt_amber: str
    deep_indigo: str
    parchment_cream: str
    vermilion: str

    @field_validator("*")
    @classmethod
    def _check_hex(cls, value: str) -> str:
        if not HEX_COLOR_PATTERN.match(value):
            raise ValueError(f"{value!r} is not a 6-digit hex colour such as '#191D38'")
        return value.upper()

    def as_ffmpeg_color(self, name: str) -> str:
        """Return a palette colour in FFmpeg's ``0xRRGGBB`` notation."""
        value = getattr(self, name, None)
        if not isinstance(value, str):
            raise ConfigurationError(
                f"Unknown palette colour {name!r}. Available: "
                f"{', '.join(sorted(type(self).model_fields))}."
            )
        return "0x" + value.lstrip("#").upper()

    def as_rgb(self, name: str) -> tuple[int, int, int]:
        """Return a palette colour as an ``(r, g, b)`` tuple for Pillow."""
        value = getattr(self, name, None)
        if not isinstance(value, str):
            raise ConfigurationError(
                f"Unknown palette colour {name!r}. Available: "
                f"{', '.join(sorted(type(self).model_fields))}."
            )
        digits = value.lstrip("#")
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


class ProposedFont(PolymationModel):
    """A font the brand direction proposes. Availability is checked separately."""

    family: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=32)


class Typography(PolymationModel):
    """Typeface direction and the specific families proposed for it."""

    primary_direction: str
    secondary_direction: str
    proposed_fonts: list[ProposedFont] = Field(default_factory=list)


class ChannelBrand(PolymationModel):
    """``channels/<channel_id>/brand.yaml``."""

    schema_version: str = SCHEMA_VERSION
    channel_id: str = Field(min_length=1, max_length=64)
    provisional: bool = True
    name: str = Field(min_length=1, max_length=128)
    tagline: str = Field(min_length=1, max_length=256)
    language: str = Field(default="en", min_length=2, max_length=16)
    palette: ColorPalette
    typography: Typography


class YearRange(PolymationModel):
    """An inclusive year range."""

    start_year: int = Field(ge=1900, le=2200)
    end_year: int = Field(ge=1900, le=2200)

    @field_validator("end_year")
    @classmethod
    def _check_order(cls, value: int, info: Any) -> int:
        start = info.data.get("start_year")
        if isinstance(start, int) and value < start:
            raise ValueError(f"end_year {value} precedes start_year {start}")
        return value


class DurationBand(PolymationModel):
    """Permitted duration range for one output format."""

    min_seconds: int = Field(gt=0, le=7200)
    max_seconds: int = Field(gt=0, le=7200)

    @field_validator("max_seconds")
    @classmethod
    def _check_order(cls, value: int, info: Any) -> int:
        minimum = info.data.get("min_seconds")
        if isinstance(minimum, int) and value < minimum:
            raise ValueError(f"max_seconds {value} is below min_seconds {minimum}")
        return value


class PillarDefinition(PolymationModel):
    """One content pillar."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)


class ChannelEditorial(PolymationModel):
    """``channels/<channel_id>/editorial.yaml``."""

    schema_version: str = SCHEMA_VERSION
    channel_id: str = Field(min_length=1, max_length=64)
    historical_scope: YearRange
    primary_subjects: list[str] = Field(min_length=1)
    durations: dict[str, DurationBand]
    content_pillars: list[PillarDefinition] = Field(min_length=1)
    editorial_principles: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class ChannelConfig:
    """A channel's brand and editorial configuration, loaded together."""

    brand: ChannelBrand
    editorial: ChannelEditorial

    @property
    def channel_id(self) -> str:
        """The channel's identifier."""
        return self.brand.channel_id

    @property
    def is_provisional(self) -> bool:
        """Whether the brand values are provisional rather than approved artwork."""
        return self.brand.provisional


def _load_yaml_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(
            f"Channel configuration file {path} is missing. Expected a YAML document there."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Channel configuration {path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"Channel configuration {path} must contain a YAML mapping at the top level."
        )
    return raw


def load_channel_config(
    workspace: Workspace, channel_id: str = DEFAULT_CHANNEL_ID
) -> ChannelConfig:
    """Load and validate a channel's brand and editorial configuration.

    Raises:
        ConfigurationError: if either file is missing, unparsable or invalid.
    """
    channel_dir = workspace.channel_dir(channel_id)

    brand_payload = _load_yaml_document(channel_dir / "brand.yaml")
    editorial_payload = _load_yaml_document(channel_dir / "editorial.yaml")

    try:
        brand = ChannelBrand.model_validate(brand_payload)
    except ValidationError as exc:
        raise ConfigurationError(f"{channel_dir / 'brand.yaml'} is invalid:\n{exc}") from exc

    try:
        editorial = ChannelEditorial.model_validate(editorial_payload)
    except ValidationError as exc:
        raise ConfigurationError(f"{channel_dir / 'editorial.yaml'} is invalid:\n{exc}") from exc

    if brand.channel_id != channel_id or editorial.channel_id != channel_id:
        raise ConfigurationError(
            f"Channel configuration in {channel_dir} declares channel_id "
            f"{brand.channel_id!r}/{editorial.channel_id!r} but lives in a directory "
            f"named {channel_id!r}."
        )

    return ChannelConfig(brand=brand, editorial=editorial)
