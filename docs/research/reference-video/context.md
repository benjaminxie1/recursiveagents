# Video context package: `gaussian_refernece(1).mp4`

A Claude-readable copy of the reference demo video: frames sampled at 2 fps, a timestamped transcript, and an index tying them together. The original video was not modified.

> **In the repo:** this file, `transcript.md`, `transcript.json`, `frames.csv`, and the 8 key frames next to it are committed. The full 630-frame set (9.7 MB of `frames/*.webp`) stays local in `video-context/`, which is not committed. Rebuild it with the ffmpeg settings under Encoding below.

## Source

| | |
|---|---|
| File | `gaussian_refernece(1).mp4` (Google Drive id `1FDmyZMTsxKxI7xDO2deZ6bSuQ_yKolCw`), 151,441,351 bytes |
| Duration | 314.79 s (5 min 14.8 s) |
| Video | H.264, 1920×1080, 30 fps |
| Audio | AAC, with speech (two presenters) |
| Content | Hackathon demo of "Phylax" (spelled on screen; the speakers say "Filax"): a construction-inspection product built on a Gaussian-splat 3D scan, VLM camera sweeps, the OpenAI Decisions API, California codes and standards, and a Hermes bot over MCP |

## Contents

| Path | What it is |
|---|---|
| `frames/NNNN_tMMmSS.Ss.webp` | 630 frames, 720×405 WebP. `NNNN` is the frame index and `tMMmSS.Ss` is the source timestamp |
| `frames.csv` | index, t_seconds, timestamp, file, bytes, one row per frame |
| `transcript.md` | readable transcript; each segment lists the frame range it covers |
| `transcript.json` | the same segments with start/end seconds, plus `no_speech_prob` and `avg_logprob` (ASR confidence) |

## Sampling and timestamps

- **Rate:** 2 frames per second, one every 0.5 s, via ffmpeg `fps=2`.
- **Timestamps:** frame `i` shows the source at `t = i × 0.5 s`. The first frame (`0000`) is t = 0.0 s and the last (`0629`) is t = 314.5 s. ffmpeg's `fps` filter takes the source frame nearest each 0.5 s tick, so every timestamp is exact to within ±1/60 s.
- **Order:** sorting the filenames lexically gives chronological order, because the index is zero-padded.

## Encoding

1. Decoded once to lossless PNG masters at 1440 px wide (Lanczos).
2. Each frame was resized to 720 px wide (Lanczos), given a light unsharp mask (radius 1.0, 60%, threshold 2) so small UI text survives, and saved as WebP with `method=6`.
3. Quality was chosen by measuring the total size against the 10 MB budget:

| width | quality | total frames | result |
|---|---|---|---|
| 720 | 30 | 11.03 MB | over budget |
| 720 | 25 | 10.04 MB | over budget (no room for text files) |
| **720** | **23** | **9.67 MB** | **chosen** |
| 720 | 20 | 9.10 MB | fits, but noticeably blockier |
| 640 | 30 | 9.25 MB | fits, but the sidebar text blurs |

Readable text wins over cleaner gradients. At 720 px and q23, the sidebar labels ("Pump", "Mechanical / Hydraulic Distribution", "73%" evidence verified) stay legible. At 640 px and q30 they don't, even though that file has fewer compression artifacts.

## Reading or rebuilding the video in order

- **To read it as a story:** go through `frames.csv` in order, and line up `transcript.md` segments using their frame ranges. Speech at second `t` goes with frame `round(2t)`.
- **To rebuild a 2 fps preview video:**
  ```bash
  ffmpeg -framerate 2 -pattern_type glob -i 'frames/*.webp' -c:v libx264 -pix_fmt yuv420p preview.mp4
  ```
  To add the speech back, mux in the original audio track. The audio isn't in this package; take it from the source file.
- **To look at one moment:** frame index = `floor(t × 2)`. For example, 2:32 is t = 152 s, which is frame `0304`.

## What compression and sampling lost

- **Motion under 0.5 s:** cursor paths, quick clicks, hover states, the first frames of transitions, and the smoothness of camera fly-throughs in the 3D view. A UI state shown for less than 0.5 s may be missing entirely.
- **Fine detail:** the frames are 37.5% of source width. Text smaller than about 10 px in the 1080p source (the status bar, tiny captions, dense list sublabels, the webcam overlay) may not be readable. Fine splat detail is softened.
- **Lossy artifacts:** WebP q23 adds blocking and ringing on gradients and splat textures. 4:2:0 chroma subsampling blurs thin colored lines and small colored pins. Colors shift slightly.
- **Sharpening:** the unsharp mask slightly changes edges compared with the source.
- **Audio:** only speech survives, as text. Music, tone of voice and other sound are gone. Speakers aren't separated automatically: Kaushik speaks from 0:00 to about 3:10 and Charlie from about 3:11 to 4:32 (from the self-introductions and the hand-off at 3:06), and both speak in the closing.
- **ASR errors are possible:** the product name is transcribed "Filax" or "filex" but reads "Phylax" on screen. Proper nouns ("Hermes", "grounding dino", "modal") and numbers should be checked against the frames.
