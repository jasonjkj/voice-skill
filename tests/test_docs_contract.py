#!/usr/bin/env python3
"""文档契约测试：把 README 记录的标准 API / voice-config schema / 八声线名称
钉成可执行断言，防止文档与实现静默漂移。**离线、不触网、不碰用户真实家目录**。

覆盖（README §4/§5/§7 记录的内容）：
1. 公开 API 签名（`voice_skill.voice.speak` 等）与 README §4 一致。
2. console 入口 `voice-bff` == `voice_skill.speak:main`（pyproject 声明 + 已装元数据）。
3. 八声线规范名（8 个）在配置真实存在、`resolve_voice_mode` 全命中、`list_voice_modes` 出 8 行。
4. 七档 emotion_id → 声线直连映射；`冷面` 无 emotion_id。
5. `voice-config.json` schema（顶层键 / active / providers / roles / moods / voice_modes /
   clone_styles / voices / retry）。
6. 配置三段式优先级：`VOICE_CONFIG_PATH` > `VOICE_HOME/voice-config.json` > 包内默认。

运行：python3 -m pytest tests/ -q
"""
import contextlib
import importlib.metadata
import inspect
import io
import tomllib
from pathlib import Path

import pytest

import voice_skill.speak as speak
import voice_skill.voice as voice

REPO_ROOT = Path(__file__).resolve().parents[1]

# README §4 记录的八声线规范名（顺序即配置 `voice_modes` 顺序）
VOICE_MODES = ["温柔", "魅惑", "清醒", "冷面", "热烈", "俏皮", "深沉", "慵懒"]
# 七档情绪直连（README §4 / voice-modes.md）
EMOTION_MAP = {
    "温柔": "tender",
    "魅惑": "teasing",
    "清醒": "sharp",
    "热烈": "fierce",
    "俏皮": "playful",
    "深沉": "deep",
    "慵懒": "lazy",
}


@pytest.fixture(autouse=True)
def _isolate_config_env(monkeypatch):
    """默认读包内配置：清掉 VOICE_HOME / VOICE_CONFIG_PATH（个别测试自行设置）。"""
    monkeypatch.delenv("VOICE_HOME", raising=False)
    monkeypatch.delenv("VOICE_CONFIG_PATH", raising=False)


@pytest.fixture
def cfg():
    return voice.load_config()


def _params(fn):
    return inspect.signature(fn).parameters


# ---------------------------------------------------------------------------
# 1. 公开 API 签名（README §4）
# ---------------------------------------------------------------------------

