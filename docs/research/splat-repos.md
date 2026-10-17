# GridTwin - public repos to borrow from

Searched 2026-10-17 (about 15 minutes) with `gh api` and the GitHub search API.
Every repo below was verified with `gh api repos/OWNER/NAME`; stars, license and last push are from that call.
"License" is the SPDX id from the API, corrected from the LICENSE file where the API said NOASSERTION.

License key:
- MIT, Apache-2.0, BSD-2-Clause: code may be copied with credit in our README.
- Inria "Gaussian-Splatting License" (research, non-commercial), CC BY-NC, CC BY-NC-SA, LGPL, or no license at all: ideas only, do not copy code.
- Many Apache or MIT 3DGS repos vendor Inria's `diff-gaussian-rasterization` as a submodule, which stays non-commercial; copy only their own files.

Pieces we map to: registration, depth index, viewer pins, equipment segmentation, change detection.

No public repo was found for the SPIE paper "Rapid 3D reconstruction of key power plant equipment using SAM" (DOI 10.1117/12.3114168).
Searches for "power plant segment anything", "power plant gaussian" and "substation gaussian splatting" returned nothing relevant.

## References Will pointed at

- SPIE paper "Rapid 3D reconstruction of key power plant equipment using SAM", Proc. SPIE vol. 14164, paper 141640K, DOI [10.1117/12.3114168](https://doi.org/10.1117/12.3114168).
  The SPIE page sits behind a CAPTCHA and its abstract is not indexed elsewhere, so as of 12:05 nobody on the team has read the method; cite it by title only until someone does.
  Its title is the same idea as section 1 below: segment equipment with SAM, then reconstruct it in 3D.
- iTek Media, ["Gaussian Splats - Power Plant"](https://itek-media.com/gs/pp_spatial/), a streaming splat tour of an indoor power-plant hall (pipe racks, cable trays, cabinets, extinguishers), made with XGRIDS LCC Studio and published on LCC Cloud ([direct viewer](https://lcc-viewer.xgrids.com/pub/dbaykk-pp_spatial)).
  It shows what our tabletop mock stands in for, and that full plant interiors are already captured as splats today, which backs the "nightly recapture" story in `docs/JUDGE_QA.md`.

## 1. Equipment segmentation (SAM on splats)

### risenW/segment-3gs - the closest match to what we want
- URL: https://github.com/risenW/segment-3gs
- Stars 51, MIT, last push 2026-08-12.
- What: a PlayCanvas splat viewer plus a local Python SAM server (mock, mobile_sam, gated SAM3 tiers) that lifts 2D masks onto the splats and merges views by per-Gaussian voting.
- Borrow: `src/segmentation/lift.ts` and `docs/ARCHITECTURE.md`.
  The lift culls candidates to the frustum slab under the mask bbox with a spatial grid, anchors depth on the masked splats themselves (not on a collision mesh), keeps splats within a depth band around the anchor, and merges multi-view results with a per-Gaussian vote and score.
  This is the same projection-plus-depth-test our depth index does, already written in TypeScript against a browser splat viewer.
- Maps to: equipment segmentation, depth index.
- License: MIT, copy with credit; SAM3 weights are gated by Meta's own license, mobile_sam is Apache-2.0.

### Jumpat/SegAnyGAussians (SAGA)
- URL: https://github.com/Jumpat/SegAnyGAussians
- Stars 1001, Apache-2.0, last push 2026-08-02.
- What: trains a low-dimensional affinity feature per Gaussian from SAM masks with a mask-scale gate, then segments any object from a click in milliseconds.
- Borrow: the data layout (`sam_masks/`, `mask_scales/`, `features/` next to the scene) and the flow in `prompt_segmenting.ipynb`: click point, read per-Gaussian feature, threshold similarity, export the Gaussian index set.
  The scale gate is how SAGA separates a valve from the pipe it sits on, which is exactly our equipment granularity problem.
- Maps to: equipment segmentation.
- License: Apache-2.0 for its own code; it depends on the Inria rasterizer and needs a CUDA training pass per scene, so for today take the idea, not the pipeline.

### lkeab/gaussian-grouping
- URL: https://github.com/lkeab/gaussian-grouping
- Stars 1063, Apache-2.0, last push 2024-07-04.
- What: adds an "identity encoding" vector to every Gaussian, supervised by SAM masks that were made consistent across views with a video tracker.
- Borrow: the idea of storing an object id per Gaussian as an extra PLY property, so equipment ids travel with the splat file into the viewer, and the trick of running a video object tracker over the capture frames to get view-consistent SAM ids.
- Maps to: equipment segmentation, viewer pins (pin to an object id, not only to a point).
- License: Apache-2.0 for its own code; built on the Inria codebase.

### florinshen/FlashSplat
- URL: https://github.com/florinshen/FlashSplat
- Stars 272, Inria Gaussian-Splatting License (non-commercial), last push 2024-09-13.
- What: lifts 2D masks to 3D Gaussians optimally in one pass by solving a linear program in closed form.
- Borrow (idea only): render each view, accumulate every Gaussian's alpha-weighted contribution to masked versus unmasked pixels, and label each Gaussian by the larger total.
  No training, roughly seconds per object, and it is a cleaner rule than a hard depth band.
- Maps to: equipment segmentation, change detection (the same accumulation works for "changed" masks).
- License: non-commercial, ideas only.

Also verified, lower priority today:
- https://github.com/weijielyu/Gaga - 421 stars, MIT, 2026-06-24, groups inconsistent SAM masks across views with a 3D-aware memory bank.
- https://github.com/minghanqin/LangSplat - 1096 stars, Inria non-commercial, 2025-10-10, text queries ("transformer", "breaker") over a CLIP feature field.
- https://github.com/ShijieZhou-UCLA/feature-3dgs - 690 stars, Inria non-commercial, 2024-10-17, distills SAM or CLIP feature fields into the splat.

## 2. Registration and photo localization against a splat

### cvg/Hierarchical-Localization (hloc)
- URL: https://github.com/cvg/Hierarchical-Localization
- Stars 4243, Apache-2.0, last push 2025-12-10.
- What: image retrieval plus local feature matching plus PnP RANSAC to localize a query photo against an SfM model.
- Borrow: the fallback path for station photos where the AprilTag board is not visible.
  Match the photo to the capture frames with LightGlue (ALIKED or DISK features), lift the matches to 3D through the reference frame's depth, then `pycolmap` absolute pose RANSAC.
  Our splat depth can stand in for the SfM points, so the same PnP step we already run on tag corners works on matched keypoints.
- Maps to: registration.
- License: Apache-2.0 for hloc; SuperPoint and SuperGlue weights are non-commercial, so pick LightGlue with ALIKED or DISK.

### XRIM-Lab/GS-CPR
- URL: https://github.com/XRIM-Lab/GS-CPR
- Stars 156, CC BY-NC-SA 4.0 (inherits DUSt3R and MASt3R), last push 2026-04-11.
- What: refines a coarse camera pose by rendering the splat at that pose, matching the real photo to the rendering with MASt3R, and solving PnP with the rendered depth.
- Borrow (idea only): one render-match-PnP refinement step after our tag PnP.
  It uses the splat as its own depth index, which is the cleanest way to tighten residuals when the board is small in frame.
- Maps to: registration, depth index.
- License: non-commercial share-alike, ideas only.

### atticuszeller/GsplatLoc
- URL: https://github.com/atticuszeller/GsplatLoc
- Stars 96, MIT, last push 2026-10-05.
- What: pose optimization by reprojecting 3D Gaussians with gsplat and minimizing depth and photometric error against an observation.
- Borrow: the gradient-based pose refinement loop on top of `gsplat` (Apache-2.0), which we can run after PnP as a final polish if a GPU is free.
- Maps to: registration.
- License: MIT, copy with credit.

### N0t4R0b0t/photogram
- URL: https://github.com/N0t4R0b0t/photogram
- Stars 5, MIT, last push 2026-07-01.
- What: a working photogrammetry pipeline (video or photos to metric cloud, mesh and splat) that solves scale with printed ArUco markers instead of AI.
- Borrow: `scripts/generate_aruco_sheet.py` for a print-ready board, `backend/workers/pipeline/scale_from_aruco.py` and `tests/test_scale_from_aruco.py` as a reference and test pattern for marker-based scale.
  Small and unvetted, but it is the only verified repo doing printed-marker scale for splats.
- Maps to: registration.
- License: MIT, copy with credit.

Also verified:
- https://github.com/be2rlab/gsplatloc - 153 stars, Apache-2.0, 2026-01-27, distills keypoint descriptors into the splat, coarse PnP then render-based refinement (uses the Inria rasterizer submodule).
- https://github.com/YuanSun-XJTU/iComMa - 135 stars, no license, 2024-04-11, inverts 3DGS with a comparing loss plus a matching loss; ideas only.
- https://github.com/mbortolon97/6dgs - 181 stars, Inria non-commercial, 2024-07-29, single-image pose from a splat with no initial guess; ideas only.
- https://github.com/manudelu/georeferenced_gsplat - 6 stars, no license, 2025-10-30, GPS-EXIF georeferencing and scale for drone splats via COLMAP; ideas only.

## 3. Change detection (splat versus splat, or splat versus new photos)

### Chumsy0725/O-SCD - closest to Afterimage
- URL: https://github.com/Chumsy0725/O-SCD
- Stars 25, no license, last push 2026-06-07 (CVPR 2026).
- What: online scene change detection; each new photo is localized against the reference splat with fast PnP, change cues are fused across views with a self-supervised loss, and the splat is updated only where it changed.
- Borrow (idea only): the loop "PnP the new photo, render the reference splat at that pose, compute per-pixel change, fuse across views".
  It is our Afterimage plan with a published evaluation on the PASLCD dataset (on Hugging Face) that we can cite.
- Maps to: change detection, registration.
- License: none, so all rights reserved, ideas only.
- Note: https://github.com/Taha-Gandhi/O-SCD is an unofficial copy of the same code, not a GitHub fork; use the Chumsy0725 one.

### 520xyxyzq/3DGS-CD
- URL: https://github.com/520xyxyzq/3DGS-CD
- Stars 74, Apache-2.0, last push 2025-02-22.
- What: object-level change detection from two unaligned RGB sets; renders the pre-change splat (nerfstudio splatfacto) at post-change poses, compares, and uses SAM to turn pixel changes into object masks and pose changes.
- Borrow: the dataset layout (`rgb/`, `rgb_new/`, `masks_gt/`, one `transforms.json` holding pre and post poses) as our morning-versus-later format, and `scripts/merge_masks.py` plus `scripts/eval.py` for scoring against hand-drawn change masks.
- Maps to: change detection.
- License: Apache-2.0, copy with credit.

### Chumsy0725/MV3DCD
- URL: https://github.com/Chumsy0725/MV3DCD
- Stars 17, Inria non-commercial (LICENSE.md), last push 2026-02-02.
- What: multi-view pose-agnostic change localization with zero labels; builds feature and SSIM change masks per view (`create_mask.py`) and fuses them into 3D by training change channels (`train_masks.py`).
- Borrow (idea only): combine a feature-difference mask (DINO or similar) with an SSIM mask before voting, which cuts false positives from lighting changes between morning and afternoon.
- Maps to: change detection.

### jan-ackermann/cl-splats
- URL: https://github.com/jan-ackermann/cl-splats
- Stars 65, MIT for original code plus Inria for files derived from 3DGS, last push 2026-06-12 (ICCV 2025).
- What: detects the changed region from new images, bounds it in 3D, and re-optimizes only the Gaussians inside it.
- Borrow: the "bound the changed region in 3D, then update locally" step; our octree cells are that bound.
- Maps to: change detection.

Also verified but code not released:
- https://github.com/BoazKG93/gs-pool - 0 stars, MIT, 2026-10-06; paper idea worth taking now: set the change threshold per scene from the objects that are present in both visits, instead of a fixed constant.
- https://github.com/Chumsy0725/GS-DIFF - 4 stars, CC BY-NC 4.0, 2026-05-11, README and LICENSE only.

## 4. Web viewers with pins, hotspots, measurement and camera poses

### sparkjsdev/spark - our viewer's splat library, read its examples first
- URL: https://github.com/sparkjsdev/spark
- Stars 3750, MIT, last push 2026-10-16.
- What: three.js Gaussian splat renderer with editable, dynamic splats.
- Borrow, all from `examples/`:
  - `raycasting/index.html`: a plain `THREE.Raycaster` against the scene, then the first hit that is a `SplatMesh` gives the world point; this is the click-to-place-pin path.
  - `multiple-viewpoints/index.html`: builds a textured rectangle at each viewpoint's near plane; reuse it to draw each registered station photo as a frustum card next to the splat.
  - `render-cube-depth/index.html`: packs splat depth into RGB through a custom shader; a browser-side depth render if we want the viewer to check pin occlusion.
- Maps to: viewer pins, registration display, depth index.
- License: MIT.

### playcanvas/engine - annotation and picking examples for splats
- URL: https://github.com/playcanvas/engine
- Stars 17076, MIT, last push 2026-10-17.
- What: the PlayCanvas engine; its splat examples include clickable hotspot annotations and splat picking.
- Borrow: `examples/src/examples/gaussian-splatting/annotations.example.mjs`, which uses `Annotation` and `AnnotationManager` from `scripts/esm/annotations.mjs`, and `picking.example.mjs`.
  Copy the UX and data shape (hotspot, label, details panel, camera framing) into our Spark pins rather than switching engines.
- Maps to: viewer pins.
- License: MIT.

### reall3d-com/Reall3dViewer
- URL: https://github.com/reall3d-com/Reall3dViewer
- Stars 516, MIT, last push 2026-09-11.
- What: three.js 3DGS viewer with built-in distance and area measurement, labeling, and per-model `.meta.json` config.
- Borrow: the measurement and marking tools (a finding like "crack 4 cm" can show a measured line) and the per-scene `.meta.json` sidecar pattern for camera start, transform and annotations.
- Maps to: viewer pins.
- License: MIT.

### potree/potree
- URL: https://github.com/potree/potree
- Stars 5645, BSD-2-Clause (API says NOASSERTION; LICENSE file is BSD-style), last push 2026-01-08.
- What: the standard web point-cloud viewer in surveying and plant inspection, with annotations and measurement.
- Borrow: the annotation schema in `src/Annotation.js`: `title`, `description`, `position`, plus `cameraPosition` and `cameraTarget` so clicking a pin flies to the view the inspector saw.
  Adding those two camera fields to our finding pins lets each pin reopen the station photo's pose.
- Maps to: viewer pins, registration.
- License: BSD-2-Clause, copy with credit.

### nerfstudio-project/viser
- URL: https://github.com/nerfstudio-project/viser
- Stars 2822, Apache-2.0, last push 2026-09-25.
- What: Python web 3D viewer; `examples/01_scene/09_gaussian_splats.py` shows splats, and `scene.add_camera_frustum(..., image=...)` draws a camera with its photo.
- Borrow: a 20-line debug script that loads our PLY, the Umeyama-aligned station poses from `scripts/register_viewpoints.py`, and the photos, to eyeball registration before it reaches the Spark viewer.
- Maps to: registration QA, viewer pins.
- License: Apache-2.0.

Also verified:
- https://github.com/playcanvas/supersplat - 10406 stars, MIT, 2026-10-05, editor to crop the Scaniverse PLY to the tabletop and delete floaters before demo.
- https://github.com/playcanvas/supersplat-viewer - 596 stars, MIT, 2026-10-16, self-contained static viewer driven by `settings.json` and a `content` URL parameter.
- https://github.com/playcanvas/splat-transform - 1384 stars, MIT, 2026-10-09, CLI to convert, crop and compress PLY (SOG and others).
- https://github.com/mkkellogg/GaussianSplats3D - 2916 stars, MIT, 2025-10-19; https://github.com/antimatter15/splat - 3074 stars, MIT, 2025-11-16; https://github.com/huggingface/gsplat.js - 1670 stars, MIT, 2026-09-24.

## 5. Industrial, power plant and digital twin

Honest finding: no verified public repo combines splats of power equipment with inspection annotations.
The closest public material is vendor SDKs and BIM overlays.

### xgrids/LCC-Web-SDK
- URL: https://github.com/xgrids/LCC-Web-SDK
- Stars 60, no license (the SDK itself downloads from developer.xgrids.com), last push 2026-09-08.
- What: XGRIDS' web SDK for LCC splats on three.js and Cesium; the iTek Media power-plant tour uses XGRIDS LCC Studio.
- Borrow (idea only): its feature list is a checklist of what a plant-tour product ships: ray detection against splat and mesh, collision, box and plane clipping, point cloud mode.
  Box clipping is a cheap way to "isolate" one piece of equipment in our viewer by cropping to its segment's AABB.
- Maps to: viewer pins, equipment segmentation (display side).

### wallabyway/gaussian-splats-lmv
- URL: https://github.com/wallabyway/gaussian-splats-lmv
- Stars 17, no license, last push 2026-05-19.
- What: decodes XGRIDS LCC splats in the browser and composites them over a Revit BIM model in the Autodesk APS viewer, with no build step.
- Borrow (idea only): splat plus asset model in one frame with one shared transform; our equivalent is drawing each equipment segment's box or label from an asset list over the splat.
- Maps to: viewer pins.

### sjtu-jq/BIM-based-multimodal-reconstruction
- URL: https://github.com/sjtu-jq/BIM-based-multimodal-reconstruction
- Stars 5, LGPL-2.1, last push 2024-09-03.
- What: substation equipment inspection images registered onto a BIM model, with an image decision strategy and image harmonization; the implementation is not released, but the BIM model and virtual plus real multimodal images are linked on Google Drive.
- Borrow: the framing for the pitch (inspection photo, possibly infrared, registered to a 3D equipment model) and the dataset as a realistic substation example.
- Maps to: registration (framing only).

Seen but not recommended (0 to 1 stars, unvetted): RakeshPedada/PowerTwinAI, zybkpro/ZP-PowerSubstation, 0xFEEDC0DE/industrial-digital-twin-mobile.

## Top 5 for the next 4 hours

1. https://github.com/sparkjsdev/spark (MIT) - `examples/raycasting` for click-to-pin and `examples/multiple-viewpoints` for station photo cards; zero new dependencies. Pins, registration display.
2. https://github.com/risenW/segment-3gs (MIT) - `src/segmentation/lift.ts`: frustum-slab cull, splat-anchored depth band, per-Gaussian vote merge; drops into our depth index for SAM equipment masks and for Afterimage voting. Segmentation, depth index, change detection.
3. https://github.com/potree/potree (BSD-2-Clause) - pin schema with `cameraPosition` and `cameraTarget` so each finding flies to the station view. Pins.
4. https://github.com/Chumsy0725/O-SCD (no license, ideas only) - the PnP-render-diff-fuse loop that Afterimage needs, with a dataset to cite. Change detection.
5. https://github.com/nerfstudio-project/viser (Apache-2.0) - `add_camera_frustum(image=...)` plus splats for a quick registration sanity check. Registration QA.

Next tier: 520xyxyzq/3DGS-CD (Apache-2.0) for a change-detection data format and eval, cvg/Hierarchical-Localization (Apache-2.0) for tag-free photo localization, playcanvas/engine annotations example (MIT) for hotspot UX.

Credits needed in our README if code is copied: Spark (MIT), segment-3gs (MIT), Potree (BSD-2-Clause), viser (Apache-2.0), and any other MIT, BSD or Apache repo used.

## Segment Anything: per-asset splat segmentation

Searched 2026-10-17 from 12:30 to 12:45 PDT, starting from https://github.com/liliu-avril/Awesome-Segment-Anything (MIT, 1227 stars, last push 2026-10-12).
We read its README (2026 papers and the open-source project table) and `Paper_List/paper_list_2023.md`, `paper_list_2024.md` and `paper_list_2025.md`, about 2,800 entries in all.
We filtered them for 3D, Gaussian, NeRF, multi-view and point-cloud entries, and separately for power, industrial and solar entries.
Every repo below was checked with `gh api repos/OWNER/NAME`, and its LICENSE file was read where the API said NOASSERTION.
Weight sizes are exact byte counts from the Hugging Face model API or the GitHub contents API.
Nothing was downloaded.
Section 1 above already covers segment-3gs, SAGA, Gaussian Grouping, FlashSplat, Gaga, LangSplat and feature-3dgs, so they are not repeated here.

### What our code already gives us

- `scripts/register_viewpoints.py` writes `assets/splats/<scene>.registration.json` with one view per station photo, keyed by the photo stem, which is the viewpoint id (for example `inv1_cabinet`).
  Each view has `K`, `R`, `t` (OpenCV, `X_cam = R @ X + t`, splat units) and `size` at full photo resolution after EXIF rotation, plus `target`, the splat point at the image centre.
- `assets/photos/viewpoints.yaml` maps each viewpoint id to its asset (`inv1_cabinet: {asset: INV-1, ...}`), so every registered photo already knows which asset it was taken of.
  Naming is therefore free for each station's own asset.
  Text prompts are needed only to find that asset's box in the photo, to name other equipment visible in the same photo, and to discover equipment that has no viewpoint.
- `SplatIndex.render(K, R, t, size)` gives the z-buffer for the occlusion test, `scaled(K, f)` gives the camera for a downscaled photo, and `SplatIndex.lift` maps pixels back to 3D.
- `SplatIndex.from_splat` drops Gaussians below opacity 0.3, so positions in the index are not PLY indices.
  Keep `np.flatnonzero(splat.opacity >= 0.3)` to map back before writing anything the viewer reads.
- `board_to_splat.scale` in the registration file is splat units per metre, so every tolerance below is set in metres.
  The board's z axis, `board_to_splat.R[:, 2]`, is "up" if the board lay flat during capture.
- The team plan (`docs/MEMORY.md`, 12:04) asks for 2 to 3 named viewpoints per demo asset, which is enough for a per-Gaussian vote.
- This Mac is an Apple M4 with 16 GB.
  The system `python3` (3.14.6) already has torch 2.14.1 with MPS available and transformers 5.18.0, which ships `Sam2Model`, `GroundingDinoForObjectDetection`, `Owlv2ForObjectDetection` and `Sam3Model`.
  It lacks torchvision, which the transformers SAM 2 image processor imports at the top of `image_processing_sam2.py`, and it lacks cv2.
  The project `.venv` has numpy and cv2 but no torch.

### Ranked candidates

| # | Candidate | License (LICENSE file) | What we reuse | Weights, login | Apple-silicon fit | Role |
|:-:|---|---|---|---|---|---|
| 1 | SAM 2.1, https://github.com/facebookresearch/sam2 (20014 stars, pushed 2026-05-30), run through transformers `Sam2Model` | Apache-2.0 (plus BSD-3 `LICENSE_cctorch`) | box plus point prompted mask per station photo, using the `Sam2Model` / `Sam2Processor` code on the model card | `facebook/sam2.1-hiera-tiny` 155.9 MB, `-small` 184.3 MB, not gated | pure PyTorch in transformers, no CUDA extension; MPS or CPU; estimate about 1 s per photo on MPS | 2D mask engine (a) |
| 2 | Grounded-SAM-2, https://github.com/IDEA-Research/Grounded-SAM-2 (3780 stars, pushed 2025-11-11), with Grounding DINO, https://github.com/IDEA-Research/GroundingDINO | Apache-2.0 (`LICENSE`, `LICENSE_groundingdino`, `LICENSE_sam2`) | the flow in `grounded_sam2_hf_model_demo.py`: text prompt to boxes with `AutoModelForZeroShotObjectDetection`, best box to SAM 2 as a box prompt; prompts lowercase and ending in "." | `IDEA-Research/grounding-dino-tiny` 689.4 MB, not gated | transformers implementation; run it on CPU to be safe; estimate 2 to 5 s per photo | naming (b) |
| 3 | MV3DIS, https://github.com/zybjn/MV3DIS (7 stars, pushed 2026-10-10) | MIT (Copyright 2026 Yibo Zhao) | `mv3dis_base.py` `get_points_label_seen`: project points, relative depth test `np.isclose(real_depth, captured_depth, rtol=vis_dis)`, per-view weight `1 - depth_difference / tolerance`, mask label lookup; about 45 lines of numpy | none of its own | numpy only for this function; the rest of the repo needs CUDA | the lift and vote (a) |
| 4 | SegmentAnything3D (SAM3D), https://github.com/Pointcept/SegmentAnything3D (1390 stars, pushed 2024-04-21) | MIT (Copyright 2023 Pointcept) | `sam3d.py` `get_pcd` (back-project a SAM mask through depth and pose) and `cal_group` / `cal_2_scenes` (merge mask ids across views by overlap ratio 0.5) | uses SAM ViT-H; swap in SAM 2.1 tiny | merge logic is numpy; its KNN uses CUDA `pointops` | automatic discovery in segment-everything mode (a) |
| 5 | SA3D, https://github.com/Jumpat/SegmentAnythingin3D (1031 stars, pushed 2025-05-19) | Apache-2.0 on `main` (NeRF version); the `sa3d-gs` branch is the Inria non-commercial license | `lib/self_prompting.py` `mask_to_prompt` from `main`: picks spread-out positive points from a projected 3D mask, depth-aware, to prompt SAM in a new view | none of its own | small torch function, CPU is fine | extra views, stretch (a) |
| 6 | MobileSAM, https://github.com/ChaoningZhang/MobileSAM (5893 stars), and EfficientSAM, https://github.com/yformer/EfficientSAM (2498 stars) | Apache-2.0 both | fallback 2D engine; EfficientSAM ships browser-ready `weights/efficient_sam_vitt_encoder.onnx` and `weights/efficient_sam_vitt_decoder.onnx` for onnxruntime-web | in the GitHub repos, no login: `mobile_sam.pt` 40.7 MB; EfficientSAM ONNX encoder 24.8 MB plus decoder 16.6 MB | MobileSAM README: about 3 s per image on a Mac i5 CPU | fallback, or click-to-segment in the browser later |
| 7 | SAGD, https://github.com/XuHu0529/SAGS (156 stars, pushed 2024-05-24) | none, ideas only | idea: project Gaussian centres into each view's SAM mask and vote, which needs no training; then split or drop large Gaussians that straddle the mask boundary | none | its code needs the CUDA rasterizer | boundary clean-up rule (a) |
| 8 | Multi-view Bayesian fusion for industrial scenes, arXiv 2512.06882 (BMVC 2025, Zhu, Chiba, Hashimoto) | no code; the list's code link points to an unrelated Cornell repo | idea: YOLO-World boxes prompt SAM, masks are back-projected onto the point cloud, and each view's vote is weighted by mask size, in-mask point count and boundary clarity | none | not applicable | vote weights (a), closest published method to our setting |

Why this ranking: 1 to 3 together are the whole pipeline with no per-scene training, no CUDA and only permissive licenses.
4 and 5 add discovery and extra views if time remains.
6 is the plan B if the 690 MB Grounding DINO download is refused, and the route to a live in-browser SAM later.
7 and 8 are rules we implement ourselves.

Ruled out for today:
- SAM 3 and SAM 3.1 (https://github.com/facebookresearch/sam3, 12011 stars) would answer (b) in one model with concept prompts like "inverter cabinet".
  Its SAM License allows commercial use, but it is a custom license whose redistribution terms carry over, and it forbids end uses "related to military or warfare purposes, nuclear industries or applications".
  The weights are gated with manual approval on Hugging Face (`facebook/sam3`, 3.44 GB), and the README asks for a CUDA 12.6 GPU.
  Not today, and flag the nuclear clause if GridTwin ever pitches nuclear plants.
- EdgeSAM (https://github.com/chongzhou96/EdgeSAM) and Open-Vocabulary SAM (https://github.com/HarborYuan/ovsam) are under S-Lab License 1.0, non-commercial only.
- LUDVIG (https://github.com/naver/ludvig), learning-free uplifting of 2D features onto Gaussians, is under a NAVER non-commercial license; the idea matches ours, the code cannot ship.
- Ultralytics (https://github.com/ultralytics/ultralytics) wraps SAM, SAM 2, MobileSAM, FastSAM and YOLO-World but is AGPL-3.0; X-AnyLabeling (https://github.com/CVHub520/X-AnyLabeling) is GPL-3.0.
- Open-YOLO 3D (https://github.com/aminebdj/OpenYOLO3D), SAI3D (https://github.com/yd-yin/SAI3D) and samesh (https://github.com/gtangg12/samesh) have no license, so ideas only.
- OV-SAM3D (https://github.com/HanchenTai/OV-SAM3D) is MIT but runs in Docker with CUDA and superpoints.
- lang-segment-anything (https://github.com/luca-medeiros/lang-segment-anything, Apache-2.0) wraps the same Grounding DINO plus SAM 2 path; transformers already gives us that without another package.
- SAM-V (https://github.com/gong208/SAM-V, Apache-2.0, 2026-09) makes multi-view masks consistent with VGGT features, but it needs the VGGT and patched SAM-HQ submodules plus its own checkpoint, too much for today.
- Seed2GS (arXiv 2608.11928) and GaussianSelector (arXiv 2608.01492) have no code.
  Seed2GS's idea is worth keeping: render virtual orbits of the splat around the object and propagate one seed mask along them.
  GaussianSelector's idea: group Gaussians into superpoints and grow sparse evidence with a graph cut, which suits our one-to-three views per asset.

### Power, industrial and solar entries in the list

Honest finding: no entry ships code we can use under a license we can ship, so these are citations for the pitch, not code.
They do show that "SAM on power equipment" is an active line of work, which backs the SPIE paper's framing.

- SAMCM-SR, "Applying SAM3 Under Data-Scarce Conditions for Cross-Modal Segmentation of Power Equipment Infrared Images with Super-Resolution Enhancement", Applied Sciences 2026, https://www.mdpi.com/2076-3417/16/5/2351; no code.
  Closest to our thermal photos of INV-1.
- PowerSAM, "Edge-Efficient Segment Anything for Power Systems Through Visual Model Distillation", arXiv 2025; no code.
- ELE-SAM, "Adapting Segment Anything Model for Power Transmission Corridor Hazard Segmentation", arXiv 2505.22105, https://github.com/Hhaizee/ELE-SAM; code without a license, so ideas only.
- MSBA-SAM, power grid segmentation in aerial images, Energy Informatics 2026; no code.
- "Defect segmentation and 3D reconstruction in concrete structures using SAM 2 and 3D Gaussian splatting", Journal of Civil Structural Health Monitoring 2025, https://doi.org/10.1007/s13349-025-00993-z; no code.
  It is the same SAM-then-splat pattern as the SPIE paper, applied to inspection, and is the best second citation for it.
- Industrial3D, a terrestrial LiDAR point-cloud benchmark of industrial infrastructure, arXiv 2603.28660, https://github.com/PointCloudYC/Industrial3D; GPL-3.0, cite only.
- CAD-Prompted SAM3, geometry-conditioned instance segmentation of industrial objects, arXiv 2026; no code.
- Solar from remote sensing, all without code: PVPSAM (weakly supervised photovoltaic panel extraction, JSTARS 2026), ESAM (edge-enhanced SAM for photovoltaic plants, JAG 2025), SolarSAM (rooftop photovoltaic potential, arXiv 2407.00296), PV-SAM (photovoltaic defect segmentation, Solar Energy 2025).
- segment-geospatial, https://github.com/opengeos/segment-geospatial (MIT, 4169 stars), runs SAM, SAM 2 and SAM 3 with text prompts on aerial tiles (`docs/examples/sam2_text_prompts.ipynb`).
  It fits the "nightly drone recapture" story for panel rows, not our ground-level iPhone capture.
- Segment-Any-Anomaly, https://github.com/caoyunkang/Segment-Any-Anomaly (MIT, 842 stars), is Grounding DINO plus SAM with defect text prompts (`SAA/prompts/`).
  It is a later option for status masks such as the XFMR-1 oil leak photos.

### Recommended 1-hour pipeline

Idea: SAM 2.1 tiny makes one mask per registered photo for that station's asset, the mask is lifted onto Gaussian centres with our z-buffer depth test, votes are combined over the 2 to 3 views of each asset, the groups are cleaned in 3D, and the result goes to a JSON file the viewer loads.
No per-scene training, no CUDA, and the only new code is two scripts and a viewer hook.

1. Downloads, 5 minutes, after the user approves the list in the next subsection.
   Run the 2D step with the system `python3`, which already has torch and transformers, after adding torchvision.
2. `scripts/segment_photos.py`, 15 minutes to write, under 2 minutes to run on 10 photos.
   It imports only torch, transformers, PIL and numpy.
   - Read the registration file and `viewpoints.yaml`.
     For each view, open the photo with `ImageOps.exif_transpose` exactly as `load_photo` does, and check that its size equals the view's `size`, so `K` still applies.
   - Pick the text from the asset id prefix: `INV` "inverter cabinet.", `XFMR` "transformer.", `BESS` "battery container.", `CB` "combiner box.", `52` "circuit breaker.".
   - Run Grounding DINO tiny (box threshold about 0.35, text threshold 0.25; the Grounded-SAM-2 demo uses 0.4 and 0.3).
     Keep the box that contains the image centre, because the registered `target` is the splat point at the image centre and stations are aimed at their asset.
     If no box contains it, take the highest score; if there is no box at all, use the point prompt alone.
   - Run SAM 2.1 tiny with that box plus a positive point at the image centre, take the mask with the best `iou_scores`, and add negative points at the projected pins of any other asset in view if the mask bleeds.
   - Write `runs/segment/<stamp>/<vid>.png` (0 or 255, full photo size), an overlay JPEG per photo to check by eye, and `masks.json` with `vid`, `asset`, `label`, `box` and scores.
   - Plan B with no Grounding DINO: point prompt only, or send the photo to the Crusoe `vision_fast` model in `gridtwin/config.py` and ask for the asset's box, which needs no download.
3. `gridtwin/splat/segment.py` plus `scripts/segment_splat.py`, 20 minutes, in the project `.venv`.
   - `splat = load_ply(ply)`, `index = SplatIndex.from_splat(splat)`, `ply_ids = np.flatnonzero(splat.opacity >= 0.3)`, and `s = board_to_splat["scale"]`.
   - Per view, render at 1024 px wide: `f = 1024 / W`, `Ks = scaled(K, f)`, `_, zbuf = index.render(Ks, R, t, (w, h), max_radius=3)`.
     `max_radius=3` keeps the disc loop short; the estimate is seconds per view, not measured yet.
     Resize the mask to `(w, h)` with nearest-neighbour and erode it by 2 px so boundary Gaussians do not bleed in.
   - Project every centre: `cam = centers @ R.T + t`, keep `z > 0` and pixels inside the image, and read `d = zbuf[v, u]`.
     Set `tol = max(0.03 * s, 0.015 * z)`, that is 3 cm or 1.5 % of the distance.
     A Gaussian is seen if `z - d <= tol`, with weight `w = 1 - min(abs(z - d) / tol, 1)` as in MV3DIS `get_points_label_seen`.
   - For the view's asset `a`: `pos[a] += w * inside` and `neg[a] += w * (seen and not inside)`.
     If a photo also shows other assets with their own masks, they vote the same way from that photo.
   - Assign a Gaussian to the asset with the highest `pos / (pos + neg)` when that ratio is at least 0.6 and `pos >= 0.5`, so it was seen inside at least one mask at a good depth.
   - Clean up in three passes.
     Drop members whose `scale` is above 3 times the group's median, the cheap version of SAGD's boundary decomposition, which also removes floaters.
     Voxelize members at `0.02 * s` and keep the 26-connected component with the most `pos` weight, plus any component above 20 % of it, with a short breadth-first search over a voxel dict.
     Fill the unseen back side: in the plane perpendicular to "up", rasterize the group's footprint with `cv2.convexHull` and `cv2.fillPoly`, then add Gaussians that no view saw, whose footprint falls inside and whose height lies within the group's height range.
   - Write `assets/splats/<scene>.segments.json` and a QA overlay per view, the group rendered with `index.subset(...)` and blended on the photo as `overlay()` does.
     Sorted PLY indices cost about 7 bytes each in JSON, so 100,000 Gaussians per asset is about 0.7 MB; switch to delta-encoded runs only if the file gets large.

   ```json
   {
     "splat": "assets/splats/scene.ply",
     "splat_sha256": "same value as in scene.registration.json",
     "count": 812345,
     "method": "sam2.1-hiera-tiny + grounding-dino-tiny, z-buffer vote, tol 3 cm",
     "assets": {
       "INV-1": {"label": "inverter cabinet", "views": ["inv1_cabinet"], "n": 41210,
                 "centroid": [0.1, 0.2, 0.3], "aabb": [[0, 0, 0], [1, 1, 1]], "indices": [1203, 1204, 1310]}
     }
   }
   ```

4. Viewer hook, 15 minutes, for whoever owns the twin page.
   - Fetch `segments.json` and refuse it if `splat_sha256` differs from the registration file's, since the indices address one exact PLY.
   - After the `SplatMesh` loads, snapshot the original colours of labelled splats with `mesh.packedSplats.forEachSplat((i, center, scales, quaternion, opacity, color) => ...)`; Spark 2.3.1 passes exactly those arguments.
   - Status colour: for each index call `packedSplats.setSplat(i, center, scales, quaternion, opacity, tinted)`, then set `packedSplats.needsUpdate = true`.
     Mix about half status colour into the original so the texture stays readable.
   - Hover and click: raycast as in Spark's `examples/raycasting`, pick the asset whose `aabb` holds the hit point (nearest `centroid` as a tie-break), and brighten it.
     A `SplatEdit` with a `SplatEditSdf` box and blend mode `ADD_RGBA` over that box is the no-copy alternative for the hover glow.
   - Keep LOD off for this mesh: Spark's `lod` option builds a separate `lodSplats` set, and our indices address the base `PackedSplats` in PLY order.
     If the PLY is converted to SOG or SPZ with splat-transform, which may reorder splats, rerun step 3 on the converted file.
   - The same groups let `get_camera_view` paint a hidden state onto exactly one asset's Gaussians (`docs/MEMORY.md`, 11:44).
5. Stretch, if time remains: make more views from the splat itself, as SA3D and Seed2GS do.
   Render virtual cameras orbiting the asset with `index.render` plus `fill_holes`, prompt SAM 2 with points from SA3D's `mask_to_prompt` applied to the projected group, and vote again.
   It covers the back side when an asset has only one or two photos.

Automatic naming beyond the station's own asset (b): run Grounding DINO once per photo with all classes in one prompt, "inverter cabinet. transformer. battery container. combiner box. circuit breaker. switchgear.".
Assign each box to the asset whose pin projects inside it, and list unmatched boxes as "unlisted equipment" for a human to name.

Timing assumptions: the per-photo model times above are estimates for an M4 and were not measured here.
Grounding DINO has had MPS operator gaps in older torch releases, so run it on CPU and SAM 2 on MPS, with `PYTORCH_ENABLE_MPS_FALLBACK=1` set.

### Downloads and packages to approve

Ask the user before any of these; together the required items are about 848 MB.

| Item | Source | Size | License | Login |
|---|---|---|---|---|
| `facebook/sam2.1-hiera-tiny` (`model.safetensors` plus small configs) | huggingface.co | 155,908,064 bytes (155.9 MB) | Apache-2.0 | no |
| `IDEA-Research/grounding-dino-tiny` (`model.safetensors` plus tokenizer files, about 1 MB) | huggingface.co | 689,359,096 bytes (689.4 MB) | Apache-2.0 | no |
| `torchvision==0.29.1` for the system `python3` (`pip install`) | pypi.org | 1,813,852 bytes (1.8 MB) wheel, cp314 macOS arm64 | BSD-3-Clause | no |
| Optional: `facebook/sam2.1-hiera-small` | huggingface.co | 184,305,280 bytes (184.3 MB) | Apache-2.0 | no |
| Optional: `google/owlv2-base-patch16-ensemble`, alternative detector | huggingface.co | 619,918,824 bytes (619.9 MB) | Apache-2.0 | no |
| Optional: EfficientSAM ONNX encoder and decoder, for a browser SAM | github.com/yformer/EfficientSAM `weights/` | 24.8 MB plus 16.6 MB | Apache-2.0 | no |
| Alternative to the system `python3`: `torch==2.14.1 torchvision==0.29.1 transformers==5.18.0` in `.venv` | pypi.org | torch wheel 127,325,330 bytes (127.3 MB), transformers wheel 12.6 MB, plus tokenizers, safetensors and huggingface-hub | BSD-3-Clause, Apache-2.0 | no |

Hugging Face files land in `~/.cache/huggingface/hub`; set `HF_HUB_OFFLINE=1` after the first run so the demo never fetches again.

### README credits

If we ship this pipeline, the README must credit:
- SAM 2 and SAM 2.1, Meta, Apache-2.0: N. Ravi et al., "SAM 2: Segment Anything in Images and Videos", arXiv 2408.00714, weights `facebook/sam2.1-hiera-tiny`.
- Grounding DINO, IDEA Research, Apache-2.0: S. Liu et al., arXiv 2303.05499, weights `IDEA-Research/grounding-dino-tiny`.
- Grounded SAM and Grounded-SAM-2, IDEA Research, Apache-2.0, for the text-to-box-to-mask flow: T. Ren et al., "Grounded SAM", arXiv 2401.14159.
- Hugging Face Transformers, Apache-2.0, which runs both models.
- MV3DIS, MIT, Copyright (c) 2026 Yibo Zhao, if `get_points_label_seen` is copied or adapted; MIT also needs its copyright and permission notice kept in the copied file.
- SA3D, Apache-2.0, if `mask_to_prompt` is copied; Apache-2.0 needs the license text and a note of our changes.
- SegmentAnything3D, MIT, Copyright (c) 2023 Pointcept, if its merge logic is copied.
- MobileSAM or EfficientSAM, Apache-2.0, if either replaces SAM 2.
- Cite as ideas, with no code copied: SAGD (arXiv 2401.17857), FlashSplat, the BMVC 2025 multi-view Bayesian fusion paper (arXiv 2512.06882), Seed2GS, GaussianSelector, and the SPIE paper (DOI 10.1117/12.3114168).
- The survey source, Awesome-Segment-Anything (MIT), is a courtesy credit, not a license requirement.
