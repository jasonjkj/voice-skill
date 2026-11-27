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

def test_key_from_voice_dotenv_env_var(monkeypatch, tmp_path):
    """VOICE_DOTENV 显式指定：优先于其它候选。"""
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_HOME", raising=False)
    f = tmp_path / "secret.env"
    f.write_text("MIMO_API_KEY=from-dotenv-var\n", encoding="utf-8")
    monkeypatch.setenv("VOICE_DOTENV", str(f))
    assert v._load_mimo_key() == "from-dotenv-var"


def test_key_found_by_walking_up_from_package_dir(monkeypatch, tmp_path):
    """包被内联到 vendor/<pkg>/ 时，仓根 .env 在包目录上方——必须能向上找到。"""
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_HOME", raising=False)
    monkeypatch.delenv("VOICE_DOTENV", raising=False)
    # 造 vendor/pkg/voice_skill/voice.py 这样的三层结构
    pkg_dir = tmp_path / "vendor" / "pkg" / "voice_skill"
    pkg_dir.mkdir(parents=True)
    (tmp_path / ".env").write_text("MIMO_API_KEY=from-repo-root\n", encoding="utf-8")
    monkeypatch.setattr(v, "__file__", str(pkg_dir / "voice.py"))
    assert v._load_mimo_key() == "from-repo-root"


def test_voice_home_beats_parent_dotenv(monkeypatch, tmp_path):
    """VOICE_HOME/.env 排在向上查找之前。"""
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_DOTENV", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("MIMO_API_KEY=from-voice-home\n", encoding="utf-8")
    (tmp_path / ".env").write_text("MIMO_API_KEY=from-parent\n", encoding="utf-8")
    monkeypatch.setenv("VOICE_HOME", str(home))
    monkeypatch.setattr(v, "__file__", str(tmp_path / "x" / "y" / "voice.py"))
    assert v._load_mimo_key() == "from-voice-home"


def test_missing_key_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_HOME", raising=False)
    monkeypatch.delenv("VOICE_DOTENV", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # 别读到真实用户级 .env
    monkeypatch.setattr(v, "__file__", str(tmp_path / "nowhere" / "voice_skill" / "voice.py"))
    assert v._load_mimo_key() is None
