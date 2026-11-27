#!/usr/bin/env python3
"""mood_from_file 单测：可配置路径读情绪 mood 段，缺失/损坏安全退化 None。

run: python3 -m pytest tests/test_mood_source.py -q
"""
import json

import voice_skill.voice as v


def test_reads_mood_file(tmp_path, monkeypatch):
    f = tmp_path / "emotion-state.json"
    f.write_text(json.dumps({"mood": {"mood_id": "tender", "mood": "温柔"}}), encoding="utf-8")
    assert v.mood_from_file(f) == {"mood_id": "tender", "mood": "温柔"}


def test_env_var_path(tmp_path, monkeypatch):
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"mood": {"mood_id": "sharp"}}), encoding="utf-8")
    monkeypatch.setenv("VOICE_MOOD_JSON", str(f))
    assert v.mood_from_file() == {"mood_id": "sharp"}


def test_missing_file_returns_none(monkeypatch):
    monkeypatch.setenv("VOICE_MOOD_JSON", "/nonexistent/x.json")
    assert v.mood_from_file() is None


def test_bad_json_returns_none(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    assert v.mood_from_file(f) is None


def test_no_mood_key_returns_none(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps({"emotion": {}}), encoding="utf-8")
    assert v.mood_from_file(f) is None