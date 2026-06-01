"""
离线建库：从安全隐患 docx 提取图片+整改意见，提取 AKAZE 特征，存为可快速加载的 db。
运行一次即可：python -m pipeline.safety_check.build_db
"""
from __future__ import annotations

import json
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

from pipeline.resource_paths import preferred_runtime_dir

HERE = Path(__file__).resolve().parent
DATA_DIR = preferred_runtime_dir("safety_check_data", HERE / "data")
IMAGES_DIR = DATA_DIR / "images"
FEATURES_NPZ = DATA_DIR / "features.npz"
METADATA_JSON = DATA_DIR / "metadata.json"
DOCX_PATH = HERE.parent.parent.parent / "安全隐患图片-供AI识别.docx"

MAX_SIDE = 1600
AKAZE = cv2.AKAZE_create()
BF = cv2.BFMatcher(cv2.NORM_HAMMING)


def extract_docx(docx_path: Path) -> list[dict]:
    """从 docx 提取每行的图片文件名和整改意见文本。"""
    z = zipfile.ZipFile(docx_path)
    xml_content = z.read("word/document.xml")
    root = ET.fromstring(xml_content)

    ns_w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns_a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    # 先解析 rId → 文件名映射
    rels_xml = z.read("word/_rels/document.xml.rels")
    rels_root = ET.fromstring(rels_xml)
    rid_to_file: dict[str, str] = {}
    ns_rel = "http://schemas.openxmlformats.org/package/2006/relationships"
    for rel in rels_root:
        rid = rel.get("Id", "")
        target = rel.get("Target", "")
        if "image" in target.lower():
            rid_to_file[rid] = Path(target).name

    # 解析表格
    tables = root.findall(f".//{{{ns_w}}}tbl")
    if not tables:
        print("No tables found in docx.")
        return []

    data_rows = []
    for row in tables[0].findall(f"{{{ns_w}}}tr")[1:]:  # 跳过表头
        cells = row.findall(f"{{{ns_w}}}tc")
        if len(cells) < 4:
            continue

        # Cell0: images
        image_files = []
        for blip in cells[0].findall(f".//{{{ns_a}}}blip"):
            embed = blip.get(f"{{{ns_r}}}embed", "")
            fname = rid_to_file.get(embed, "")
            if fname:
                image_files.append(fname)

        # Cell1: title/category
        title_text = " ".join(t.text or "" for t in cells[1].iter(f"{{{ns_w}}}t")).strip()

        # Cell3: 整改意见
        rectification = " ".join(t.text or "" for t in cells[3].iter(f"{{{ns_w}}}t")).strip()

        if image_files:
            data_rows.append({
                "image_files": image_files,
                "title": title_text,
                "rectification": rectification,
            })

    # 解压图片到 images/
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    for name in z.namelist():
        if "media/image" in name:
            data = z.read(name)
            dest = IMAGES_DIR / Path(name).name
            if not dest.exists():
                dest.write_bytes(data)

    return data_rows


def preprocess(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    if max(h, w) <= MAX_SIDE:
        working = image.copy()
        scale = 1.0
    else:
        scale = MAX_SIDE / max(h, w)
        working = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))),
                             interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def extract_features(image_path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """返回 (keypoints_nx2_float32, descriptors_nx61_uint8_or_None)。"""
    data = np.fromfile(str(image_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return np.zeros((0, 2), dtype=np.float32), None
    gray = preprocess(img)
    kps, desc = AKAZE.detectAndCompute(gray, None)
    if not kps or desc is None:
        return np.zeros((0, 2), dtype=np.float32), None
    pts = np.array([kp.pt for kp in kps], dtype=np.float32)
    return pts, desc


def build_db() -> dict:
    print(f"Extracting from: {DOCX_PATH}")
    rows = extract_docx(DOCX_PATH)
    print(f"Found {len(rows)} entries, extracting features...")

    samples: list[dict] = []
    all_pts: list[np.ndarray] = []
    all_desc: list[np.ndarray] = []

    started = time.perf_counter()
    for i, row in enumerate(rows):
        for img_file in row["image_files"]:
            img_path = IMAGES_DIR / img_file
            if not img_path.exists():
                print(f"  MISSING: {img_file}")
                continue

            pts, desc = extract_features(img_path)
            if desc is None or len(pts) < 4:
                print(f"  SKIP {img_file}: too few keypoints ({len(pts)})")
                continue

            sid = f"{i}_{img_file}"
            pts_key = f"{sid}_pts"
            desc_key = f"{sid}_desc"
            all_pts.append(pts.astype(np.float32))
            all_desc.append(desc.astype(np.uint8))

            samples.append({
                "sample_id": sid,
                "entry_index": i,
                "image_file": img_file,
                "title": row["title"],
                "rectification": row["rectification"],
                "pts_key": pts_key,
                "desc_key": desc_key,
                "keypoint_count": len(pts),
            })

        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(rows)}...")

    # 写 npz
    arrays = {}
    for s, p, d in zip(samples, all_pts, all_desc):
        arrays[s["pts_key"]] = p
        arrays[s["desc_key"]] = d
    np.savez_compressed(FEATURES_NPZ, **arrays)

    metadata = {
        "version": 1,
        "feature": "AKAZE",
        "max_side": MAX_SIDE,
        "sample_count": len(samples),
        "total_keypoints": sum(s["keypoint_count"] for s in samples),
        "samples": samples,
    }
    METADATA_JSON.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    elapsed = (time.perf_counter() - started) * 1000
    print(f"Done. {len(samples)} samples saved in {elapsed:.0f} ms.")
    return metadata


if __name__ == "__main__":
    build_db()
