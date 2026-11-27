#!/usr/bin/env python3
"""voice.py — TTS 语音池统一入口（配置外展 + 情绪直连 + 声线克隆）。

provider 三档（见 voice-config.json `providers`）：
  - edge：微软 edge-tts（免费、无需 key、无需参考音频，兜底线，默认）
  - mimo-clone：小米 MiMo-V2.5-TTS-VoiceClone（参考音频零样本复刻声线，需 MIMO_API_KEY）
  - mimo：小米 MiMo-TTS voicedesign 定制声线（读 `voices` 预置，需 MIMO_API_KEY）

声线：八档 voice_modes。其中 7 档带 `emotion_id`，与外部情绪状态 JSON 的 `mood_id`
同名直连（无映射表）；冷面为手动专用档。不指定 mood 时按情绪状态文件选取。

配置：VOICE_HOME/voice-config.json（缺省回退包内 data；active 主备 + providers +
roles + moods + voice_modes + voices 音色预置）。
克隆参考音频：<ref> 依次探测 绝对/相对路径 → VOICE_HOME/<ref> → 包内 data；
包内自带 refs/example-reference.mp3，可直接试听，替换成你自己的音频即可。

用法：
  voice.py --text "你好" --out /tmp/out.mp3
  voice.py --text "你好" --mood 温柔 --out /tmp/out.mp3
  voice.py --text "你好" --auto-mood --out /tmp/out.mp3
  voice.py --text "你好" --provider edge --out /tmp/out.mp3
  voice.py --list-providers
  voice.py --list-moods
"""
from __future__ import annotations

import argparse
import base64
import importlib.resources
import json
import os
import random
import subprocess
import sys
from pathlib import Path

EDGE_VOICE = "zh-CN-XiaoxiaoNeural"

# 单段克隆合成的文本上限（字）。MiMo 偶发在 ~60s 音频处提前收尾（实测：229 字的
# 散文只合成到 60s，末尾几句直接丢失），所以长文先分段再拼接，避免半截成品。
CLONE_SEGMENT_MAX_CHARS = 120


def split_tts_text(text: str, max_chars: int = CLONE_SEGMENT_MAX_CHARS) -> list[str]:
    """把长文本切成 <= max_chars 的段：先按句末标点，再按逗号，最后硬切；不丢字。"""
    text = (text or "").strip()
    if not text:
        return []
    if max_chars < 1:
        max_chars = CLONE_SEGMENT_MAX_CHARS

    sentences: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。！？!?；;…\n":
            sentences.append(buf)
            buf = ""
    if buf:
        sentences.append(buf)

    chunks: list[str] = []
    cur = ""
    for sentence in sentences:
        while len(sentence) > max_chars:
            window = sentence[:max_chars]
            cut = max(window.rfind(sep) for sep in "，,、 ")
            cut = cut + 1 if cut >= max_chars // 3 else max_chars
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(sentence[:cut])
            sentence = sentence[cut:]
        if cur and len(cur) + len(sentence) > max_chars:
            chunks.append(cur)
            cur = ""
        cur += sentence
    if cur:
        chunks.append(cur)
    return chunks


