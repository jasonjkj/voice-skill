---
name: voice
description: 多声线 TTS 发声技能。当需要把一段文字合成语音 mp3（用户说「说句话 / 发语音 / 念一段 / 朗读 / 朗诵」），或要调整说话声线（八声线：🌙温柔 / 🔥魅惑 / 🧠清醒 / ❄️冷面 / ⚡热烈 / 😋俏皮 / 🌘深沉 / 😴慵懒）时使用。**发声统一走 `voice-bff` 入口**——它带八声线与情绪注入；不要绕到底层 TTS 接口，那些接口参数窄、拿不到声线档，参考音频缺失时还会静默降级成默认音色。
---

# Voice · 多声线 TTS

把「要说什么」→「用什么声线」→「生成 mp3」三步接起来。console 入口 `voice-bff`
（= `voice_skill.speak:main`），引擎实现 `voice_skill.voice`。

## 什么时候用

- 用户要一段**音频**而不是文字：「说句话」「念一段」「朗读/朗诵 + 诗/文/词」。
  这类请求的交付物是 mp3 文件，**不要只回文本**。
- 已有文本需要换声线、换语速、批量出音频。
- 想把语音接进自己的流程（日报播报、提醒、有声内容）。

## 发声

```bash
# 基本
voice-bff --text "你好，我在" --out /tmp/out.mp3

# 指定声线
voice-bff --text "这事就这么定了" --voice-mode 冷面 --out /tmp/out.mp3

# 声线 + 自由风格补充（追加在档位文案之后）
voice-bff --text "慢慢讲" --voice-mode 温柔 --style "语速再慢一点，每句收稳" --out /tmp/out.mp3

# 长文（>120 字自动分段合成 + 拼接，避免截断）
voice-bff --text-file poem.txt --voice-mode 温柔 --out /tmp/poem.mp3

# 看声线表
voice-bff --list-voice-modes
```

生成后把 mp3 作为文件/附件交付（按运行环境的方式），不要只贴路径。

### Python 直接调用

```python
from voice_skill.voice import speak

path = speak("你好，我在")                     # 默认声线
path = speak("这事就这么定了", mood="冷面")     # 指定情绪
path = speak("通知你一声", provider="edge")     # 强制兜底线
```

- 返回 mp3 路径（`str`）。
- 不传 `mood`/`auto_mood` → 若设了 `VOICE_MOOD_JSON` 读它的 `mood` 段定声线，否则用 `default` 档。
- `out` 不传 → `VOICE_HOME/out/tts_<时间戳>.mp3`。
- 全部 provider 失败抛 `RuntimeError`；空文本抛 `ValueError`。

## 八声线

| 声线 | `emotion_id` | rate | pitch | 效果 |
|------|--------------|------|-------|------|
| 🌙 温柔 | tender | +0% | +5Hz | 语速舒缓，语气柔和 |
| 🔥 魅惑 | teasing | +15% | +8Hz | 语速略快，尾音轻扬 |
| 🧠 清醒 | sharp | +0% | -15Hz | 语速平稳，吐字干脆 |
| ❄️ 冷面 | （手动） | +20% | -20Hz | 简短果断，音高偏低 |
| ⚡ 热烈 | fierce | +20% | +5Hz | 语速偏快，语气饱满 |
| 😋 俏皮 | playful | +15% | +10Hz | 语速轻快，尾音上挑 |
| 🌘 深沉 | deep | -10% | -10Hz | 语速偏慢，音高偏低 |
| 😴 慵懒 | lazy | -20% | +0Hz | 拖尾带倦，节奏松弛 |

选声线的判断顺序：**显式指定 > `--auto-mood` 随机 > 外部情绪 JSON 直连 > `default`**。

## provider 主备

在配置的 `active` 段（默认 `primary: edge`、`secondary: mimo-clone`）：

```json
"active": {
  "mode": "auto",              // auto：主失败自动切备用；primary：只用主
  "primary": "mimo-clone",     // 克隆声线（需参考音频 + MIMO_API_KEY）
  "secondary": "edge"          // 微软 edge（免费，无需 key）
}
```

改完即生效，不用重启。调用时不指定 `--provider` 就按这个顺序回退。
edge 免费接口高频会限流，内置 1 次 2s 重试。

## 声线约定

- `--voice-mode` 与 `--mood` 二选一，`--voice-mode` 优先；`--auto-mood` 与 `--voice-mode` 同用时空掉随机。
- `rate`/`pitch` 只对 edge / mimo 生效；克隆档靠 `clone_styles` 文案注入，物理提速用 `--speed`。
- 声线是**语气**变化，不换音色人设。想换音色：edge 用 `--voice <名字>`，
  克隆用 `refs/` 里的参考音频，voicedesign 用 `--preset`。
- 具体数值全在 `voice-config.json` 的 `voice_modes` 段，改配置即可。

## 文件

| 文件 | 说明 |
|------|------|
| `voice_skill/speak.py` | 入口 `voice-bff`：`--voice-mode` / `--list-voice-modes`，其余参数透传引擎 |
| `voice_skill/voice.py` | 引擎：provider 回退链、情绪解析、长文分段、落盘 |
| `voice_skill/mimo_provider.py` | MiMo voicedesign provider |
| `voice_skill/voice-config.json` | 配置单文件：主备 + roles + moods + voice_modes + clone_styles + voices |
| `voice_skill/refs/example-reference.mp3` | 克隆示例参考音频（可直接试听，替换成自己的） |

## 依赖

- `edge-tts`（默认 provider，免费）
- `openai`（MiMo，克隆 / voicedesign 才用）
- `ffmpeg`（系统安装：wav→mp3 转码、长文拼接、`--speed` 提速）
- `MIMO_API_KEY`（克隆 / `mimo` 才需要；环境变量 → `VOICE_HOME/.env`）

## 环境变量

| 变量 | 作用 | 缺省 |
|------|------|------|
| `VOICE_HOME` | 配置/数据根（`voice-config.json`、`refs/`、`out/`） | 包目录 |
| `VOICE_CONFIG_PATH` | 显式配置文件路径（最高优先） | 未设 |
| `VOICE_MOOD_JSON` | 外部情绪 JSON 路径（读 `mood` 段定声线） | 空（→ `default` 档） |
| `VOICE_CACHE` | 输出目录覆盖 | `VOICE_HOME/out/` |
| `VOICE_DOTENV` | 显式指定 `.env` 文件（最高优先） | 未设 |
| `MIMO_API_KEY` | MiMo key | 环境变量 → `VOICE_DOTENV` → `VOICE_HOME/.env` → 向上找仓根 `.env` |
