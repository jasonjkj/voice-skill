#!/usr/bin/env python3
"""speak.py — 语音子技能入口（console `voice-bff`）。

封装 voice.py（TTS 引擎），新增 --voice-mode（八声线：🌙温柔/🔥魅惑/🧠清醒/❄️冷面/⚡热烈/😋俏皮/🌘深沉/😴慵懒）。
把 voice-mode 解析成对应 mood 参数后透传给 voice.py。

用法：
  speak.py --text "你好，我在" --voice-mode 温柔 --out /tmp/out.mp3
  speak.py --text "去做。" --voice-mode 冷面 --out /tmp/out.mp3
  speak.py --text "早安" --auto-mood --out /tmp/out.mp3   # 透传 voice.py
  speak.py --list-voice-modes

其余参数（--role/--provider/--voice/--speed/--list-*）原样透传 voice.py。
不指定任何声线 → 若设了 VOICE_MOOD_JSON 则读该情绪状态文件，否则用 default 档。
"""
from __future__ import annotations

import argparse
import sys

from .voice import load_config


def list_voice_modes(cfg: dict) -> None:
    modes = cfg.get("voice_modes", {})
    for name, m in modes.items():
        print(f"  {m.get('label', name)} — {m.get('desc', '')} (rate={m.get('rate','+0%')} pitch={m.get('pitch','+0Hz')})")


def resolve_voice_mode(cfg: dict, name: str) -> str:
    """voice-mode → mood 名。未知声线回报 None。"""
    modes = cfg.get("voice_modes", {})
    entry = modes.get(name)
    if entry:
        return entry.get("mood", name)
    return None


def main() -> int:
    from .voice import main as voice_main  # 同包，透传引擎

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--voice-mode", help="八声线：温柔/魅惑/清醒/冷面/热烈/俏皮/深沉/慵懒（与 --mood 二选一，优先级更高）")
    parser.add_argument("--list-voice-modes", action="store_true", help="列出八声线映射")
    parser.add_argument("--style", help="克隆声线自由风格补充文本（透传给 voice.py）")
    args, rest = parser.parse_known_args()

    cfg = load_config()

    if args.list_voice_modes:
        list_voice_modes(cfg)
        return 0

    rebuild = list(rest)

    if args.voice_mode:
        mood = resolve_voice_mode(cfg, args.voice_mode)
        if mood is None:
            print(f"未知声线 {args.voice_mode}，可用：{', '.join(cfg.get('voice_modes', {}))}", file=sys.stderr)
            return 2
        # 兼容 --mood X 与 --mood=X 两种写法（裸 "--mood" in 检查会漏掉等号形式）
        has_mood = any(a == "--mood" or a.startswith("--mood=") for a in rebuild)
        if has_mood:
            print("--voice-mode 与 --mood 二选一，请只用一个", file=sys.stderr)
            return 2
        if "--auto-mood" in rebuild:
            rebuild.remove("--auto-mood")
        rebuild += ["--mood", mood]

    if args.style:
        rebuild += ["--style", args.style]

    # 替换 argv，让 voice.py 用 (脚本名 + 透传参数) 正常解析
    sys.argv = [__file__] + rebuild
    return voice_main()


if __name__ == "__main__":
    raise SystemExit(main())
