# CV Detection

This folder contains the local annotation and detection tools.

## 1. Annotate the reference image

```powershell
python CV_detection\annotate.py --host 127.0.0.1 --port 5050
```

Open:

```text
http://127.0.0.1:5050
```

Draw the components that exist in the reference image. Missing components are saved as `[]` in `CV_detection\bbox.json`.

## 2. Detect the same components in a new image

Pass the same reference image that was used for annotation, plus the new target image:

```powershell
python CV_detection\detect.py --reference "path\to\correct.jpg" --target "path\to\new.jpg"
```

Outputs are written next to the target image by default:

```text
detected_<target_name>.jpg
detected_<target_stem>.json
```

The JSON keeps the same box format:

```json
[ymin, xmin, ymax, xmax]
```

Only non-empty boxes from `bbox.json` are detected. In the current annotation, this means the four marked components are used and skipped labels stay empty.

## Notes

- The method is not a general detector. It assumes the new image is the same setup with a reasonably similar viewpoint.
- It first estimates a reference-to-target homography, then refines each component with local template matching.
- If the reference image path cannot be inferred from `bbox.json`, always pass `--reference` explicitly.

## 3. View detection in a browser

```powershell
python CV_detection\detect_app.py --host 127.0.0.1 --port 5060
```

Open:

```text
http://127.0.0.1:5060
```

Upload the reference image and a new image. The page shows the annotated result, timing breakdown, homography statistics, per-component boxes, and template scores.
