# ASMR cutting studio

Type an object and a number of cuts, and get a vertical (9:16) ASMR cutting Short made in Blender. A knife slices the object, a counter ticks up with every chop, each hit has its own sound, and at the end the slices fall onto the board. No stock footage, no editing: one command goes from `"Papaya", 50` to a finished MP4.

Pure Python standard library plus Blender and FFmpeg. Nothing to pip install.

Ek object. Kitne cuts? Object ka naam aur cuts ki sankhya do, Blender ek vertical (9:16) ASMR cutting video banata hai:
knife strokes, har contact par counter +1 aur chop sound, aakhir mein slices board par girte hain.

## Setup check (ek baar)

Needs Blender (4.2+), `ffmpeg.exe` and `ffprobe.exe`. Python 3.10+ (stdlib only, no pip installs).

```
python studio.py check
```

Prints the path + version of all three tools and renders `runs/_setup_check/preview.png`.
If a tool isn't found, put its full path in `config.json` → `tools` (Explorer: Shift + right-click → Copy as path).

## Video banao

1. `config.json` mein do values likho:
   ```json
   "object": "Papaya",
   "cuts": 50,
   ```
2. Preview (new folder in `runs/` every time, old videos are never touched):
   ```
   python studio.py preview
   ```
   Saves `scene.blend` (editable), `impacts.json` (impact frame list + direction of every cut) and
   `previews/opening.png`, `previews/middle_cut.png`, `previews/fall.png`.
3. Preview pasand aaye toh full render + sound + MP4:
   ```
   python studio.py render            # latest run, or --run runs/<folder>
   ```
   Renders numbered PNG frames (re-running resumes: valid frames are kept, missing/corrupt ones re-rendered),
   writes `soundtrack.wav` (48 kHz stereo, hit at `(impact frame - 1) / fps`; soft-attack rounded hits, no clicks,
   then a small room reverb + gentle compression via FFmpeg), exports H.264 + yuv420p + AAC MP4,
   then checks size, duration, frame count and audio with FFprobe and decodes the whole MP4 for errors.

One-shot (preview + render): `python studio.py make --object "Soap bar" --cuts 12`.
`--object`, `--cuts`, `--watermark`, `--samples`, `--engine cycles`, `--seed` override `config.json` for one run.

**Har baar alag (yede-vakde) cuts:** every run picks a new overall cut angle (up to ~26°), a little wobble per cut
(uneven slices that never cross inside the object), an angled lengthwise split, a slightly different camera and fall.
The knife always lies in each cut's plane. The run's `seed` is saved in its `config.json`; `--seed <n>` rebuilds it
exactly, `"seed": null` in `config.json` = new look every time.

## Timing (24 fps)

First impact frame 29, then one every 6 frames, slices fall 20 frames after the last cut, video ends 97 frames after it.
Papaya × 50: impacts 29 … 323, fall 343, end 420 = 17.5 s. Soap × 12: last impact 95.
Knife, counter and WAV all come from the same impact list, so they can't drift apart.

## Objects

Presets (name match, Hindi names too — papita, pyaz, aloo, tarbooz, nariyal...):
papaya, kiwi, soap, carrot, cucumber, candle, watermelon, mango, apple, orange, lemon, tomato, onion, beetroot,
dragon fruit, avocado, peach, strawberry, coconut, pineapple, potato, cake, cheese, bread, chocolate,
kinetic sand, funnel. Any other name gets a generic egg shape with colours derived from the name.

Textures: every skin has micro-grain plus its own pattern (mottled specks, fuzz, stripes, soap marble, seeds,
pineapple diamonds, bread crust cracks, glossy coat, sand grain, citrus pores). Cut faces get fibres and a wet sheen
(juicy fruit), onion/beetroot rings, cake layers, cheese holes or bread crumb.

- **split** (papaya, watermelon, mango): cut 1 chops lengthwise down the middle, then N-1 horizontal cuts across
  both halves → 2N half-slices (papaya 50 → 100).
- **parallel** (everything else): N horizontal cuts → N+1 slices (soap 12 → 13).

To add an object, add an entry to `PRESETS` in `plan.py` (shape, size, skin, cut surface, sound).

## Run folder

| File | What |
|---|---|
| `scene.blend` | editable Blender scene (open it, scrub the timeline around an impact frame) |
| `impacts.json` | impact frames, times, cut directions, landing frames |
| `previews/` | opening, middle cut, fall |
| `frames/` | numbered PNGs (render resume) |
| `soundtrack.wav` | separate audio |
| `<object>_<N>cuts.mp4` | final video to share |
| `blender.log`, `result.json` | logs and the FFprobe check results |

## Troubleshooting

- **Render stopped midway:** run `python studio.py render` again — it keeps valid frames and renders the rest.
  If you changed the scene/lighting, make a new preview so two versions don't mix.
- **Slow / GPU error:** lower `render.samples` in `config.json`, or `--engine cycles` (CPU, denoised).
- **Slices:** the fall is keyframed (no physics bake), so slices never blast or pass through the board.
- **Silent MP4:** play `soundtrack.wav`; `result.json` shows the AAC stream FFprobe found.

## Shorts dashboard

The *ASMR Cutting Studio* page in my [PODSYU](https://github.com/avvi-mee/podsyu) Shorts dashboard runs `studio.py make`
and adds the finished MP4 to Generated Shorts for review and YouTube publishing.
Set `ASMR_CUTTING_DIR` in that app's `.env` if this folder moves.

Self-checks: `python test_studio.py`
