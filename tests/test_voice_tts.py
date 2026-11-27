#!/usr/bin/env python3
"""voice 克隆声线链单元测试（单元测试，不触网、不调真实 API）。

覆盖（voice_skill.voice + voice_skill.speak）：
1. mimo_clone_tts：ref 缺失 / MIMO key 缺失 / openai 未装 / ref 超 7.5MB /
   API 异常 / ffmpeg 转码失败 → False；成功链路（mock OpenAI + ffmpeg）→ True 且
   base64 解码 wav 落盘 → ffmpeg 转 mp3 → 清理 tmp。
2. _provider_order：显式 provider 只用它；active auto → primary+secondary；mode=primary 只 primary。
3. _speak_mimo_clone：缺 ref 回落 False 且不调 mimo_clone_tts；正常走 tts + ffmpeg_speedup；
   ref 路径 expanduser 展开。
4. speak.py CLI：未知声线返回 2；--voice-mode 与 --mood（含 = 形式）冲突返回 2；
   透传 --mood；--auto-mood 被移除；--style 追加透传。
5. speak() 主链路：mimo-clone 失败 → edge 兜底；显式 provider 只用它；
   全失败抛 RuntimeError；text 空抛 ValueError。

运行：python3 -m pytest tests/ -q
"""
import base64
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import voice_skill.voice as voice_mod
import voice_skill.speak as speak_mod

# 最小 mock 配置（结构与真实 voice-config.json 一致，仅供测试）
VOICE_CFG = {
    "active": {"mode": "auto", "primary": "mimo-clone", "secondary": "edge"},
    "roles": {
        "default": {
            "voice_primary": {
                "provider": "mimo-clone",
                "ref": "~/voice-test/refs/example-reference.mp3",
                "speed": 1.0,
                "label": "克隆",
            },
            "voice_secondary": {
                "provider": "edge",
                "voice": "zh-CN-XiaoxiaoNeural",
                "rate": "+0%",
                "pitch": "+0Hz",
                "label": "晓晓edge",
            },
        }
    },
    "moods": {
        "default": {"rate": "+0%", "pitch": "+0Hz", "label": "原味"},
        "温柔": {"rate": "+0%", "pitch": "+5Hz", "label": "温柔"},
    },
    "voice_modes": {"温柔": {"mood": "温柔", "label": "🌙 温柔"}},
    "clone_styles": {"温柔": "请用温柔柔和的语气朗读，轻声细语。"},
}

FAKE_WAV = b"RIFFfakewavdata"  # base64 解码后的假 wav 字节


def _install_fake_openai(audio_b64=None, fail=None, finish_reason=None):
    """往 sys.modules 注入假 openai 模块。返回 (fake_openai_cls, restore_fn)。

    - audio_b64: chat.completions.create 返回的 audio.data（None 表示调用即失败）；
      传 list 时按调用顺序逐个弹出（长文分段合成用）
    - fail: 非 None 时 create 抛该异常
    - finish_reason: choices[0].finish_reason（"length" 模拟被截断）
    """
    class _FakeAudio:
        def __init__(self, data):
            self.data = data

    class _FakeMessage:
        def __init__(self, audio):
            self.audio = audio

    class _FakeChoice:
        def __init__(self, msg):
            self.message = msg
            self.finish_reason = finish_reason

    class _FakeCompletion:
        def __init__(self, b64):
            self.choices = [_FakeChoice(_FakeMessage(_FakeAudio(b64)))]

    class _FakeCompletions:
        def __init__(self):
            self.last_kwargs = None

        def create(self, **kwargs):
            if fail is not None:
                raise fail
            self.last_kwargs = kwargs
            payload = audio_b64.pop(0) if isinstance(audio_b64, list) else audio_b64
            return _FakeCompletion(payload)

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        instances = []

        def __init__(self, api_key=None, base_url=None):
            self.api_key = api_key
            self.base_url = base_url
            self.chat = _FakeChat()
            _FakeOpenAI.instances.append(self)

    mod = types.ModuleType("openai")
    mod.OpenAI = _FakeOpenAI
    prev = sys.modules.get("openai")
    sys.modules["openai"] = mod

    def restore():
        if prev is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = prev

    return _FakeOpenAI, restore


