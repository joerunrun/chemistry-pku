"""Small curated teacher supplement; PDF images are prepared offline."""
import json
from pathlib import Path

from pipeline.resource_paths import runtime_asset_path

CATALOG_PATH = Path(__file__).with_name("teacher_materials.json")


def search_materials(query: str) -> tuple[list[dict], list[dict]]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    query = query.casefold()
    ranked = sorted(
        ((sum(word.casefold() in query for word in topic["keywords"]), topic)
         for topic in catalog["topics"]), key=lambda item: item[0], reverse=True,
    )
    sources, images = [], []
    for score, topic in ranked[:3]:
        if not score:
            continue
        sources.append({
            "knowledge_base_id": "teacher_supplements",
            "document_id": f"{catalog['document_id']}:{topic['id']}",
            "title": f"{catalog['title']} · {topic['title']}",
            "pages": topic["pages"], "content": topic["content"],
        })
        for example in topic["images"]:
            path = runtime_asset_path("knowledge_supplements", catalog["document_id"], example["file"])
            if path.is_file():
                image_score = score + sum(word.casefold() in query for word in example["keywords"])
                images.append({**example, "path": path, "score": image_score,
                               "document_id": sources[-1]["document_id"], "title": catalog["title"]})
    images.sort(key=lambda item: item["score"], reverse=True)
    return sources, images[:2]
