# Council verdict: which concept to build (Oct 17 2026, 10:40)

This is a proposal for the team to accept or reject at kickoff, not a decision yet.
Twelve concepts are in `unexpected.md`, `recursion.md` and `transform.md`.
Four were shortlisted and put to a four-voice council (Architect, Skeptic, Pragmatist, Critic) that saw only the question and the concept files.

## The four voices

- **Architect:** build Afterimage, agents over a splat of a real scene that subdivide where Jev finds change.
  Three ideation agents converged on this core independently.
- **Skeptic:** build Afterimage, but cut the freehand phone walk-through.
  Use a matte tabletop scene with 4 to 5 taped photo stations, cap the octree at depth 3, and diff photo against photo.
  Every node must make a real choice (render a closer view, ask for a re-shoot of one station, or split), or the recursion is just a for-loop.
- **Pragmatist:** build the Viewpoint Court instead.
  It needs no live registration, fits the rate limits, and a judge can type the claim live.
- **Critic:** build the Viewpoint Court.
  Afterimage's fatal flaw is registering a handheld video in afternoon light within 60 seconds.
  The Court's fatal flaw is generic testimony that makes the trial tree look scripted.
  Ask organizers where demos happen before committing to anything tied to our table.

## Verdict

- **Consensus:** the splat must be the agents' memory, not a backdrop; Jev should rule on a written evidence record, because it takes JSON and not images; the live moment must come from a judge, to prove it is not canned.
- **Strongest dissent:** the Court is safer, because Afterimage depends on aligning live images with the splat.
- **Recommendation:** build **Afterimage, in the Skeptic's form**, and use the Court's rule that Jev decides on structured evidence.
  - The scene is a portable tabletop diorama of matte objects, so it works at our table or on a stage.
  - 4 to 5 taped photo stations have poses calibrated once at capture, so there is no live registration.
  - Change is found by diffing station photo against station photo; the splat supplies depth to place each change in a 3D cell, novel viewpoints for a closer look, and the edited "after" scene.
  - Each octree agent chooses one action per step: split, render a closer splat view, request a re-shoot of one station, or clear; Jev returns calibrated clear, changed or split probabilities from the evidence JSON (pixel-diff scores, 3D vote counts, how many views agree).
  - Depth is capped at 3; the pitch number is changes found and model calls used, adaptive tree against a uniform grid.
- **Go/no-go at 12:30:** if station diff plus depth projection cannot place a moved object in the right cell, switch to the Viewpoint Court, which reuses the same splat, render bridge, Jev client, recursive tree runtime and viewer.

## Build bottlenecks and checkpoints

| # | Bottleneck | Check by | Fallback |
|---|---|---|---|
| 1 | Splat capture on a phone (the Mac has no CUDA for training) | 11:30 | Scaniverse or Polycam on-device; worst case a public tabletop splat |
| 2 | Jev access: no key yet, signups reportedly paused | 11:30 | OpenRouter `typesafe/jev-1.14-20261014`; then OpenAI Decisions; last resort a Nemotron vote fraction labelled as such |
| 3 | Render bridge returns RGB and depth for a pose on the Mac | 12:15 | Pre-render the station and closer-look poses in a batch |
| 4 | Change localization on the diorama | 12:30 | Switch to the Viewpoint Court |
| 5 | A full run finishes in about 60 seconds within rate limits | 14:30 | Judge tampers before our slot; live part re-runs on a smaller change set; event-log replay |

Before committing, also: ask organizers where demos are judged, and put a card on Crusoe (30 to 600 requests per minute per model).
Nemotron 3.5 Super VL and Nemotron 3 Nano Omni are both in Crusoe's catalog, and Omni passed the smoke test.
