# fixtures/

Generated, **original** fixture media. Everything here is produced from code in
[`src/polymation/rendering/fixtures.py`](../src/polymation/rendering/fixtures.py)
— geometric primitives drawn with Pillow, text set in locally installed fonts,
and a sine tone synthesised with `math` and the standard library's `wave`
module.

## Contents are not committed

The generated files are git-ignored (only this README and `.gitkeep` are
tracked). Regenerate them at any time:

```bash
polymation demo create --recreate
```

Generation is deterministic: identical inputs produce byte-identical output, so
asset checksums are stable across runs.

`polymation demo create` writes to `fixtures/demo/`:

| File | What it is |
|---|---|
| `scene-01-title-card.png` | 1920×1080 title card — indigo field, parchment wordmark, amber rule and tagline |
| `scene-02-test-pattern-4x3.png` | 640×480 (4:3) geometric test pattern: checkerboard, concentric squares, crosshair, corner brackets, palette bars |
| `scene-03-closing-card.png` | 1920×1080 closing graphic with a vermilion accent |
| `demo-tone.wav` | 15 s quiet three-note sine tone, 48 kHz 16-bit stereo, with fades |

They are then imported into the demonstration project through the normal asset
pipeline, so they get checksums, metadata and (unreviewed) rights records like
any other media.

## Rule for this directory

**No third-party material, ever.** No game footage or screenshots, no anime
footage or stills, no commercial music, no downloaded artwork or fonts, no stock
media. If a demonstration needs a new asset, write code that generates it. See
[`docs/MEDIA_RIGHTS.md`](../docs/MEDIA_RIGHTS.md).

Note that the fixtures carry `basis=original_work` but `review_status=unreviewed`:
we generated them, and a human still has not reviewed them. Nothing in this
project approves its own output.