class TestPublicApiSignatures:
    def test_speak_signature_matches_docs(self):
        p = _params(voice.speak)
        assert list(p) == [
            "text", "role", "provider", "preset", "voice",
            "mood", "auto_mood", "style", "speed", "out",
        ]
        assert p["text"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert p["text"].default is inspect.Parameter.empty
        for name in ("role", "provider", "preset", "voice", "mood", "auto_mood", "style", "speed", "out"):
            assert p[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        # role 缺省由 voice-config.json 的 default_role 决定
        assert p["role"].default is None
        assert p["auto_mood"].default is False
        assert p["speed"].default == 1.0
        assert p["out"].default is None

    def test_speak_return_annotation_str(self):
        # `from __future__ import annotations` → 注解是字符串
        ann = inspect.signature(voice.speak).return_annotation
        assert ann in ("str", str)

    def test_documented_functions_exist_and_callable(self):
        for name in (
            "speak", "load_config", "mood_from_file", "voice_mode_for_emotion",
            "build_clone_style", "mimo_clone_tts", "mimo_tts", "edge_tts",
            "default_out_path", "resolve_voice", "_provider_order",
        ):
            assert callable(getattr(voice, name)), name

    def test_edge_tts_defaults(self):
        p = _params(voice.edge_tts)
        assert p["rate"].default == "+0%"
        assert p["pitch"].default == "+0Hz"
        assert p["voice"].default == "zh-CN-XiaoxiaoNeural"

    def test_build_clone_style_default_style_text_empty(self):
        p = _params(voice.build_clone_style)
        assert list(p) == ["cfg", "mood_name", "style_text"]
        assert p["style_text"].default == ""

    def test_mood_from_file_default_path_none(self):
        p = _params(voice.mood_from_file)
        assert list(p) == ["path"]
        assert p["path"].default is None

    def test_speak_module_public_api(self):
        assert callable(speak.main)
        assert callable(speak.list_voice_modes)
        assert callable(speak.resolve_voice_mode)
        assert list(_params(speak.resolve_voice_mode)) == ["cfg", "name"]


# ---------------------------------------------------------------------------
# 2. CLI 入口（README §2/§3）
# ---------------------------------------------------------------------------

class TestCliEntry:
    def test_pyproject_declares_voice_bff(self):
        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert data["project"]["scripts"]["voice-bff"] == "voice_skill.speak:main"

    def test_installed_metadata_entry(self):
        eps = importlib.metadata.entry_points(group="console_scripts")
        vals = {e.name: e.value for e in eps}
        if "voice-bff" not in vals:
            pytest.skip("voice-bff 未安装（无 dist-info/egg-info），仅校验 pyproject 声明")
        assert vals["voice-bff"] == "voice_skill.speak:main"


# ---------------------------------------------------------------------------
# 3. 八声线契约（README §4）
# ---------------------------------------------------------------------------

class TestVoiceModesContract:
    def test_exactly_eight_modes_in_order(self, cfg):
        assert list(cfg["voice_modes"]) == VOICE_MODES

    def test_each_mode_has_documented_fields(self, cfg):
        for name, m in cfg["voice_modes"].items():
            assert {"mood", "label", "desc", "rate", "pitch"} <= set(m), name
            assert name in m["label"], name

    def test_resolve_voice_mode_maps_all(self, cfg):
        for name in VOICE_MODES:
            assert speak.resolve_voice_mode(cfg, name) == cfg["voice_modes"][name]["mood"]

    def test_resolve_voice_mode_unknown_none(self, cfg):
        assert speak.resolve_voice_mode(cfg, "不存在") is None

    def test_list_voice_modes_prints_eight_lines(self, cfg):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            speak.list_voice_modes(cfg)
        body = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert len(body) == 8
        for name in VOICE_MODES:
            assert cfg["voice_modes"][name]["label"] in buf.getvalue(), name


# ---------------------------------------------------------------------------
# 4. 情绪直连契约（README §4）
# ---------------------------------------------------------------------------

class TestHormoneContract:
    def test_seven_emotion_ids_map_to_modes(self, cfg):
        for mode, hid in EMOTION_MAP.items():
            name, m = voice.voice_mode_for_emotion(cfg, {"mood_id": hid})
            assert name == mode, hid
            assert m["emotion_id"] == hid

    def test_lengmian_is_manual_without_emotion_id(self, cfg):
        assert "emotion_id" not in cfg["voice_modes"]["冷面"]

    def test_unknown_or_empty_emotion_returns_none(self, cfg):
        assert voice.voice_mode_for_emotion(cfg, {"mood_id": "nope"}) is None
        assert voice.voice_mode_for_emotion(cfg, None) is None
        assert voice.voice_mode_for_emotion(cfg, {}) is None


# ---------------------------------------------------------------------------
# 5. voice-config.json schema（README §4）
# ---------------------------------------------------------------------------

class TestVoiceConfigSchema:
    def test_top_level_keys(self, cfg):
        required = {
            "default_role", "active", "providers", "roles", "moods", "mood_pool",
            "voice_modes", "clone_styles", "voices", "role_voices", "retry",
        }
        assert required <= set(cfg)

    def test_active_schema_references_real_providers(self, cfg):
        act = cfg["active"]
        assert act["mode"] in ("auto", "primary")
        names = {p["name"] for p in cfg["providers"]}
        assert act["primary"] in names
        assert act["secondary"] in names

    def test_providers_schema(self, cfg):
        assert cfg["providers"]
        for p in cfg["providers"]:
            assert {"name", "priority", "status", "type", "desc"} <= set(p)

    def test_roles_schema(self, cfg):
        role = cfg["roles"]["default"]
        for key in ("voice_primary", "voice_secondary"):
            assert "provider" in role[key], key

    def test_moods_schema(self, cfg):
        for name, m in cfg["moods"].items():
            assert {"rate", "pitch", "label"} <= set(m), name

    def test_every_voice_mode_mood_exists_in_moods(self, cfg):
        for name, m in cfg["voice_modes"].items():
            assert m["mood"] in cfg["moods"], name

    def test_clone_styles_cover_all_modes(self, cfg):
        assert set(cfg["clone_styles"]) == set(VOICE_MODES)

    def test_mood_pool_entries_are_valid_moods(self, cfg):
        assert cfg["mood_pool"]
        for name in cfg["mood_pool"]:
            assert name in cfg["moods"], name

    def test_voices_schema(self, cfg):
        assert cfg["voices"]
        for vid, v in cfg["voices"].items():
            assert {"id", "mode", "label", "voice_desc", "style"} <= set(v), vid
            assert v["id"] == vid

    def test_retry_schema(self, cfg):
        assert {"max_attempts", "delay_seconds"} <= set(cfg["retry"])


# ---------------------------------------------------------------------------
# 6. 配置三段式优先级（README §4/§7）
# ---------------------------------------------------------------------------

class TestConfigPrecedence:
    def test_voice_config_path_beats_voice_home(self, monkeypatch, tmp_path):
        (tmp_path / "voice-config.json").write_text('{"marker": "HOME"}', encoding="utf-8")
        explicit = tmp_path / "explicit.json"
        explicit.write_text('{"marker": "PATH"}', encoding="utf-8")
        monkeypatch.setenv("VOICE_HOME", str(tmp_path))
        monkeypatch.setenv("VOICE_CONFIG_PATH", str(explicit))
        assert voice.load_config()["marker"] == "PATH"

    def test_voice_home_beats_package_default(self, monkeypatch, tmp_path):
        (tmp_path / "voice-config.json").write_text('{"marker": "HOME"}', encoding="utf-8")
        monkeypatch.setenv("VOICE_HOME", str(tmp_path))
        assert voice.load_config()["marker"] == "HOME"

    def test_package_default_when_unset(self, cfg):
        assert cfg["active"]["primary"] == "edge"


# ---------------------------------------------------------------------------
# 7. speak() 文档化行为（README §4）
# ---------------------------------------------------------------------------

class TestSpeakDocumentedBehavior:
    def test_empty_text_raises_valueerror(self):
        with pytest.raises(ValueError):
            voice.speak("   ")

    def test_returns_out_path_string(self, monkeypatch, tmp_path):
        out = tmp_path / "o.mp3"
        monkeypatch.setattr(voice, "_speak_mimo_clone", lambda *a, **k: True)
        assert voice.speak("你好", out=out) == str(out)

    def test_all_providers_fail_raises_runtimeerror(self, monkeypatch, tmp_path):
        monkeypatch.setattr(voice, "_speak_mimo_clone", lambda *a, **k: False)
        monkeypatch.setattr(voice, "_speak_edge", lambda *a, **k: False)
        with pytest.raises(RuntimeError):
            voice.speak("你好", out=tmp_path / "o.mp3")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