class TestMimoCloneTts(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.ref = self.tmp / "ref.mp3"
        self.ref.write_bytes(b"FAKEREF" * 64)
        self.out = self.tmp / "out.mp3"
        # 真实契约：_load_mimo_key() 有 key 时 os.environ["MIMO_API_KEY"] 必已设置。
        # 用 patch.dict 注入 env 走真实 _load_mimo_key 读取路径（不触 用户家目录 .env）。
        self._env_patch = mock.patch.dict(os.environ, {"MIMO_API_KEY": "sk-test"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def tearDown(self):
        self._td.cleanup()

    def _run(self, text="你好", ref=None, out=None, style="", **patch_ctx):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ok = voice_mod.mimo_clone_tts(text, ref or self.ref, out or self.out, style)
        return ok, buf.getvalue()

    def test_ref_missing_returns_false(self):
        ok, err = self._run(ref=self.tmp / "nope.mp3")
        self.assertFalse(ok)
        self.assertIn("参考音频不存在", err)

    def test_missing_key_returns_false(self):
        with mock.patch.object(voice_mod, "_load_mimo_key", return_value=None):
            ok, err = self._run()
        self.assertFalse(ok)
        self.assertIn("缺少 MIMO_API_KEY", err)
    def test_openai_not_installed_returns_false(self):
        with mock.patch.dict(sys.modules, {"openai": None}):
            ok, err = self._run()
        self.assertFalse(ok)
        self.assertIn("openai 未安装", err)

    def test_ref_too_large_returns_false(self):
        big = self.tmp / "big.mp3"
        with open(big, "wb") as f:
            f.truncate(7_500_001)  # 稀疏文件，逻辑大小超上限
        fake_cls, restore = _install_fake_openai(audio_b64=base64.b64encode(FAKE_WAV).decode())
        self.addCleanup(restore)
        ok, err = self._run(ref=big)
        self.assertFalse(ok)
        self.assertIn("超 7.5MB", err)
        self.assertEqual(fake_cls.instances, [])  # 未创建客户端即失败

    def test_api_error_returns_false(self):
        fake_cls, restore = _install_fake_openai(fail=RuntimeError("api down"))
        self.addCleanup(restore)
        ok, err = self._run(style="风格")
        self.assertFalse(ok)
        self.assertIn("voiceclone ❌", err)

    def test_ffmpeg_failure_returns_false_and_cleans_tmp(self):
        fake_cls, restore = _install_fake_openai(audio_b64=base64.b64encode(FAKE_WAV).decode())
        self.addCleanup(restore)

        def fake_run(cmd, **kwargs):
            return types.SimpleNamespace(returncode=1, stderr="boom")

        with mock.patch.object(voice_mod, "subprocess", types.SimpleNamespace(run=fake_run)):
            ok, err = self._run()
        self.assertFalse(ok)
        self.assertIn("转码", err)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.with_suffix(".tmp.wav").exists())  # finally 清理

    def test_success_path_decodes_wav_and_converts(self):
        b64 = base64.b64encode(FAKE_WAV).decode()
        fake_cls, restore = _install_fake_openai(audio_b64=b64)
        self.addCleanup(restore)

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["tmp_bytes"] = Path(cmd[3]).read_bytes()  # ffmpeg -i <tmp>
            Path(cmd[-1]).write_bytes(b"ID3MP3FAKE")
            return types.SimpleNamespace(returncode=0, stderr="")

        with mock.patch.object(voice_mod, "subprocess", types.SimpleNamespace(run=fake_run)):
            ok, err = self._run(style="风格文案")

        self.assertTrue(ok, err)
        self.assertTrue(self.out.exists())
        self.assertEqual(self.out.read_bytes(), b"ID3MP3FAKE")  # ffmpeg 产物
        self.assertFalse(self.out.with_suffix(".tmp.wav").exists())  # tmp 已清

        # base64 解码落盘 wav 的字节 == 传给 OpenAI 的假音频
        self.assertEqual(captured["tmp_bytes"], FAKE_WAV)
        # ffmpeg 参数契约：-y -i tmp -c:a libmp3lame -b:a 128k out
        self.assertEqual(captured["cmd"][0], "ffmpeg")
        self.assertEqual(captured["cmd"][2], "-i")
        self.assertEqual(captured["cmd"][-1], str(self.out))

        # OpenAI 调用契约
        client = fake_cls.instances[0]
        self.assertEqual(client.api_key, "sk-test")
        self.assertEqual(client.base_url, "https://api.xiaomimimo.com/v1")
        kwargs = client.chat.completions.last_kwargs
        self.assertEqual(kwargs["model"], "mimo-v2.5-tts-voiceclone")
        self.assertEqual(
            kwargs["messages"],
            [{"role": "user", "content": "风格文案"}, {"role": "assistant", "content": "你好"}],
        )
        self.assertEqual(kwargs["audio"]["format"], "wav")
        # voice payload = data:audio/mpeg;base64,<ref 内容>
        self.assertTrue(kwargs["audio"]["voice"].startswith("data:audio/mpeg;base64,"))
        payload = kwargs["audio"]["voice"].split(";base64,", 1)[1]
        self.assertEqual(base64.b64decode(payload), self.ref.read_bytes())


    def test_truncated_response_returns_false(self):
        """finish_reason=length：模型没念完，半截音频必须判失败（实测会出现在长文上）。"""
        b64 = base64.b64encode(FAKE_WAV).decode()
        fake_cls, restore = _install_fake_openai(audio_b64=b64, finish_reason="length")
        self.addCleanup(restore)

        with mock.patch.object(voice_mod, "subprocess", types.SimpleNamespace(run=mock.Mock())) as sp:
            ok, err = self._run()

        self.assertFalse(ok)
        self.assertIn("截断", err)
        self.assertFalse(self.out.exists())
        sp.run.assert_not_called()  # 截断音频不落盘、不转码


class TestSplitTtsText(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(voice_mod.split_tts_text("你好"), ["你好"])
        self.assertEqual(voice_mod.split_tts_text("   "), [])

    def test_long_poem_split_without_loss(self):
        text = (
            "再别康桥。轻轻的我走了，正如我轻轻的来；我轻轻的招手，作别西天的云彩。"
            "那河畔的金柳，是夕阳中的新娘；波光里的艳影，在我的心头荡漾。"
            "软泥上的青荇，油油的在水底招摇；在康河的柔波里，我甘心做一条水草。"
            "那榆荫下的一潭，不是清泉，是天上虹；揉碎在浮藻间，沉淀着彩虹似的梦。"
            "寻梦？撑一支长篙，向青草更青处漫溯；满载一船星辉，在星辉斑斓里放歌。"
            "但我不能放歌，悄悄是别离的笙箫；夏虫也为我沉默，沉默是今晚的康桥。"
            "悄悄的我走了，正如我悄悄的来；我挥一挥衣袖，不带走一片云彩。"
        )
        chunks = voice_mod.split_tts_text(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= voice_mod.CLONE_SEGMENT_MAX_CHARS for c in chunks))
        self.assertEqual("".join(chunks), text)  # 不丢字

    def test_sentence_over_limit_is_hard_split(self):
        text = "啊" * 250
        chunks = voice_mod.split_tts_text(text, max_chars=100)

        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        self.assertEqual("".join(chunks), text)


class TestMimoCloneLongText(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.ref = self.tmp / "ref.mp3"
        self.ref.write_bytes(b"FAKEREF")
        self.out = self.tmp / "out.mp3"
        self._env_patch = mock.patch.dict(os.environ, {"MIMO_API_KEY": "sk-test"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def tearDown(self):
        self._td.cleanup()

    def test_long_text_segments_then_concat(self):
        text = "再别康桥。轻轻的我走了，正如我轻轻的来；我轻轻的招手，作别西天的云彩。" * 6
        segments = voice_mod.split_tts_text(text)
        self.assertGreater(len(segments), 1)

        payloads = [base64.b64encode(f"RIFF{i}".encode()).decode() for i in range(len(segments))]
        fake_cls, restore = _install_fake_openai(audio_b64=payloads)
        self.addCleanup(restore)

        captured = {}

        def fake_run(cmd, **kwargs):
            if "-f" in cmd and "concat" in cmd:
                list_path = Path(cmd[cmd.index("-i") + 1])
                captured["list"] = list_path.read_text(encoding="utf-8")
                Path(cmd[-1]).write_bytes(b"JOINEDWAV")
                return types.SimpleNamespace(returncode=0, stderr="")
            Path(cmd[-1]).write_bytes(b"ID3MP3FAKE")
            return types.SimpleNamespace(returncode=0, stderr="")

        with mock.patch.object(voice_mod, "subprocess", types.SimpleNamespace(run=fake_run)):
            ok = voice_mod.mimo_clone_tts(text, self.ref, self.out, "")

        self.assertTrue(ok)
        self.assertEqual(self.out.read_bytes(), b"ID3MP3FAKE")
        # 每段都单独请求，且单段都不超过上限
        calls = fake_cls.instances[0].chat.completions
        self.assertEqual(len(captured["list"].strip().splitlines()), len(segments))
        self.assertNotEqual(calls.last_kwargs["messages"][1]["content"], text)
        # 分段临时文件已清理
        self.assertEqual(list(self.tmp.glob(".voiceclone_*")), [])


class TestProviderOrder(unittest.TestCase):
    def test_explicit_provider_uses_only_it(self):
        self.assertEqual(voice_mod._provider_order({}, "mimo"), ["mimo", None])

    def test_auto_mode_primary_then_secondary(self):
        cfg = {"active": {"mode": "auto", "primary": "mimo-clone", "secondary": "edge"}}
        self.assertEqual(voice_mod._provider_order(cfg, None), ["mimo-clone", "edge", None])

    def test_primary_mode_skips_secondary(self):
        cfg = {"active": {"mode": "primary", "primary": "mimo-clone", "secondary": "edge"}}
        self.assertEqual(voice_mod._provider_order(cfg, None), ["mimo-clone", None])

    def test_empty_active_uses_defaults(self):
        self.assertEqual(voice_mod._provider_order({}, None), ["edge", "mimo", None])


class TestListProviders(unittest.TestCase):
    def _cfg(self, mode="auto"):
        return {
            "active": {"mode": mode, "primary": "mimo-clone", "secondary": "edge"},
            "providers": [
                {"name": "mimo-clone", "priority": 0, "status": "standby", "desc": "克隆"},
                {"name": "mimo", "priority": 1, "status": "standby", "desc": "预置"},
                {"name": "edge", "priority": 2, "status": "active", "desc": "兜底"},
            ],
            "roles": {"default": {"voice_primary": {}, "voice_secondary": {}}},
        }

    def _line_for(self, out, name):
        return next(ln for ln in out.splitlines() if f"] {name} " in ln)

    def test_icons_follow_active_not_static_status(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            voice_mod.list_providers(self._cfg(mode="auto"))
        out = buf.getvalue()
        # primary 用 ✅、secondary 用 ⚠、其余 ❌；与静态 status 字段无关
        self.assertIn("✅", self._line_for(out, "mimo-clone"))
        self.assertIn("⚠", self._line_for(out, "edge"))
        self.assertIn("❌", self._line_for(out, "mimo"))

    def test_primary_mode_marks_secondary_inactive(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            voice_mod.list_providers(self._cfg(mode="primary"))
        out = buf.getvalue()
        self.assertIn("✅", self._line_for(out, "mimo-clone"))
        self.assertIn("❌", self._line_for(out, "edge"))


class TestSpeakMimoClone(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.out = self.tmp / "out.mp3"

    def tearDown(self):
        self._td.cleanup()

    def _role_cfg(self, ref=None, speed=1.3):
        entry = {"provider": "mimo-clone"}
        if speed is not None:
            entry["speed"] = speed
        if ref is not None:
            entry["ref"] = ref
        return {"voice_primary": entry, "voice_secondary": {"provider": "edge"}}

    def test_missing_ref_returns_false_and_skips_tts(self):
        buf = io.StringIO()
        with mock.patch.object(voice_mod, "mimo_clone_tts") as mc, \
                mock.patch.object(voice_mod, "ffmpeg_speedup") as sp, \
                contextlib.redirect_stderr(buf):
            ok = voice_mod._speak_mimo_clone("你好", self.out, 1.0, self._role_cfg(), {})
        self.assertFalse(ok)
        self.assertIn("缺 ref", buf.getvalue())
        mc.assert_not_called()
        sp.assert_not_called()

    def test_normal_path_calls_tts_and_speedup(self):
        ref = str(self.tmp / "ref.mp3")
        Path(ref).write_bytes(b"R")
        cfg = {"clone_styles": {"温柔": "温柔文案"}}
        with mock.patch.object(voice_mod, "mimo_clone_tts", return_value=True) as mc, \
                mock.patch.object(voice_mod, "ffmpeg_speedup", return_value=True) as sp:
            ok = voice_mod._speak_mimo_clone("你好", self.out, 1.0, self._role_cfg(ref=ref), cfg, mood_name="温柔")
        self.assertTrue(ok)
        mc.assert_called_once_with("你好", Path(ref), self.out, "温柔文案")
        sp.assert_called_once_with(self.out, 1.3)  # 角色配置 speed 优先

    def test_style_text_appended_to_clone_style(self):
        ref = str(self.tmp / "ref.mp3")
        Path(ref).write_bytes(b"R")
        cfg = {"clone_styles": {"温柔": "温柔文案"}}
        with mock.patch.object(voice_mod, "mimo_clone_tts", return_value=True) as mc, \
                mock.patch.object(voice_mod, "ffmpeg_speedup", return_value=True):
            ok = voice_mod._speak_mimo_clone("你好", self.out, 1.0, self._role_cfg(ref=ref), cfg, mood_name="温柔", style_text="语速再慢一点")
        self.assertTrue(ok)
        mc.assert_called_once_with("你好", Path(ref), self.out, "温柔文案\n语速再慢一点")

    def test_ref_expanduser_applied(self):
        with mock.patch.object(voice_mod.os.path, "expanduser", return_value="/tmp/expanded-ref.mp3") as ex, \
                mock.patch.object(voice_mod, "mimo_clone_tts", return_value=True) as mc, \
                mock.patch.object(voice_mod, "ffmpeg_speedup", return_value=True):
            ok = voice_mod._speak_mimo_clone("你好", self.out, 1.0, self._role_cfg(ref="~/refs/x.mp3"), {})
        self.assertTrue(ok)
        ex.assert_called_once_with("~/refs/x.mp3")
        mc.assert_called_once_with("你好", Path("/tmp/expanded-ref.mp3"), self.out, "")

    def test_speed_fallback_to_param(self):
        ref = str(self.tmp / "ref.mp3")
        Path(ref).write_bytes(b"R")
        with mock.patch.object(voice_mod, "mimo_clone_tts", return_value=True), \
                mock.patch.object(voice_mod, "ffmpeg_speedup", return_value=True) as sp:
            # 角色配置无 speed 字段 → 用入参 speed
            ok = voice_mod._speak_mimo_clone("你好", self.out, 0.9, self._role_cfg(ref=ref, speed=None), {})
        self.assertTrue(ok)
        sp.assert_called_once_with(self.out, 0.9)


class TestSpeakCli(unittest.TestCase):
    """speak.py CLI 行为（patch voice_skill.voice.main 假实现，不真实合成）。"""

    def setUp(self):
        self.called_argv = None

        def fake_main():
            self.called_argv = list(sys.argv)
            return 0

        self._voice_main_patch = mock.patch.object(voice_mod, "main", new=fake_main)
        self._voice_main_patch.start()

    def tearDown(self):
        self._voice_main_patch.stop()

    def _run_main(self, argv):
        buf = io.StringIO()
        with mock.patch.object(speak_mod, "load_config", return_value=VOICE_CFG), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stderr(buf):
            rc = speak_mod.main()
        return rc, buf.getvalue()

    def test_unknown_voice_mode_returns_2(self):
        rc, err = self._run_main(["speak.py", "--text", "你好", "--voice-mode", "不存在"])
        self.assertEqual(rc, 2)
        self.assertIn("未知声线", err)
        self.assertIsNone(self.called_argv)  # 未透传到 voice.main

    def test_voice_mode_conflicts_with_mood(self):
        rc, err = self._run_main(["speak.py", "--text", "你好", "--voice-mode", "温柔", "--mood", "冷面"])
        self.assertEqual(rc, 2)
        self.assertIn("二选一", err)

    def test_voice_mode_conflicts_with_mood_equals_form(self):
        rc, err = self._run_main(["speak.py", "--text", "你好", "--voice-mode", "温柔", "--mood=冷面"])
        self.assertEqual(rc, 2)
        self.assertIn("二选一", err)

    def test_voice_mode_translates_to_mood(self):
        rc, _ = self._run_main(["speak.py", "--text", "你好", "--voice-mode", "温柔"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.called_argv[1:], ["--text", "你好", "--mood", "温柔"])
        self.assertNotIn("--voice-mode", self.called_argv)

    def test_auto_mood_removed_when_voice_mode(self):
        rc, _ = self._run_main(["speak.py", "--text", "你好", "--voice-mode", "温柔", "--auto-mood"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.called_argv[1:], ["--text", "你好", "--mood", "温柔"])
        self.assertNotIn("--auto-mood", self.called_argv)

    def test_style_appended_without_voice_mode(self):
        rc, _ = self._run_main(["speak.py", "--text", "你好", "--style", "语速慢一点"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.called_argv[1:], ["--text", "你好", "--style", "语速慢一点"])

    def test_style_appended_with_voice_mode(self):
        rc, _ = self._run_main(["speak.py", "--text", "你好", "--voice-mode", "温柔", "--style", "语速慢一点"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.called_argv[1:], ["--text", "你好", "--mood", "温柔", "--style", "语速慢一点"])


class TestSpeakMain(unittest.TestCase):
    """speak() 主链路（mock load_config 与 _speak_* 子函数，不触网）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.out = self.tmp / "out.mp3"
        self._cfg_patch = mock.patch.object(voice_mod, "load_config", return_value=VOICE_CFG)
        self._cfg_patch.start()
        self.addCleanup(self._cfg_patch.stop)

    def tearDown(self):
        self._td.cleanup()

    def test_empty_text_raises_valueerror(self):
        with self.assertRaises(ValueError):
            voice_mod.speak("   ")

    def test_fallback_mimo_clone_then_edge(self):
        with mock.patch.object(voice_mod, "_speak_mimo_clone", return_value=False) as m1, \
                mock.patch.object(voice_mod, "_speak_edge", return_value=True) as m2:
            path = voice_mod.speak("你好", mood="温柔", out=self.out)
        self.assertEqual(path, str(self.out))
        m1.assert_called_once()
        m2.assert_called_once()  # 只有 mimo-clone 失败才会轮到 edge
        # 传给 _speak_mimo_clone 的参数契约（text, out, speed, role_cfg, cfg, mood_name, style）
        self.assertEqual(m1.call_args.args[:3], ("你好", Path(self.out), 1.0))
        self.assertEqual(m1.call_args.args[5], "温柔")

    def test_explicit_provider_uses_only_it(self):
        with mock.patch.object(voice_mod, "_speak_mimo_clone", return_value=True) as m1, \
                mock.patch.object(voice_mod, "_speak_edge", return_value=True) as m2:
            path = voice_mod.speak("你好", provider="mimo-clone", mood="温柔", out=self.out)
        self.assertEqual(path, str(self.out))
        m1.assert_called_once()
        m2.assert_not_called()

    def test_all_fail_raises_runtimeerror(self):
        with mock.patch.object(voice_mod, "_speak_mimo_clone", return_value=False), \
                mock.patch.object(voice_mod, "_speak_edge", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                voice_mod.speak("你好", mood="温柔", out=self.out)
        self.assertIn("mimo-clone", str(ctx.exception))

    def test_unknown_provider_raises_runtimeerror(self):
        with self.assertRaises(RuntimeError):
            voice_mod.speak("你好", provider="nope", mood="温柔", out=self.out)


class TestEdgeFailureCleanup(unittest.TestCase):
    """edge 合成失败时不许留下 0 字节半成品（真实接口会先建文件再报错）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.out = self.tmp / "o.mp3"

    def tearDown(self):
        self._td.cleanup()

    def _fake_edge(self, payload=b"", error=None):
        mod = types.ModuleType("edge_tts")

        class _Communicate:
            def __init__(self, *a, **k):
                pass

            async def save(self, path):  # 真实接口是 async：先建文件再报错
                Path(path).write_bytes(payload)
                if error is not None:
                    raise error

        mod.Communicate = _Communicate
        return mod

    def test_failure_removes_empty_output(self):
        fake = self._fake_edge(payload=b"", error=RuntimeError("NoAudioReceived"))
        buf = io.StringIO()
        with mock.patch.dict(sys.modules, {"edge_tts": fake}), \
                mock.patch("time.sleep"), contextlib.redirect_stderr(buf):
            ok = voice_mod.edge_tts("你好", self.out)
        self.assertFalse(ok)
        self.assertIn("第 2 次失败", buf.getvalue())  # 重试 1 次后才放弃
        self.assertFalse(self.out.exists())  # 空文件已清理

    def test_success_keeps_output(self):
        fake = self._fake_edge(payload=b"ID3OK")
        with mock.patch.dict(sys.modules, {"edge_tts": fake}), \
                mock.patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            ok = voice_mod.edge_tts("你好", self.out)
        self.assertTrue(ok)
        self.assertEqual(self.out.read_bytes(), b"ID3OK")


if __name__ == "__main__":
    unittest.main()


class TestCloneSegmentationGuard(unittest.TestCase):
    """60s 截断的两道闸：长文必须分段；分段失效要报错而不是交半截。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.ref = self.tmp / "ref.mp3"
        self.ref.write_bytes(b"FAKEREF" * 8)
        self.out = self.tmp / "out.mp3"
        self._env_patch = mock.patch.dict(os.environ, {"MIMO_API_KEY": "sk-test"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def tearDown(self):
        self._td.cleanup()

    def test_long_text_not_split_aborts(self):
        """分段逻辑退化（长文只切出 1 段）→ 直接失败，绝不单次合成后当成品。"""
        long_text = "啊" * 200
        buf = io.StringIO()
        with mock.patch.object(voice_mod, "split_tts_text", return_value=[long_text]), \
                mock.patch.object(voice_mod, "_clone_synthesize") as synth, \
                contextlib.redirect_stderr(buf):
            ok = voice_mod.mimo_clone_tts(long_text, self.ref, self.out, "")
        self.assertFalse(ok)
        self.assertIn("长文没有被分段", buf.getvalue())
        synth.assert_not_called()  # 连 API 都不该打
        self.assertFalse(self.out.exists())

    def test_short_text_single_segment_allowed(self):
        """短文本单段是正常路径，不受守卫影响（失败原因是缺 openai，不是守卫）。"""
        buf = io.StringIO()
        with mock.patch.dict(sys.modules, {"openai": None}), contextlib.redirect_stderr(buf):
            ok = voice_mod.mimo_clone_tts("你好", self.ref, self.out, "")
        self.assertFalse(ok)
        self.assertNotIn("长文没有被分段", buf.getvalue())


class TestWavDuration(unittest.TestCase):
    def _wav(self, seconds: float, rate: int = 24000) -> bytes:
        import io as _io
        import wave

        buf = _io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\x00\x00" * int(rate * seconds))
        return buf.getvalue()

    def test_duration_from_header(self):
        self.assertAlmostEqual(voice_mod.wav_duration(self._wav(2.0)), 2.0, places=2)

    def test_garbage_returns_none(self):
        self.assertIsNone(voice_mod.wav_duration(b"not-a-wav"))

    def test_probe_duration_missing_file_returns_none(self):
        # 不存在/不可解析 → None，不抛
        self.assertIsNone(voice_mod.probe_duration(Path("/nonexistent/x.mp3")))
