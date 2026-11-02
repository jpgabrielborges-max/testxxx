from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wifstudio.cinematic25d import compile_story


def test_continuous_camera_actions_and_timeline():
    story = compile_story([
        {"text": "Ela olhou para trás.", "duration": 3, "characters": ["Lia"]},
        {"text": "O amigo levantou a lanterna.", "duration": 4, "characters": ["Caio"]},
        {"text": "Os dois ficaram assustados.", "duration": 3, "characters": ["Lia", "Caio"]},
    ], ["Lia", "Caio"])
    assert story["duration"] == 10
    assert [x["action"] for x in story["beats"]] == ["look", "lantern", "fear"]
    assert story["beats"][1]["start"] == story["beats"][0]["end"]
    assert story["camera"][0]["at"] == 0
    assert story["camera"][-1]["at"] == 10
    assert all("cut" not in c for c in story["camera"])
    assert story["events"][1]["actor"] == "actor_1"


def test_validation_and_deterministic_actor_colors():
    scenes = [{"text": "Vento na floresta", "duration": 5}]
    assert compile_story(scenes) == compile_story(scenes)
    assert compile_story(scenes)["actors"][0]["palette"]["hair"].startswith("#")
    for bad in ([], [{"text": "", "duration": 1}], [{"text": "Hello", "duration": -1}]):
        try:
            compile_story(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Entrada inválida deveria falhar")
