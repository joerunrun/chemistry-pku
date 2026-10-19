"""Render curated PDF examples locally; server does not need PyMuPDF."""
import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from pipeline.qa.teacher_materials import CATALOG_PATH
from pipeline.resource_paths import runtime_asset_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--bundle", type=Path, help="Optional standalone server asset ZIP")
    args = parser.parse_args()
    import pymupdf  # Offline preparation only: pip install pymupdf

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    root = runtime_asset_path("knowledge_supplements")
    source = args.pdf or root / catalog["pdf"]
    output = root / catalog["document_id"]
    output.mkdir(parents=True, exist_ok=True)
    files = []
    with pymupdf.open(source) as document:
        for topic in catalog["topics"]:
            for example in topic["images"]:
                page = document[example["page"] - 1]
                bounds = page.get_image_info()[example["image_index"]]["bbox"]
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=pymupdf.Rect(bounds), alpha=False)
                target = output / example["file"]
                pixmap.save(target, jpg_quality=88)
                files.append(target)
    if args.bundle:
        args.bundle.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(args.bundle, "w", ZIP_DEFLATED) as bundle:
            for file in files:
                bundle.write(file, "runtime_assets/" + file.relative_to(root.parent).as_posix())
    print(f"Prepared {len(files)} reference images: {sum(f.stat().st_size for f in files):,} bytes")
    if args.bundle:
        print(f"Asset bundle: {args.bundle.resolve()}")


if __name__ == "__main__":
    main()
