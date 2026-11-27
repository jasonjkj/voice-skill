#!/usr/bin/env python3
"""VOICE_HOME / 配置三段式解析单测。

run: python3 -m pytest tests/test_home.py -q
"""
import os

import voice_skill.voice as v


def test_home_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_HOME", str(tmp_path))
    assert v._home() == tmp_path.resolve()


def test_home_default_is_package_dir(monkeypatch):
    monkeypatch.delenv("VOICE_HOME", raising=False)
    assert v._home().name == "voice_skill"


def test_config_prefers_voice_home(monkeypatch, tmp_path):
    (tmp_path / "voice-config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("VOICE_HOME", str(tmp_path))
    assert v.load_config() == {}


def test_default_out_under_voice_home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_HOME", str(tmp_path))
    monkeypatch.delenv("VOICE_CACHE", raising=False)
    p = v.default_out_path()
    assert p.parent == tmp_path / "out"
    assert p.name.startswith("tts_") and p.suffix == ".mp3"


def test_default_out_voice_cache_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_CACHE", str(tmp_path / "cache"))
    p = v.default_out_path()
    assert p.parent == (tmp_path / "cache")