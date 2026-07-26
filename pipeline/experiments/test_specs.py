from __future__ import annotations

from pipeline.experiments.retrieval_runner import retrieve_and_analyze
from pipeline.experiments.spec_loader import get_spec, list_specs, reload_specs


def test_all_experiment_specs_load_and_validate():
    reload_specs()
    specs = {spec["id"]: spec for spec in list_specs()}

    assert {
        "atmospheric_distillation",
        "distillation",
        "photometer",
        "electrolysis",
        "safety",
        "tlc",
        "thin_layer_chromatography",
        "column_chromatography",
        "recrystallization_reflux",
        "recrystallization_filtration",
        "other",
    } <= specs.keys()
    assert specs["distillation"]["strategy"] == "cv_reference"
    assert specs["atmospheric_distillation"]["strategy"] == "cv_reference"
    assert specs["electrolysis"]["strategy"] == "retrieval"
    assert specs["tlc"]["strategy"] == "vlm_only"
    assert specs["thin_layer_chromatography"]["strategy"] == "vlm_only"
    assert specs["column_chromatography"]["strategy"] == "vlm_only"
    assert specs["recrystallization_reflux"]["strategy"] == "vlm_only"
    assert specs["recrystallization_filtration"]["strategy"] == "vlm_only"
    assert specs["thin_layer_chromatography"]["reference_images"] == [
        {
            "role": "correct",
            "path": (
                "assets://reference_images/"
                "thin_layer_chromatography/correct.png"
            ),
        },
        {
            "role": "wrong",
            "path": (
                "assets://reference_images/"
                "thin_layer_chromatography/wrong.png"
            ),
        },
    ]
    assert "展开剂前沿" in specs["thin_layer_chromatography"]["prompt"]
    assert "冷凝管" in specs["recrystallization_reflux"]["prompt"]
    assert "吸滤瓶" in specs["recrystallization_filtration"]["prompt"]


def test_distillation_reference_and_prompt_are_owned_by_json():
    spec = get_spec("distillation")

    assert spec is not None
    assert spec["reference"] == {
        "source": "file",
        "annotated_image_path": (
            "assets://reference_images/distillation_correct_annotated.png"
        ),
    }
    assert "克氏蒸馏头" in spec["experiment_requirements"]
    assert "{cv_summary}" in spec["prompt_a"]
    assert "vacuum_gauge" not in {
        component["label"] for component in spec["components"]
    }
    assert "真空表" not in spec["experiment_requirements"]


def test_atmospheric_distillation_has_independent_reference():
    spec = get_spec("atmospheric_distillation")

    assert spec is not None
    assert spec["reference"] == {
        "source": "file",
        "annotated_image_path": (
            "assets://reference_images/"
            "atmospheric_distillation_correct_annotated.png"
        ),
    }
    assert "常压蒸馏" in spec["prompt_a"]


def test_empty_safety_retrieval_uses_json_result(monkeypatch):
    spec = get_spec("safety")
    assert spec is not None

    monkeypatch.setattr(
        "pipeline.safety_check.retriever.retrieve",
        lambda *_args, **_kwargs: [],
    )
    result = retrieve_and_analyze(spec, b"image")

    assert result["analysis_text"] == spec["empty_result_text"]
    assert result["mode"] == "safety"
    assert result["timings"]["vlm"] == 0.0


def test_empty_electrolysis_retrieval_uses_json_result(monkeypatch):
    spec = get_spec("electrolysis")
    assert spec is not None

    monkeypatch.setattr(
        "pipeline.electrolysis_check.retriever.retrieve_pair",
        lambda *_args, **_kwargs: {
            "correct": None,
            "wrong": None,
            "elapsed_ms": 0.0,
        },
    )
    result = retrieve_and_analyze(spec, b"image")

    assert result["analysis_text"] == spec["empty_result_text"]
    assert result["experiment_type"] == "electrolysis"
    assert result["timings"]["vlm"] == 0.0