def _home() -> Path:
    """配置/数据根：VOICE_HOME 优先（expanduser + resolve）；缺省 = 包目录。"""
    env = os.environ.get("VOICE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent


def _package_file(*parts: str) -> Path:
    """包内 data 文件（随包分发，如默认配置/参考音频）。"""
    return Path(importlib.resources.files("voice_skill").joinpath(*parts))


def _config_path() -> Path:
    """配置三段式：VOICE_CONFIG_PATH > _home()/voice-config.json > package data。"""
    env = os.environ.get("VOICE_CONFIG_PATH")
    if env:
        return Path(env).expanduser().resolve()
    home_cfg = _home() / "voice-config.json"
    if home_cfg.exists():
        return home_cfg.resolve()
    return _package_file("voice-config.json")


# 兼容符号：import 时解析一次；运行时 load_config() 每次重新解析（env 可后设）
CONFIG_PATH = _config_path()


def load_config() -> dict:
    with open(_config_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def list_providers(cfg: dict) -> None:
    for p in cfg["providers"]:
        status = "✅" if p["status"] == "active" else "❌"
        print(f"  {status} [{p['priority']}] {p['name']} — {p['desc']}")
    role_name = cfg.get("default_role", "default")
    r = cfg["roles"][role_name]
    vp = r.get("voice_primary", {})
    vs = r.get("voice_secondary", {})
    print(f"\n角色 {role_name} · 主线: {vp.get('label', '?')}")
    print(f"角色 {role_name} · 备用: {vs.get('label', '?')}")


def list_presets(cfg: dict) -> None:
    voices = cfg.get("voices", {})
    if not voices:
        print("无音色预置")
        return
    for vid, data in voices.items():
        print(f"  {vid} — {data.get('label', '')}")


def list_moods(cfg: dict) -> None:
    for name, m in cfg.get("moods", {}).items():
        print(f"  {name} — {m.get('label', '')} (rate={m.get('rate','+0%')} pitch={m.get('pitch','+0Hz')})")
    pool = cfg.get("mood_pool", [])
    print(f"\n随机池: {', '.join(pool)}")


def list_styles(cfg: dict) -> None:
    for name, s in cfg.get("clone_styles", {}).items():
        print(f"  {name} — {s}")


def pick_mood(cfg: dict, explicit: str | None, auto: bool) -> tuple[str, dict]:
    """解析情绪。explicit 优先；auto 时按时间段加权随机；否则 default。"""
    moods = cfg.get("moods", {})
    if explicit:
        if explicit in moods:
            return explicit, moods[explicit]
        print(f"未知情绪 {explicit}，回退 default", file=sys.stderr)
        return "default", moods.get("default", {})
    if auto:
        pool = cfg.get("mood_pool", [])
        # 深夜时段（22:00-05:00）偏向深夜/温柔
        hour = _hour_now()
        if 22 <= hour or hour < 5:
            pool = ["深夜", "温柔", "慵懒"] + [m for m in pool if m not in ("深夜", "温柔", "慵懒")]
        name = random.choice(pool) if pool else "default"
        m = moods.get(name, {})
        print(f"auto-mood 选中: {name} ({m.get('label','')})", file=sys.stderr)
        return name, m
    return "default", moods.get("default", {})


def _hour_now() -> int:
    import datetime
    return datetime.datetime.now().hour


def mood_from_file(path=None) -> dict | None:
    """读情绪状态 JSON 的 mood 段（可配置路径）。

    返回 mood 段 dict（{mood_id, mood, ...}）；path 缺省取 VOICE_MOOD_JSON 环境变量；
    文件缺失/损坏/无 mood 段 → None（安全退化，不抛）。
    只读，绝不写情绪状态文件。
    """
    src = os.environ.get("VOICE_MOOD_JSON") if path is None else str(path)
    if not src:
        return None
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        mood = data.get("mood")
        return mood if isinstance(mood, dict) else None
    except (OSError, ValueError):
        # 安全退化：文件权限等 OSError / 解析类异常一律返回 None
        return None


def voice_mode_for_emotion(cfg: dict, emotion_state: dict | None) -> tuple[str, dict] | None:
    """外部情绪状态(mood_id) → voice_modes 档位。

    voice_modes 每档带 `emotion_id`（tender/teasing/sharp/fierce/playful/deep/lazy），
    与情绪状态文件的 `mood_id` 同名直连，无映射表。找不到 → None。
    """
    mid = (emotion_state or {}).get("mood_id")
    if not mid:
        return None
    for name, m in cfg.get("voice_modes", {}).items():
        if m.get("emotion_id") == mid:
            return name, m
    return None


def _resolve_ref(ref: str) -> Path:
    """ref 参考音频解析：显式绝对/已存在直用；否则 _home()/ref → package data 依次探测；都无 → 原路径。"""
    p = Path(os.path.expanduser(ref))
    if p.exists():
        return p
    if not p.is_absolute():
        cand = _home() / ref
        if cand.exists():
            return cand
        pkg = _package_file(*Path(ref).parts)
        if pkg.exists():
            return pkg
    return p


def _dotenv_candidates():
    """MIMO key 的 .env 查找顺序：VOICE_DOTENV → VOICE_HOME → 包目录向上 3 层 → 用户级。

    为什么不只认 `VOICE_HOME/.env`：调用方通常把密钥放**仓根 `.env`**，
    而本包常被内联到 `vendor/<pkg>/` 这类子目录里——仓根在包目录上方几层，
    只认 `VOICE_HOME` 的表现是「key 明明配了却读不到」，克隆档静默降级成兜底音色。
    """
    explicit = os.environ.get("VOICE_DOTENV", "").strip()
    if explicit:
        yield Path(explicit).expanduser()
    yield _home() / ".env"
    here = Path(__file__).resolve().parent
    for depth in range(3):  # 包目录 → 上一级 → 再上一级
        try:
            yield here.parents[depth] / ".env"
        except IndexError:
            break
    yield Path.home() / ".config" / "voice-skill" / ".env"


def _load_mimo_key() -> str | None:
    """读 MIMO_API_KEY：环境变量 > `_dotenv_candidates()` 逐个 .env（命中即写回 env）。

    `MIMO_API_KEY=` 显式置空 = 明确不要 key（跳过所有 .env），
    用于测试隔离或「强制走兜底」——否则向上查找可能读到调用方仓根的真 key。
    """
    key = os.environ.get("MIMO_API_KEY")
    if key:
        return key
    if "MIMO_API_KEY" in os.environ:
        return None
    seen: set[Path] = set()
    for dotenv in _dotenv_candidates():
        try:
            dotenv = dotenv.resolve()
        except OSError:
            continue
        if dotenv in seen or not dotenv.exists():
            continue
        seen.add(dotenv)
        try:
            lines = dotenv.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line.startswith("MIMO_API_KEY="):
                key = line.split("=", 1)[1].strip()
                os.environ["MIMO_API_KEY"] = key
                return key
    return None


def mimo_tts(text: str, preset: str, out: Path, mood: dict | None = None, cfg: dict | None = None) -> bool:
    """调用 MiMo TTS provider（本包 `mimo_provider.py` 的 MiMoTTSProvider）合成语音。

    返回 True 成功，False 失败。声音特色（voice_desc + style voicedesign）读
    voice-config.json 的 voices。
    """
    # 读声线预置（voice-config.json 内嵌 voices，voicedesign 配置）
    voice_desc = None
    style = None
    if cfg:
        preset_data = cfg.get("voices", {}).get(preset)
        if preset_data:
            voice_desc = preset_data.get("voice_desc", "")
            style = preset_data.get("style", "")

    # 情绪注入 style
    if mood and mood.get("label"):
        mood_tip = f"\n\n当前情绪：{mood['label']}。{mood.get('hint', '')}"
        style = (style or "") + mood_tip

    if not _load_mimo_key():
        print("mimo TTS: 缺少 MIMO_API_KEY 环境变量", file=sys.stderr)
        return False

    try:
        from .mimo_provider import MiMoTTSProvider

        provider = MiMoTTSProvider()
        if not provider.is_available():
            print("mimo TTS: provider 不可用", file=sys.stderr)
            return False

        # voicedesign：voice_desc + style 合并成风格指令；其余交给模型默认
        style_instruction = "\n\n".join(filter(None, [voice_desc, style]))
        audio = provider.synthesize(
            text=text,
            voice="Chloe",
            format="mp3",
            style=style_instruction,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        print(f"mimo TTS ✅: {out}")
        return True
    except Exception as e:
        print(f"mimo TTS ❌: {e}", file=sys.stderr)
        return False


def build_clone_style(cfg: dict, mood_name: str, style_text: str | None = "") -> str:
    """把 8 档 clone_styles 文案 + 自由文本合并成克隆注入口风格的指令。

    - 命中 mood_name → 用该档文案；无命中 → 空串（保持现基准不发指令）
    - style_text 非空白 → 追加在档位文案之后（换行分隔）
    返回合并后的指令串；两者都没有 → ""（等价于不改 user message）。
    """
    styles = cfg.get("clone_styles", {})
    base = styles.get(mood_name or "", "").strip()
    extra = (style_text or "").strip()
    return "\n".join(p for p in (base, extra) if p)


def _clone_synthesize(client, text: str, mime: str, ref_b64: str, style: str) -> bytes:
    """单段克隆合成，返回 wav 字节。

    finish_reason=length 说明模型没念完就被截断（长文偶发），
    必须当失败抛出，绝不能把半截音频落盘当成品。
    """
    completion = client.chat.completions.create(
        model="mimo-v2.5-tts-voiceclone",
        messages=[
            {"role": "user", "content": style},
            {"role": "assistant", "content": text},
        ],
        audio={"format": "wav", "voice": f"data:{mime};base64,{ref_b64}"},
    )
    choice = completion.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise RuntimeError("合成被截断（finish_reason=length）")
    audio = getattr(getattr(choice, "message", None), "audio", None)
    data = getattr(audio, "data", None)
    if not data:
        raise RuntimeError("合成返回空音频")
    return base64.b64decode(data)


def mimo_clone_tts(text: str, ref: Path, out: Path, style: str = "") -> bool:
    """MiMo-V2.5-TTS-VoiceClone：参考音频零样本复刻声线并合成 text。

    - ref: 参考音频 mp3/wav（base64 后 ≤7.5MB）
    - 长文按标点分段合成，再 ffmpeg 拼接、转码成 out（mp3），避免单次超长被截断
    返回 True 成功，False 失败（缺 key / ref 不存在 / API 异常都走回退链）。
    """
    # 用统一 ref 解析（VOICE_HOME/refs → package data）
    ref_path = _resolve_ref(str(ref))
    if not ref_path.exists():
        print(f"voiceclone: 参考音频不存在 {ref_path}", file=sys.stderr)
        return False
    api_key = _load_mimo_key()
    if not api_key:
        print("voiceclone: 缺少 MIMO_API_KEY 环境变量", file=sys.stderr)
        return False

    # 先做纯本地校验（分段/体积），再决定要不要拉依赖、打 API
    segments = split_tts_text(text)
    if not segments:
        print("voiceclone: 文本为空", file=sys.stderr)
        return False
    if len(text) > CLONE_SEGMENT_MAX_CHARS and len(segments) == 1:
        # 防御：分段逻辑一旦退化就退回单次合成 → 模型在 ~60s 处收尾，成品只剩半截。
        # 宁可报错走兜底，也不要交出半截音频。
        print("voiceclone ❌: 长文没有被分段（分段逻辑异常）", file=sys.stderr)
        return False
    size = ref_path.stat().st_size
    if size > 7_500_000:
        print(f"voiceclone: 参考音频 {size}B 超 7.5MB 上限", file=sys.stderr)
        return False

    try:
        from openai import OpenAI
    except ImportError:
        print("voiceclone: openai 未安装", file=sys.stderr)
        return False
    mime = "audio/wav" if str(ref_path).lower().endswith(".wav") else "audio/mpeg"
    b64 = base64.b64encode(ref_path.read_bytes()).decode()

    try:
        client = OpenAI(api_key=api_key, base_url="https://api.xiaomimimo.com/v1")
    except Exception as e:
        print(f"voiceclone ❌: {e}", file=sys.stderr)
        return False

    out.parent.mkdir(parents=True, exist_ok=True)
    if len(segments) > 1:
        print(f"voiceclone: 长文分 {len(segments)} 段合成（{len(text)} 字）", file=sys.stderr)

    parts: list[Path] = []
    listfile: Path | None = None
    joined: Path | None = None
    try:
        try:
            for idx, segment in enumerate(segments, 1):
                data = _clone_synthesize(client, segment, mime, b64, style)
                if len(segments) == 1:
                    part = out.with_suffix(".tmp.wav")
                else:
                    part = out.with_name(f".voiceclone_{os.getpid()}_{idx}.wav")
                part.write_bytes(data)
                parts.append(part)
                if len(segments) > 1:
                    d = wav_duration(data)
                    if d:
                        print(
                            f"voiceclone: 第 {idx}/{len(segments)} 段 {d:.1f}s / {len(segment)} 字",
                            file=sys.stderr,
                        )
        except Exception as e:
            print(f"voiceclone ❌: {e}", file=sys.stderr)
            return False

        wav = parts[0]
        if len(parts) > 1:
            listfile = out.with_name(f".voiceclone_{os.getpid()}.txt")
            listfile.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
            joined = out.with_name(f".voiceclone_{os.getpid()}.joined.wav")
            r = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                 "-c", "copy", str(joined)],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0 or not joined.exists():
                print(f"voiceclone 拼接 ❌: {r.stderr.strip()}", file=sys.stderr)
                return False
            wav = joined

        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(out)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0 or not out.exists():
            print(f"voiceclone 转码 ❌: {r.stderr.strip()}", file=sys.stderr)
            return False
        print(f"voiceclone ✅: {out}（{len(segments)} 段 / {len(text)} 字）")
        return True
    finally:
        for path in [*parts, listfile, joined]:
            if path is None:
                continue
            try:
                path.unlink()
            except OSError:
                pass


def wav_duration(data: bytes) -> float | None:
    """从 WAV 字节头算时长（秒），不依赖 ffprobe。算不出 → None。

    用途：逐段核对「字数 vs 时长」，某段明显偏短就是模型提前收尾（截断）。
    """
    try:
        import io
        import wave

        with wave.open(io.BytesIO(data), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return frames / float(rate) if rate else None
    except Exception:
        return None


def probe_duration(audio: Path) -> float | None:
    """读成品音频时长（秒）。ffprobe 不可用/读失败 → None，绝不抛。"""
    import shutil

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(audio)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return None
        return float(r.stdout.strip())
    except Exception:
        # 纯诊断用途：任何异常都不该影响合成结果
        return None


def ffmpeg_speedup(audio: Path, speed: float) -> bool:
    """用 ffmpeg atempo 物理提速。返回 True 成功。"""
    if speed == 1.0:
        return True
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg 未安装，无法提速", file=sys.stderr)
        return False
    tmp = audio.with_suffix(".tmp.mp3")
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", str(audio), "-filter:a", f"atempo={speed}", str(tmp)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and tmp.exists():
            tmp.replace(audio)
            print(f"ffmpeg 提速 {speed}x ✅")
            return True
        print(f"ffmpeg 提速 ❌: {r.stderr.strip()}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"ffmpeg 提速 ❌: {e}", file=sys.stderr)
        return False


def _drop_empty(path: Path) -> None:
    """删掉 0 字节落盘：合成失败时 edge-tts 会先建空文件再报错，别留半成品。"""
    try:
        if path.exists() and path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass


def edge_tts(text: str, out: Path, rate: str = "+0%", pitch: str = "+0Hz", voice: str = EDGE_VOICE) -> bool:
    """edge-tts 免费 TTS（微软免费接口高频会 NoAudioReceived 限流，内部重试 1 次）。返回 True 成功。"""
    try:
        import edge_tts
    except ImportError:
        print("edge-tts 未安装", file=sys.stderr)
        return False

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import asyncio
        for attempt in (1, 2):
            try:
                asyncio.run(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(out)))
                if out.exists() and out.stat().st_size > 0:
                    print(f"edge TTS ✅ ({voice} rate={rate} pitch={pitch}): {out}")
                    return True
            except Exception as e:
                print(f"edge TTS 第 {attempt} 次失败: {e}", file=sys.stderr)
                if attempt == 1:
                    import time
                    time.sleep(2)  # 微软免费端短暂限流，等 2s 重试
        _drop_empty(out)
        return False
    except Exception as e:
        print(f"edge TTS ❌: {e}", file=sys.stderr)
        _drop_empty(out)
        return False


def resolve_voice(cfg: dict, role: str, explicit: str | None) -> str:
    """解析 edge 音色：--voice 显式 > role_voices 映射 > 默认晓晓。"""
    if explicit:
        return explicit
    role_voices = cfg.get("role_voices", {})
    if role in role_voices:
        return role_voices[role]
    return EDGE_VOICE


def _provider_order(cfg: dict, explicit: str | None) -> list[str | None]:
    """返回尝试的 provider 顺序（None 结尾）。

    - 显式 provider → 只用它
    - 否则按 active 段：primary 在前，mode=auto 时追加 secondary
    """
    if explicit:
        return [explicit, None]
    act = cfg.get("active", {})
    order = [act.get("primary", "edge")]
    mode = act.get("mode", "auto")
    if mode != "primary":
        order.append(act.get("secondary", "mimo"))
    order.append(None)
    return order


def default_out_path() -> Path:
    """默认落盘：VOICE_CACHE > VOICE_HOME/out/tts_YYYYMMDD_HHMMSS.mp3（不绑定固定家目录）。"""
    import datetime
    env_cache = os.environ.get("VOICE_CACHE")
    cache = Path(env_cache).expanduser() if env_cache else _home() / "out"
    cache.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return cache / f"tts_{ts}.mp3"


def _resolve_speech_mood(cfg: dict, mood: str | None, auto_mood: bool) -> tuple[str, dict]:
    """决策本次发声情绪：显式 mood > auto_mood 随机池 > 情绪状态直连 > default。

    情绪状态直连：读情绪状态 JSON 的 mood 段 mood_id，反查 voice_modes 的 emotion_id → 声线档。
    读不到/未命中 → default（安全退化，不烧流程）。
    """
    if mood:
        return pick_mood(cfg, mood, False)
    if auto_mood:
        return pick_mood(cfg, None, True)
    state = mood_from_file()
    if state:
        hit = voice_mode_for_emotion(cfg, state)
        if hit:
            name, m = hit
            print(f"情绪声线: {state.get('mood', '?')}({state.get('mood_id', '?')}) → {m.get('label', name)}", file=sys.stderr)
            return name, m
    return "default", cfg.get("moods", {}).get("default", {})


def speak(
    text: str,
    *,
    role: str | None = None,
    provider: str | None = None,
    preset: str | None = None,
    voice: str | None = None,
    mood: str | None = None,
    auto_mood: bool = False,
    style: str | None = None,
    speed: float = 1.0,
    out: Path | str | None = None,
) -> str:
    """合成语音，返回 mp3 路径。

    - provider 缺省：按 voice-config.json active 段（主→备用自动回退）
    - out 缺省：VOICE_CACHE > VOICE_HOME/out/tts_<时间戳>.mp3
    - 全部失败抛 RuntimeError
    """
    if not text or not text.strip():
        raise ValueError("text 不能为空")
    cfg = load_config()
    role = role or cfg.get("default_role", "default")
    out_path = Path(out) if out else default_out_path()
    mood_name, mood_cfg = _resolve_speech_mood(cfg, mood, auto_mood)
    role_cfg = cfg.get("roles", {}).get(role, {})

    def _done() -> str:
        # 成品时长打到 stderr：字不少却明显偏短 = 截断，一眼可见。
        dur = probe_duration(out_path)
        if dur:
            print(f"语音时长 {dur:.1f}s（{len(text)} 字）", file=sys.stderr)
        return str(out_path)

    for p in _provider_order(cfg, provider):
        if p is None:
            break
        if p == "edge":
            if _speak_edge(text, out_path, mood_cfg, role, voice, role_cfg):
                return _done()
        elif p == "mimo":
            if _speak_mimo(text, out_path, mood_cfg, preset, speed, role_cfg, cfg):
                return _done()
        elif p == "mimo-clone":
            if _speak_mimo_clone(text, out_path, speed, role_cfg, cfg, mood_name, style):
                return _done()
        else:
            print(f"未知 provider: {p}", file=sys.stderr)
        print(f"{p} 失败，尝试下一个…", file=sys.stderr)

    raise RuntimeError(f"语音合成失败（provider 顺序: {[x for x in _provider_order(cfg, provider) if x]}）")


def _edge_entry(role_cfg: dict) -> dict:
    """角色配置里 provider=edge 的条目（主/备互换后自洽）。"""
    for key in ("voice_primary", "voice_secondary"):
        e = role_cfg.get(key, {})
        if e.get("provider") == "edge":
            return e
    return {}


def _mimo_clone_entry(role_cfg: dict) -> dict:
    """角色配置里 provider=mimo-clone 的条目（主/备互换后自洽）。"""
    for key in ("voice_primary", "voice_secondary"):
        e = role_cfg.get(key, {})
        if e.get("provider") == "mimo-clone":
            return e
    return {}


def _mimo_entry(role_cfg: dict) -> dict:
    """角色配置里 provider=mimo 的条目（主/备互换后自洽）。"""
    for key in ("voice_primary", "voice_secondary"):
        e = role_cfg.get(key, {})
        if e.get("provider") == "mimo":
            return e
    return {}


def _speak_edge(text: str, out: Path, mood_cfg: dict, role: str, voice: str | None, role_cfg: dict) -> bool:
    e = _edge_entry(role_cfg)
    v = voice or e.get("voice", EDGE_VOICE)
    default_rate = e.get("rate", "+0%")
    default_pitch = e.get("pitch", "+0Hz")
    rate = mood_cfg.get("rate", default_rate)
    pitch = mood_cfg.get("pitch", default_pitch)
    print(f"edge ({v} rate={rate} pitch={pitch})…", file=sys.stderr)
    return edge_tts(text, out, rate, pitch, v)


def _speak_mimo(text: str, out: Path, mood_cfg: dict, preset: str | None, speed: float, role_cfg: dict, cfg: dict) -> bool:
    e = _mimo_entry(role_cfg)
    preset_name = preset or e.get("preset", cfg.get("default_role", "default"))
    if not mimo_tts(text, preset_name, out, mood_cfg, cfg):
        return False
    ffmpeg_speedup(out, e.get("speed", speed))
    return True


def _speak_mimo_clone(text: str, out: Path, speed: float, role_cfg: dict, cfg: dict, mood_name: str | None = None, style_text: str | None = None) -> bool:
    e = _mimo_clone_entry(role_cfg)
    ref_raw = e.get("ref", "")
    if not ref_raw:
        print("mimo-clone: 角色配置缺 ref 参考音频", file=sys.stderr)
        return False
    rp = _resolve_ref(ref_raw)
    style_instruction = build_clone_style(cfg, mood_name or "", style_text)
    print(f"voiceclone (ref={rp.name}{', style=' + mood_name if style_instruction else ''})…", file=sys.stderr)
    if not mimo_clone_tts(text, rp, out, style_instruction):
        return False
    ffmpeg_speedup(out, e.get("speed", speed))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="TTS 语音池")
    parser.add_argument("--text", help="待合成文本")
    parser.add_argument("--text-file", type=Path, help="文本文件路径")
    parser.add_argument("--role", default=None, help="角色（缺省读 voice-config.json 的 default_role）")
    parser.add_argument("--provider", help="指定 provider（mimo-clone/mimo/edge），缺省按 active 配置")
    parser.add_argument("--preset", help="指定音色预置（mimo 用）")
    parser.add_argument("--voice", help="直接指定 edge 音色名（如 zh-CN-XiaoyiNeural），覆盖角色映射")
    parser.add_argument("--mood", help="显式情绪（喜庆/开心/温柔/魅惑/清醒/冷面/热烈/傲娇/急促/慵懒/严肃/俏皮/深沉/深夜）")
    parser.add_argument("--auto-mood", action="store_true", help="随机情绪（按时间段加权）；不指定时读情绪状态文件")
    parser.add_argument("--out", type=Path, default=None, help="输出 mp3 路径（缺省 VOICE_HOME/out/tts_<时间戳>.mp3）")
    parser.add_argument("--speed", type=float, default=1.0, help="语速倍率（mimo 主线 ffmpeg 物理提速）")
    parser.add_argument("--list-providers", action="store_true", help="列出可用 provider")
    parser.add_argument("--list-presets", action="store_true", help="列出可用音色预置")
    parser.add_argument("--list-moods", action="store_true", help="列出可用情绪")
    parser.add_argument("--style", help="克隆声线自由风格补充文本（如 语速再慢一点），与 8 档 mood 叠加")
    parser.add_argument("--list-styles", action="store_true", help="列出克隆声线 8 档情绪文案")
    args = parser.parse_args()

    if args.list_providers:
        list_providers(load_config())
        return 0
    if args.list_presets:
        list_presets(load_config())
        return 0
    if args.list_moods:
        list_moods(load_config())
        return 0
    if args.list_styles:
        list_styles(load_config())
        return 0

    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text.strip()
    else:
        print("必须提供 --text 或 --text-file", file=sys.stderr)
        return 2

    try:
        path = speak(
            text=text,
            role=args.role,
            provider=args.provider,
            preset=args.preset,
            voice=args.voice,
            mood=args.mood,
            auto_mood=args.auto_mood,
            style=args.style,
            speed=args.speed,
            out=args.out,
        )
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"语音已生成: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
