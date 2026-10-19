"""Check source selection and the two-call correction payload without network requests."""
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from pipeline.qa import photo_check
from pipeline.qa.teacher_materials import search_materials


class PhotoCheckMaterialsTest(TestCase):
    def test_material_scope_and_unrelated_query(self):
        sources, _ = search_materials("色谱柱出液口与锥形瓶的套接")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["pages"], [15])
        self.assertIn("仅限生科学院", sources[0]["content"])
        self.assertEqual(search_materials("滴定管读数"), ([], []))

    def test_current_photo_precedes_examples_and_both_knowledge_sources_survive(self):
        external = {"knowledge_base_id": "external", "document_id": "doc", "title": "回流规范", "content": "回流规范内容"}
        with TemporaryDirectory() as assets, patch.dict("os.environ", {"CHEM_RUNTIME_ASSETS_DIR": assets}):
            from pathlib import Path
            folder = Path(assets) / "knowledge_supplements" / "organic_apparatus"
            folder.mkdir(parents=True)
            for name in ("reflux_clamp.jpg", "reflux_closed.jpg"):
                (folder / name).write_bytes(name.encode())
            with patch.object(photo_check, "create_vlm"), \
                 patch.object(photo_check, "build_image_part_from_bytes", side_effect=lambda data, _: {"image": data}), \
                 patch.object(photo_check, "search", return_value=[external]), \
                 patch.object(photo_check, "call_vlm", side_effect=[("回流装置，冷凝管夹子及上方塞子", 10), ("请固定冷凝管。", 20)]) as vlm:
                result = photo_check.check_photo(b"current-photo", "检查回流装置", provider="qiniu", model="test")
            content = vlm.call_args_list[1].args[1][1]["content"]
            pictures = [part["image"] for part in content if "image" in part]
            self.assertEqual(pictures[0], b"current-photo")
            self.assertCountEqual(pictures[1:], [b"reflux_clamp.jpg", b"reflux_closed.jpg"])
            self.assertIn("非当前现场", content[2]["text"])
            self.assertIn("回流规范内容", content[0]["text"])
            self.assertIn("【强制】", content[0]["text"])
            self.assertEqual(vlm.call_count, 2)
            self.assertEqual(len(result["reference_images"]), 2)
            self.assertEqual(result["sources"][0]["knowledge_base_id"], "external")
            self.assertTrue(any(s["knowledge_base_id"] == "teacher_supplements" for s in result["sources"]))

    def test_text_only_materials_and_no_material_fallback(self):
        with TemporaryDirectory() as assets, patch.dict("os.environ", {"CHEM_RUNTIME_ASSETS_DIR": assets}), \
             patch.object(photo_check, "create_vlm"), \
             patch.object(photo_check, "build_image_part_from_bytes", return_value={"image": "current"}), \
             patch.object(photo_check, "search", return_value=[]), \
             patch.object(photo_check, "call_vlm", side_effect=[("分液漏斗和接收瓶", 1), ("请核对接收瓶。", 2),
                                                                ("滴定管刻度", 1), ("刻度不清。", 2)]):
            supplied = photo_check.check_photo(b"photo", "检查分液", provider="qiniu", model="test")
            missing = photo_check.check_photo(b"photo", "检查滴定读数", provider="qiniu", model="test")
        self.assertEqual(supplied["analysis_text"], "请核对接收瓶。")
        self.assertEqual(supplied["reference_images"], [])
        self.assertEqual(supplied["sources"][0]["pages"], [24])
        self.assertTrue(missing["analysis_text"].startswith("未找到对应知识库资料"))
