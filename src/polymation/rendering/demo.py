"""The technical demonstration: build it from original fixtures, then render it.

This exists to prove the toolchain end to end — fixture generation, asset
import with provenance, validation, FFmpeg composition, ffprobe verification. It
is explicitly not a channel intro or a creative pilot, and the closing card says
so on screen.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from polymation.config.channel import ChannelConfig
from polymation.config.paths import Workspace
from polymation.errors import ProjectExistsError
from polymation.media.importer import import_asset
from polymation.projects.store import ProjectBundle, ProjectStore
from polymation.rendering.fixtures import (
    FontResolution,
    generate_closing_card,
    generate_test_pattern,
    generate_title_card,
    generate_tone,
    resolve_font,
)
from polymation.schemas.assets import AssetCategory
from polymation.schemas.project import (
    ContentFormat,
    ContentPillar,
    PrimarySubject,
    ProductionStage,
)
from polymation.schemas.rights import RightsBasis
from polymation.schemas.script import NarrationScene, NarrationStatus
from polymation.schemas.storyboard import FitMode, StoryboardScene, VisualShot

#: Fixed identifier, so `demo create` and `demo render` agree without arguments.
DEMO_PROJECT_ID = "demo-technical-fixture"
DEMO_TITLE = "Polymation Technical Demonstration"
DEMO_SLUG = "polymation-technical-demonstration"

#: Three scenes, five seconds each: a fifteen-second demonstration.
DEMO_SCENE_DURATION_SECONDS = 5.0
DEMO_SCENE_COUNT = 3
DEMO_TOTAL_DURATION_SECONDS = DEMO_SCENE_DURATION_SECONDS * DEMO_SCENE_COUNT

#: Narration text is placeholder technical copy, kept as draft. It is never
#: presented as approved editorial writing.
DEMO_NARRATION: tuple[tuple[str, str, str], ...] = (
    (
        "scene-01",
        "Title card. Deep indigo field, parchment wordmark, amber rule and tagline.",
        "Polymation. Where pixels meet animation.",
    ),
    (
        "scene-02",
        "Original 4:3 geometric test pattern, pillarboxed into the 16:9 frame without distortion.",
        "This is a four-by-three test pattern fitted into a sixteen-by-nine frame.",
    ),
    (
        "scene-03",
        "Closing graphic. Vermilion accent, simple typography.",
        "This is a technical fixture, not a finished video.",
    ),
)


@dataclass(frozen=True)
class DemoCreationResult:
    """What ``demo create`` produced."""

    bundle: ProjectBundle
    fixture_paths: tuple[Path, ...]
    font_resolutions: tuple[FontResolution, ...]
    recreated: bool

    @property
    def used_fallback_fonts(self) -> bool:
        """Whether any text was drawn with a substitute rather than a brand font."""
        return any(resolution.is_fallback for resolution in self.font_resolutions)


def generate_demo_fixtures(
    workspace: Workspace, channel: ChannelConfig
) -> tuple[tuple[Path, ...], tuple[FontResolution, ...]]:
    """Generate the four original fixture files and report the fonts used."""
    fixtures_dir = workspace.fixtures_dir / "demo"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    proposed = channel.brand.typography.proposed_fonts
    primary_family = next(
        (font.family for font in proposed if font.role == "primary"),
        proposed[0].family if proposed else "sans-serif",
    )
    accent_family = next(
        (font.family for font in proposed if font.role == "secondary"),
        primary_family,
    )

    primary_font = resolve_font(primary_family)
    accent_font = resolve_font(accent_family)

    title_card = generate_title_card(
        fixtures_dir / "scene-01-title-card.png",
        channel.brand,
        primary_font=primary_font,
        accent_font=accent_font,
    )
    test_pattern = generate_test_pattern(
        fixtures_dir / "scene-02-test-pattern-4x3.png", channel.brand
    )
    closing_card = generate_closing_card(
        fixtures_dir / "scene-03-closing-card.png",
        channel.brand,
        primary_font=primary_font,
    )
    tone = generate_tone(
        fixtures_dir / "demo-tone.wav",
        duration_seconds=DEMO_TOTAL_DURATION_SECONDS,
    )

    return (title_card, test_pattern, closing_card, tone), (primary_font, accent_font)


def create_demo_project(
    workspace: Workspace,
    channel: ChannelConfig,
    *,
    recreate: bool = False,
) -> DemoCreationResult:
    """Create (or recreate) the demonstration project from freshly built fixtures.

    Raises:
        ProjectExistsError: if the demo project already exists and ``recreate``
            is false.
    """
    store = ProjectStore(workspace)
    project_root = workspace.project_dir(DEMO_PROJECT_ID)
    recreated = False

    if project_root.exists():
        if not recreate:
            raise ProjectExistsError(DEMO_PROJECT_ID)
        _remove_demo_project(workspace)
        recreated = True

    fixtures, fonts = generate_demo_fixtures(workspace, channel)

    try:
        bundle = _build_demo_project(store, channel, fixtures)
    except BaseException:
        # Never leave a half-built demo project behind: a partial project would
        # make the next `demo create` fail with a misleading "already exists".
        _remove_demo_project(workspace)
        raise

    return DemoCreationResult(
        bundle=bundle,
        fixture_paths=fixtures,
        font_resolutions=fonts,
        recreated=recreated,
    )


def _build_demo_project(
    store: ProjectStore, channel: ChannelConfig, fixtures: tuple[Path, ...]
) -> ProjectBundle:
    """Create the demo project and populate it from already-generated fixtures."""
    title_card, test_pattern, closing_card, tone = fixtures

    bundle = store.create(
        title=DEMO_TITLE,
        project_id=DEMO_PROJECT_ID,
        channel_id=channel.channel_id,
        content_pillar=ContentPillar.DESIGN_CRAFT_BREAKDOWN,
        primary_subject=PrimarySubject.INDUSTRY_HISTORY,
        content_format=ContentFormat.STANDARD,
        target_duration_seconds=120,
        slug=DEMO_SLUG,
        language=channel.brand.language,
    )

    image_sources = (
        (title_card, AssetCategory.GRAPHICS, "Programmatically generated title card"),
        (test_pattern, AssetCategory.GRAPHICS, "Programmatically generated 4:3 test pattern"),
        (closing_card, AssetCategory.GRAPHICS, "Programmatically generated closing card"),
    )

    image_asset_ids: list[str] = []
    for source, category, description in image_sources:
        result = import_asset(
            bundle,
            source,
            category=category,
            original_source=f"polymation.rendering.fixtures ({source.name})",
            rights_basis=RightsBasis.ORIGINAL_WORK,
            rights_holder="Polymation Studio",
            intended_editorial_use=f"{description} for the M0.1 technical demonstration.",
            ai_generated=False,
        )
        image_asset_ids.append(result.asset.asset_id)

    import_asset(
        bundle,
        tone,
        category=AssetCategory.MUSIC,
        original_source="polymation.rendering.fixtures (synthesised sine tone)",
        rights_basis=RightsBasis.ORIGINAL_WORK,
        rights_holder="Polymation Studio",
        intended_editorial_use=(
            "Original synthetic audio test signal for the M0.1 technical demonstration."
        ),
        ai_generated=False,
    )

    bundle.script.scenes = [
        NarrationScene(
            scene_id=scene_id,
            order=index,
            narration=narration,
            status=NarrationStatus.DRAFT,
            estimated_duration_seconds=DEMO_SCENE_DURATION_SECONDS,
            notes=note,
        )
        for index, (scene_id, note, narration) in enumerate(DEMO_NARRATION)
    ]
    bundle.script.editorial_notes = [
        "Placeholder technical copy. Not reviewed, not approved, not for publication."
    ]
    bundle.script.touch()

    bundle.storyboard.scenes = [
        StoryboardScene(
            scene_id=scene_id,
            order=index,
            shots=[
                VisualShot(
                    shot_id=f"shot-{index + 1:02d}",
                    scene_id=scene_id,
                    asset_id=image_asset_ids[index],
                    description=note,
                    duration_seconds=DEMO_SCENE_DURATION_SECONDS,
                    # CONTAIN keeps the 4:3 test pattern undistorted in scene 2,
                    # and is harmless for the already-16:9 cards.
                    fit_mode=FitMode.CONTAIN,
                )
            ],
        )
        for index, (scene_id, note, _) in enumerate(DEMO_NARRATION)
    ]
    bundle.storyboard.touch()

    bundle.manifest.production_stage = ProductionStage.STORYBOARD
    bundle.save()

    return bundle


def _remove_demo_project(workspace: Workspace) -> None:
    """Delete the demo project tree.

    Scoped deliberately: only the fixed demo identifier can be removed this way,
    so no operator project can be destroyed by a `--recreate` flag.
    """
    target = workspace.project_dir(DEMO_PROJECT_ID)
    expected = (workspace.projects_dir / DEMO_PROJECT_ID).resolve()

    if target.resolve() != expected:  # pragma: no cover - defensive
        raise ProjectExistsError(DEMO_PROJECT_ID)

    if target.is_dir():
        shutil.rmtree(target)
