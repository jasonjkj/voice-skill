#!/usr/bin/env python3
"""包化骨架单测：voice_skill 可 import、参考音频/配置为 package data、voice-bff 入口存在。

运行：python3 -m pytest tests/ -q
"""
import importlib.resources as res


def test_package_importable():
    import voice_skill.voice as v

    assert hasattr(v, "speak")


def test_reference_audio_is_package_data():
    p = res.files("voice_skill") / "refs" / "example-reference.mp3"
    assert p.is_file()


def test_default_config_is_package_data():
    p = res.files("voice_skill") / "voice-config.json"
    assert p.is_file()


def test_console_entry_exists():
    import voice_skill.speak as s

    assert callable(s.main)