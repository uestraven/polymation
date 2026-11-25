"""Building the audio mix: narration, music, source audio and effects.

**Ducking is gain automation, not a dynamics processor.** The narration
positions are known exactly — they are integer sample spans on the timeline —
so the attenuation applied to music is computed from those positions and
expressed as a piecewise ``volume`` envelope. A sidechain compressor would key
off the audio signal instead, which makes the result depend on the narration's
waveform, vary with level, and resist assertion in a test. Automation is
reproducible and directly verifiable: a test can compute where music should be
quiet and check that it is. See docs/DECISIONS.md D-017.

All timing enters the filter graph as numbers derived from integer sample
positions. No path and no operator text appears in the filter string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from polymation.errors import RenderError
from polymation.rendering.profiles import RenderProfile
from polymation.schemas.assets import MediaAsset
from polymation.schemas.timeline import (
    AudioMix,
    AudioRole,
    AudioSegment,
    CompositionTimeline,
)

#: Roles mixed into the programme, in a fixed order so the graph is stable.
MIX_ROLE_ORDER: tuple[AudioRole, ...] = (
    AudioRole.NARRATION,
    AudioRole.MUSIC,
    AudioRole.SOURCE,
    AudioRole.EFFECT,
)


@dataclass(frozen=True)
class DuckWindow:
    """A span during which music is attenuated, in samples."""

    start_sample: int
    end_sample: int
    attack_samples: int
    release_samples: int

    def seconds(self, sample_rate: int) -> tuple[float, float]:
        """Start and end in seconds."""
        return self.start_sample / sample_rate, self.end_sample / sample_rate


@dataclass
class AudioPlan:
    """Inputs, filter chains and the label of the finished mix."""

    inputs: list[list[str]] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)
    mix_label: str = "aout"
    #: Per-role bus labels, for exporting stems.
    stem_labels: dict[AudioRole, str] = field(default_factory=dict)
    duck_windows: list[DuckWindow] = field(default_factory=list)

    @property
    def is_silent(self) -> bool:
        """Whether the plan produces silence (no segments were placed)."""
        return not self.stem_labels


def compute_duck_windows(mix: AudioMix, *, total_samples: int) -> list[DuckWindow]:
    """Windows during which music should be attenuated.

    Derived from the narration segments plus the configured lead-in and
    lead-out, then merged where they touch, so overlapping lead times do not
    produce overlapping envelopes that would double-attenuate.
    """
    if not mix.ducking.enabled:
        return []

    narration = mix.segments_for(AudioRole.NARRATION)
    if not narration:
        return []

    ducking = mix.ducking
    raw: list[tuple[int, int]] = []
    for segment in narration:
        start = max(0, segment.span.start_sample - ducking.lead_in_samples)
        end = segment.span.end_sample + ducking.lead_out_samples
        if total_samples:
            end = min(end, total_samples)
        if end > start:
            raw.append((start, end))

    raw.sort()
    merged: list[tuple[int, int]] = []
    for start, end in raw:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return [
        DuckWindow(
            start_sample=start,
            end_sample=end,
            attack_samples=ducking.attack_samples,
            release_samples=ducking.release_samples,
        )
        for start, end in merged
    ]


def _duck_volume_expression(
    windows: list[DuckWindow], attenuation_db: float, sample_rate: int
) -> str:
    """A ``volume`` expression implementing the duck envelope.

    Built as a product of per-window factors. Each factor ramps linearly from 1
    down to the attenuation gain over the attack, holds, then ramps back over
    the release — so there is no click at a window edge.

    Commas are escaped for the filter-argument parser; every value is numeric.
    """
    gain = 10 ** (attenuation_db / 20)
    terms: list[str] = []

    for window in windows:
        start, end = window.start_sample / sample_rate, window.end_sample / sample_rate
        attack = max(window.attack_samples / sample_rate, 1e-6)
        release = max(window.release_samples / sample_rate, 1e-6)

        attack_end = start + attack
        release_start = max(attack_end, end - release)
        release_end = release_start + release

        # 1 before the window, ramp down, hold at `gain`, ramp up, 1 after.
        terms.append(
            f"(if(lt(t\\,{start:.6f})\\,1\\,"
            f"if(lt(t\\,{attack_end:.6f})\\,"
            f"1-(1-{gain:.6f})*(t-{start:.6f})/{attack:.6f}\\,"
            f"if(lt(t\\,{release_start:.6f})\\,{gain:.6f}\\,"
            f"if(lt(t\\,{release_end:.6f})\\,"
            f"{gain:.6f}+(1-{gain:.6f})*(t-{release_start:.6f})/{release:.6f}\\,1)))))"
        )

    return "*".join(terms) if terms else "1"


def _segment_chain(
    segment: AudioSegment,
    profile: RenderProfile,
    *,
    input_index: int,
    output_label: str,
) -> str:
    """Filters placing one audio segment at its timeline position."""
    sample_rate = profile.audio_sample_rate
    filters: list[str] = [
        # Normalise rate and channel layout before anything else, so a mono
        # 44.1 kHz source and a stereo 48 kHz source mix identically.
        f"aresample={sample_rate}",
        f"aformat=sample_fmts=fltp:sample_rates={sample_rate}:channel_layouts=stereo",
        # Trim to exactly the requested sample count.
        f"atrim=start_sample={segment.source_start_sample}"
        f":end_sample={segment.source_start_sample + segment.span.duration_samples}",
        "asetpts=N/SR/TB",
    ]

    if segment.allow_silence_padding:
        # Guarantees the segment reaches its full length even if the source
        # runs out. Only ever reached when explicitly permitted.
        filters.append(f"apad=whole_len={segment.span.duration_samples}")

    if segment.fade_in_samples:
        filters.append(f"afade=t=in:st=0:d={segment.fade_in_samples / sample_rate:.6f}")
    if segment.fade_out_samples:
        start = (segment.span.duration_samples - segment.fade_out_samples) / sample_rate
        filters.append(f"afade=t=out:st={start:.6f}:d={segment.fade_out_samples / sample_rate:.6f}")

    if segment.gain_db != 0.0:
        filters.append(f"volume={segment.gain_db:.3f}dB")

    if segment.span.start_sample:
        filters.append(f"adelay=delays={segment.span.start_sample}S:all=1")

    chain = ",".join(filters)
    return f"[{input_index}:a]{chain}[{output_label}]"


def build_audio_plan(
    timeline: CompositionTimeline,
    profile: RenderProfile,
    *,
    asset_paths: dict[str, Path],
    assets: dict[str, MediaAsset],
    first_input_index: int,
) -> AudioPlan:
    """Build the inputs and filter chains for the complete audio mix.

    Args:
        timeline: supplies segments, ducking and total length.
        profile: supplies the output sample rate and channel count.
        asset_paths: asset id to on-disk path.
        assets: asset id to record, for validating types.
        first_input_index: index the first audio input will occupy, so audio
            inputs can follow video inputs in one invocation.

    Raises:
        RenderError: if a segment's asset is unavailable.
    """
    plan = AudioPlan()
    mix = timeline.audio
    sample_rate = profile.audio_sample_rate
    total_samples = timeline.total_samples

    if not mix.segments:
        return plan

    next_index = first_input_index
    role_labels: dict[AudioRole, list[str]] = {role: [] for role in MIX_ROLE_ORDER}

    for role in MIX_ROLE_ORDER:
        for position, segment in enumerate(mix.segments_for(role)):
            path = asset_paths.get(segment.asset_id)
            if path is None or not path.is_file():
                raise RenderError(
                    f"audio segment {segment.segment_id!r} needs asset "
                    f"{segment.asset_id!r}, which is not available on disk"
                )
            if segment.asset_id not in assets:
                raise RenderError(
                    f"audio segment {segment.segment_id!r} references unknown asset "
                    f"{segment.asset_id!r}"
                )

            plan.inputs.append(["-i", str(path)])
            label = f"a_{role.value}_{position}"
            plan.chains.append(
                _segment_chain(segment, profile, input_index=next_index, output_label=label)
            )
            role_labels[role].append(label)
            next_index += 1

    plan.duck_windows = compute_duck_windows(mix, total_samples=total_samples)

    # Collapse each role to a single bus, then apply role gain and ducking.
    bus_labels: list[str] = []
    for role in MIX_ROLE_ORDER:
        labels = role_labels[role]
        if not labels:
            continue

        bus = f"bus_{role.value}"
        if len(labels) == 1:
            plan.chains.append(f"[{labels[0]}]anull[{bus}]")
        else:
            joined = "".join(f"[{label}]" for label in labels)
            # `amix` would attenuate by input count; `amerge`/`asum` semantics
            # differ. amix with normalize=0 sums without scaling, which is what
            # placing separate segments on one bus means.
            plan.chains.append(
                f"{joined}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[{bus}]"
            )

        processed = bus
        role_gain = mix.role_gain_db.get(role, 0.0)
        if role_gain:
            gained = f"{bus}_g"
            plan.chains.append(f"[{processed}]volume={role_gain:.3f}dB[{gained}]")
            processed = gained

        if role is AudioRole.MUSIC and plan.duck_windows:
            ducked = f"{bus}_duck"
            expression = _duck_volume_expression(
                plan.duck_windows, mix.ducking.attenuation_db, sample_rate
            )
            plan.chains.append(f"[{processed}]volume=volume='{expression}':eval=frame[{ducked}]")
            processed = ducked

        plan.stem_labels[role] = processed
        bus_labels.append(processed)

    if not bus_labels:  # pragma: no cover - guarded by the early return
        return plan

    if len(bus_labels) == 1:
        summed = "mix_sum"
        plan.chains.append(f"[{bus_labels[0]}]anull[{summed}]")
    else:
        joined = "".join(f"[{label}]" for label in bus_labels)
        summed = "mix_sum"
        plan.chains.append(
            f"{joined}amix=inputs={len(bus_labels)}:normalize=0:dropout_transition=0[{summed}]"
        )

    # Pad or trim the mix to exactly the video length, so the muxer never has to
    # guess, and a short music bed does not shorten the video.
    padded = "mix_padded"
    plan.chains.append(
        f"[{summed}]apad=whole_len={total_samples},"
        f"atrim=end_sample={total_samples},asetpts=N/SR/TB[{padded}]"
    )

    # A true-peak limiter before encoding. Summed buses can exceed full scale
    # even when each is below it; clipping there would be audible and
    # irreversible.
    limit = 10 ** (mix.loudness.true_peak_dbtp / 20)
    plan.chains.append(
        f"[{padded}]alimiter=limit={limit:.6f}:level=disabled:attack=5:release=50[{plan.mix_label}]"
    )

    return plan
