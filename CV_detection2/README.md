# CV_detection2

Mask-based local feature detection for fixed chemistry apparatus.

This version does not use whole-image registration. It builds an offline AKAZE descriptor database from hand-drawn object masks, then detects each apparatus in a new image by descriptor matching plus local affine RANSAC.

## 1. Annotate Reference Images

```powershell
python CV_detection2\annotate_app.py --host 127.0.0.1 --port 5070
```

Open:

```text
http://127.0.0.1:5070
```

Upload a correct reference image. For each apparatus, click polygon points on the object body and press `完成当前多边形`. One label can have multiple polygon regions. Empty labels are allowed.

Saving writes:

```text
CV_detection2\data\images\
CV_detection2\data\annotations.json
CV_detection2\data\descriptor_db.json
CV_detection2\data\descriptor_db.npz
```

## 2. Detect New Images

```powershell
python CV_detection2\detect_app.py --host 127.0.0.1 --port 5071
```

Open:

```text
http://127.0.0.1:5071
```

Upload a new image. The page displays the annotated result, timing, match counts, RANSAC inliers, scores, boxes, and failure reasons.

## Algorithm

Offline:

1. Rasterize each hand-drawn polygon set into a binary mask.
2. Compute the mask bounding box automatically.
3. Extract AKAZE keypoints/descriptors only inside the mask.
4. Save descriptors and keypoint coordinates into the descriptor database.

Online:

1. Extract AKAZE features once from the full target image.
2. For each apparatus label, match its stored descriptors against target descriptors.
3. Filter matches with Lowe ratio test.
4. Estimate local affine transform with RANSAC.
5. Accept only candidates with enough good matches, enough inliers, valid inlier ratio, and reasonable scale.
6. Project the reference mask bbox through the affine transform and draw the result.

There is no whole-image homography and no template matching fallback in this version.

## 3. Compare AKAZE / SIFT

```powershell
python CV_detection2\compare_app.py --host 127.0.0.1 --port 5072
```

Open:

```text
http://127.0.0.1:5072
```

Upload one new image. The page runs two detectors in parallel:

- `AKAZE`: existing mask descriptor database.
- `SIFT`: mask descriptor database built on demand from existing annotations.

The comparison page shows two result images side by side, plus timing, match statistics, scores, boxes, and failure reasons.

## 4. Debug Feature Matches

```powershell
python CV_detection2\test.py --host 127.0.0.1 --port 5073
```

Open:

```text
http://127.0.0.1:5073
```

Choose AKAZE or SIFT, choose one reference sample, upload a target image, and inspect the keypoint match lines. Green lines are RANSAC inliers; red lines are rejected outliers.
