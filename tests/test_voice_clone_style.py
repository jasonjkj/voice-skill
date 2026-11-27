#!/usr/bin/env python3
"""build_clone_style 纯函数单测（不触网，stdlib unittest）。

clone 情绪注入：mimo-clone 分支把 8 档 clone_styles 文案 + --style 自由文本
合并成注入 user message 的风格指令；无匹配/无自由文本时返回空串（保持现基准）。

运行：python3 -m pytest tests/ -q
"""
import unittest

import voice_skill.voice as voice_mod

build_clone_style = voice_mod.build_clone_style

CFG = {
    "clone_styles": {
        "温柔": "请用温柔柔和的语气朗读，轻声细语。",
        "冷面": "简短果断，命令式口吻。",
    }
}


class TestBuildCloneStyle(unittest.TestCase):
    def test_mood_hit_uses_style_text(self):
        self.assertEqual(build_clone_style(CFG, "温柔"), "请用温柔柔和的语气朗读，轻声细语。")

    def test_style_text_appended_after_base(self):
        out = build_clone_style(CFG, "温柔", "语速再慢一点。")
        self.assertEqual(out, "请用温柔柔和的语气朗读，轻声细语。\n语速再慢一点。")

    def test_style_text_without_base(self):
        self.assertEqual(build_clone_style(CFG, "冷面", "短句停顿"), "简短果断，命令式口吻。\n短句停顿")

    def test_default_empty_no_match(self):
        self.assertEqual(build_clone_style(CFG, "深夜", ""), "")
        self.assertEqual(build_clone_style(CFG, "不存在", None), "")

    def test_blank_mood_fallback_default_empty(self):
        self.assertEqual(build_clone_style(CFG, "", ""), "")

    def test_no_clone_styles_key_safe(self):
        self.assertEqual(build_clone_style({}, "温柔", ""), "")


if __name__ == "__main__":
    unittest.main()
