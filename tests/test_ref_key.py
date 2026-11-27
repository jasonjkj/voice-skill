#!/usr/bin/env python3
"""refs/key 来源收敛单测。

run: python3 -m pytest tests/test_ref_key.py -q
"""
import os

import voice_skill.voice as v


def test_key_from_voice_home_env(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    (tmp_path / ".env").write_text("MIMO_API_KEY=abc123\n", encoding="utf-8")
    monkeypatch.setenv("VOICE_HOME", str(tmp_path))
    assert v._load_mimo_key() == "abc123"


def test_ref_resolves_from_voice_home(monkeypatch, tmp_path):
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "example-reference.mp3").write_bytes(b"x")
    monkeypatch.setenv("VOICE_HOME", str(tmp_path))
    assert v._resolve_ref("refs/example-reference.mp3").name == "example-reference.mp3"
    assert v._resolve_ref("refs/example-reference.mp3").parent == refs


def test_ref_falls_back_to_package_data(monkeypatch):
    monkeypatch.delenv("VOICE_HOME", raising=False)
    p = v._resolve_ref("refs/example-reference.mp3")
    assert p.is_file()