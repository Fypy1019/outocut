from __future__ import annotations

import random

import pytest

from outocut_engine.models import JobCreate
from outocut_engine.renderer import (
    RenderFailed,
    _available_artifact_paths,
    cpu_encode_thread_count,
    select_shot_assets,
)


def grouped_request() -> JobCreate:
    return JobCreate.model_validate(
        {
            "name": "grouped-selection",
            "template": {"name": "folder-view", "shot_count": 6},
            "asset_paths": [],
            "asset_groups": [
                {
                    "category_id": "action",
                    "name": "action",
                    "asset_paths": ["action-1.mp4", "action-2.mp4"],
                },
                {
                    "category_id": "product",
                    "name": "product",
                    "asset_paths": ["product-1.mp4", "product-2.mp4"],
                },
            ],
            "shots": [{"text": f"shot-{index}"} for index in range(6)],
            "output_dir": ".",
            "seed": 42,
        }
    )


def test_grouped_selection_is_balanced_random_and_repeatable() -> None:
    request = grouped_request()
    first = select_shot_assets(request, random.Random(42))
    second = select_shot_assets(request, random.Random(42))

    assert first == second
    assert len(first) == 6
    assert sum(path.startswith("action-") for path in first) == 3
    assert sum(path.startswith("product-") for path in first) == 3
    assert all(
        first[index].split("-", 1)[0] != first[index + 1].split("-", 1)[0]
        for index in range(len(first) - 1)
    )
    assert first[0] != first[2]
    assert first[1] != first[3]


def test_each_shot_uses_its_selected_folder() -> None:
    request = grouped_request()
    requested_groups = ["product", "product", "action", "product", "action", "action"]
    for shot, category_id in zip(request.shots, requested_groups, strict=True):
        shot.category_id = category_id

    selected = select_shot_assets(request, random.Random(7))

    assert [path.split("-", 1)[0] for path in selected] == requested_groups


def test_selection_only_uses_assets_long_enough_for_each_shot() -> None:
    request = grouped_request()
    for shot in request.shots:
        shot.category_id = "action"
    durations = {
        "action-1.mp4": 2.0,
        "action-2.mp4": 6.0,
        "product-1.mp4": 2.0,
        "product-2.mp4": 6.0,
    }

    selected = select_shot_assets(request, random.Random(4), [5.0] * 6, durations)

    assert selected == ["action-2.mp4"] * 6


def test_selection_fails_instead_of_looping_a_short_asset() -> None:
    request = grouped_request()
    request.shots[0].category_id = "product"
    durations = {
        "action-1.mp4": 2.0,
        "action-2.mp4": 3.0,
        "product-1.mp4": 2.0,
        "product-2.mp4": 3.0,
    }

    with pytest.raises(RenderFailed, match="镜头 1 需要至少 5.0 秒"):
        select_shot_assets(request, random.Random(4), [5.0] * 6, durations)


def test_output_paths_increment_without_overwriting_existing_artifacts(tmp_path) -> None:
    completed_dir = tmp_path / "Completed"
    subtitle_dir = tmp_path / "ass"
    manifest_dir = tmp_path / "json"
    first_video, first_subtitle, first_manifest = _available_artifact_paths(
        completed_dir, subtitle_dir, manifest_dir, "same-task", 0
    )
    first_video.write_bytes(b"original-video")
    first_subtitle.write_text("original-subtitle", encoding="utf-8")
    first_manifest.write_text("{}", encoding="utf-8")

    second_video, second_subtitle, second_manifest = _available_artifact_paths(
        completed_dir, subtitle_dir, manifest_dir, "same-task", 0
    )

    assert first_video.parent == completed_dir
    assert first_subtitle.parent == subtitle_dir
    assert first_manifest.parent == manifest_dir
    assert second_video.name == "same-task-001-002.mp4"
    assert second_subtitle.name == "same-task-001-002.ass"
    assert second_manifest.name == "same-task-001-002.json"
    assert first_video.read_bytes() == b"original-video"


@pytest.mark.parametrize(
    ("logical_cpus", "expected"),
    [(16, 14), (4, 2), (2, 1), (1, 1)],
)
def test_cpu_encoding_reserves_two_logical_cores(logical_cpus: int, expected: int) -> None:
    assert cpu_encode_thread_count(logical_cpus) == expected
