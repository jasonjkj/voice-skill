# voice-skill

> 多声线 TTS 合成包：一条命令把文字变成 mp3，内置 8 档情绪声线。
> 默认走微软 edge-tts（免费、无需 key、开箱即用）；可选接入小米 MiMo 做**声线克隆**——
> 给一段参考音频，就能用那个音色念任何文本。

```bash
pip install git+https://github.com/jasonjkj/voice-skill.git
voice-bff --text "你好，我在" --voice-mode 温柔 --out /tmp/out.mp3
```

---

## 1. 特性

- **零配置可用**：默认 provider 是微软 edge-tts，不需要 key，不需要参考音频。
- **8 档情绪声线**（`--voice-mode`）：温柔 / 魅惑 / 清醒 / 冷面 / 热烈 / 俏皮 / 深沉 / 慵懒。
  同一音色的语气变化，不是换人。每档可用 `--style` 再叠一句自由文本（如「语速再慢一点」）。
- **声线克隆**（可选，需 MiMo key）：参考音频零样本复刻；长文自动分段 + 拼接，避免半截成品。
- **配置单文件外展**：provider 主备、角色、情绪表、克隆风格文案全在 `voice-config.json`，
  三段式查找（`VOICE_CONFIG_PATH` > `VOICE_HOME/voice-config.json` > 包内默认），改配置不动代码。
- **情绪可外部驱动**：设 `VOICE_MOOD_JSON` 指向一个 JSON，引擎读它的 `mood.mood_id`
  自动挑声线档（适合把语音接进自己的状态机 / 日程 / 天气之类的外部信号）。
- **纯本地调用**：CLI + Python API，无服务端、无数据库。

## 2. 安装

```bash
# 从仓库
pip install git+https://github.com/jasonjkj/voice-skill.git

# 本地开发
git clone https://github.com/jasonjkj/voice-skill.git
cd voice-skill && pip install -e .
```

可选依赖：

| 依赖 | 何时需要 | 说明 |
|---|---|---|
| `ffmpeg` | 克隆 + 提速 | 系统安装（`brew install ffmpeg` / `apt install ffmpeg`）。wav→mp3 转码与长文拼接都靠它 |
| `MIMO_API_KEY` | 只有克隆 / `mimo` 才需要 | 小米 MiMo 的 key；edge 兜底线不需要 |

装好后提供 console 脚本 **`voice-bff`**（= `voice_skill.speak:main`）。
不想装包也能直接跑模块：`python -m voice_skill.speak` / `python -m voice_skill.voice`。

## 3. 快速开始

```bash
# 最简：默认 provider（edge，晓晓女声）
voice-bff --text "你好，我在" --out /tmp/out.mp3

# 换声线
voice-bff --text "这事就这么定了" --voice-mode 冷面 --out /tmp/out.mp3

# 声线 + 自由风格补充
voice-bff --text "慢慢讲" --voice-mode 温柔 --style "语速再慢一点，每句收稳" --out /tmp/out.mp3

# 长文（>120 字自动分段合成再拼接）
voice-bff --text-file poem.txt --voice-mode 温柔 --out /tmp/poem.mp3

# 看有哪些声线 / provider / 情绪 / 音色预置
voice-bff --list-voice-modes
python -m voice_skill.voice --list-providers
python -m voice_skill.voice --list-moods
python -m voice_skill.voice --list-presets
```

### 用自己的声音（声线克隆）

1. 准备 10~30 秒干净人声音频（无背景音乐、无混响），例如 `~/voice/refs/my-voice.mp3`
2. 设 `MIMO_API_KEY`
3. 编辑配置（`VOICE_HOME/voice-config.json`，只写要改的字段即可）：

```json
{
  "active":   {"mode": "auto", "primary": "mimo-clone", "secondary": "edge"},
  "roles": {
    "default": {
      "voice_primary":   {"provider": "mimo-clone", "ref": "refs/my-voice.mp3"},
      "voice_secondary": {"provider": "edge", "voice": "zh-CN-XiaoxiaoNeural"}
    }
  }
}
```

4. 跑一次：`VOICE_HOME=~/voice voice-bff --text "试一下" --out /tmp/t.mp3`

包内自带 `voice_skill/refs/example-reference.mp3`（约 63 秒，中文散文朗读），
是**可以直接试听效果的示例**——不配任何东西也能验证克隆链路通不通，换成你自己的音频即可。
`ref` 的解析顺序：绝对/相对路径 → `VOICE_HOME/<ref>` → 包内 `data`。

## 4. CLI 参数

`voice-bff`（= `python -m voice_skill.speak`）在引擎参数之上多了 `--voice-mode` / `--list-voice-modes`，
其余参数原样透传。

| 参数 | 说明 |
|------|------|
| `--text` / `--text-file` | 待合成文本，二者必须给其一 |
| `--out` | 输出 mp3 路径；缺省 `VOICE_HOME/out/tts_<时间戳>.mp3` |
| `--voice-mode` | 八声线（与 `--mood` 二选一，`--voice-mode` 优先） |
| `--mood` | 显式情绪（`moods` 的键，如 `温柔`） |
| `--auto-mood` | 随机情绪（按时间段加权，深夜偏向温柔/慵懒） |
| `--style` | 自由风格补充文本，与声线档文案叠加注入 |
| `--role` | 角色名（缺省读 `default_role`） |
| `--provider` | 强制 provider：`edge` / `mimo-clone` / `mimo` |
| `--preset` | `mimo` 的 voicedesign 音色预置名 |
| `--voice` | 直接指定 edge 音色名，如 `zh-CN-YunxiNeural` |
| `--speed` | 语速倍率（ffmpeg atempo 物理提速） |
| `--list-voice-modes` / `--list-providers` / `--list-presets` / `--list-moods` / `--list-styles` | 离线列表命令 |

## 5. Python API

```python
from voice_skill.voice import speak

speak("你好，我在")                                  # 默认声线
speak("这事就这么定了", mood="冷面")                  # 指定情绪
speak("通知你一声", provider="edge")                  # 强制兜底线
speak("哈喽", preset="年轻女声")                      # voicedesign 音色预置（mimo）
speak("长文……", style="语速再慢一点")                # 叠加风格指令
```

| 函数 | 签名 | 说明 |
|------|------|------|
| `speak` | `speak(text, *, role=None, provider=None, preset=None, voice=None, mood=None, auto_mood=False, style=None, speed=1.0, out=None) -> str` | 合成语音，返回 mp3 路径；全失败抛 `RuntimeError`，空文本抛 `ValueError` |
| `load_config` | `load_config() -> dict` | 读配置（三段式，每次重解析环境变量） |
| `mood_from_file` | `mood_from_file(path=None) -> dict \| None` | 读情绪 JSON 的 `mood` 段；缺失/损坏 → `None` |
| `voice_mode_for_emotion` | `voice_mode_for_emotion(cfg, emotion_state) -> tuple[str, dict] \| None` | 情绪 `mood_id` 直连声线档；无命中 → `None` |
| `build_clone_style` | `build_clone_style(cfg, mood_name, style_text="") -> str` | 拼克隆风格指令；都没有 → `""` |
| `mimo_clone_tts` | `mimo_clone_tts(text, ref, out, style="") -> bool` | 声线克隆合成（wav → ffmpeg 转 mp3） |
| `mimo_tts` | `mimo_tts(text, preset, out, mood=None, cfg=None) -> bool` | `mimo` voicedesign 定制声线 |
| `edge_tts` | `edge_tts(text, out, rate="+0%", pitch="+0Hz", voice="zh-CN-XiaoxiaoNeural") -> bool` | 微软 edge 免费合成 |
| `default_out_path` | `default_out_path() -> Path` | 默认落盘路径（`VOICE_CACHE` > `VOICE_HOME/out`） |
| `resolve_voice` | `resolve_voice(cfg, role, explicit) -> str` | edge 音色解析（显式 > `role_voices` > 默认晓晓） |

`voice_skill.speak`：`main() -> int`、`resolve_voice_mode(cfg, name) -> str | None`、`list_voice_modes(cfg) -> None`。

`speak()` 行为约定：

- 返回 mp3 路径（`str`）。
- 不传 `mood`/`auto_mood` → 若设了 `VOICE_MOOD_JSON`，读该文件的 `mood` 段定声线；否则用 `default` 档。
- `out` 不传 → `VOICE_HOME/out/tts_<时间戳>.mp3`。
- 全部 provider 失败抛 `RuntimeError`；空文本抛 `ValueError`。

## 6. 八声线

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

- `rate`/`pitch` 只对 `edge` / `mimo` 生效；**克隆档（`mimo-clone`）不吃这两个值**，
  而是把 `clone_styles` 里对应档位的自然语言文案注入模型（`--style` 追加在其后）。
- 7 档带 `emotion_id`，与外部情绪 JSON 的 `mood_id` 同名直连（无映射表）；
  `冷面` 无 `emotion_id`，是纯手动档。

## 7. 配置 schema

`voice-config.json`（三段式查找：`VOICE_CONFIG_PATH` > `VOICE_HOME/voice-config.json` > 包内默认）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `default_role` | str | 默认角色名 |
| `active` | obj | `{mode: "auto"\|"primary", primary, secondary}`：`auto` = 主失败自动切备用，`primary` = 只用主 |
| `providers` | list | 每项 `{name, priority, status, type, desc, env_key?}`；`status` 仅作说明，`--list-providers` 的图标按 `active` 段推导（primary ✅ / secondary ⚠ / 其余 ❌） |
| `roles` | obj | 每角色 `{voice_primary, voice_secondary}`，条目含 `provider`/`ref`/`voice`/`rate`/`pitch`/`speed`/`label` |
| `moods` | obj | 每情绪 `{rate, pitch, label}` |
| `mood_pool` | list | `--auto-mood` 的随机池（值须是 `moods` 的键） |
| `voice_modes` | obj | 八声线：每档 `{mood, label, desc, rate, pitch, emotion_id?}` |
| `clone_styles` | obj | 八声线对应的克隆风格文案（注入 user message） |
| `voices` | obj | voicedesign 音色预置：`{id, mode, label, voice_desc, style, notes}` |
| `role_voices` | obj | 角色 → edge 音色映射 |
| `retry` | obj | `{max_attempts, delay_seconds}` |

主备换序只需改 `active`，**不用改代码**：

```json
"active": {
  "mode": "auto",              // auto：主失败自动切备用；primary：只用主
  "primary": "edge",
  "secondary": "mimo-clone"
}
```

## 8. 环境变量

| 变量 | 作用 | 缺省 |
|------|------|------|
| `VOICE_HOME` | 配置/数据根（`voice-config.json`、`refs/`、`out/`） | 包目录 |
| `VOICE_CONFIG_PATH` | 显式配置文件路径（最高优先） | 未设 |
| `VOICE_MOOD_JSON` | 外部情绪 JSON 路径（读 `mood` 段定声线） | 空（→ `default` 档） |
| `VOICE_CACHE` | 输出目录覆盖 | `VOICE_HOME/out/` |
| `VOICE_DOTENV` | 显式指定某个 `.env` 文件（最高优先） | 未设 |
| `MIMO_API_KEY` | MiMo 的 key（克隆 / `mimo` 才需要） | 环境变量 → `VOICE_DOTENV` → `VOICE_HOME/.env` → 包目录向上 3 层的 `.env`（仓根）→ `~/.config/voice-skill/.env` |

## 9. 测试

```bash
pip install pytest
python3 -m pytest tests/ -q
```

- 全部离线：mock `openai` + 假 `ffmpeg`，**不触网**。
- 覆盖：克隆成功链与各失败分支、长文分段、provider 回退顺序、配置三段式优先级、
  配置 schema、公开 API 签名、八声线映射、CLI e2e（子进程 + tmp 隔离）。
- 真合成（需 `MIMO_API_KEY` + 网络）默认 `skipif` 跳过；
  启用：`MIMO_API_KEY=<key> VOICE_E2E_NETWORK=1 pytest tests/ -q`。

## 10. 已知限制

- **edge 限流**：微软免费接口高频会 `NoAudioReceived`，内置 1 次 2s 重试；仍失败则本次合成失败。
- **克隆会被截断**：单次 voiceclone 偶尔在 ~60 秒音频处提前收尾（实测：229 字的散文只合到 60s）。
  三道闸：① 正文超 120 字自动按标点分段，再 `ffmpeg concat` 拼接；
  ② 任一段 `finish_reason=length` 直接判失败（半截音频不落盘），由 `active.mode=auto` 切兜底；
  ③ 分段退化成长文只切出 1 段时直接报错，绝不单次合成后当成品。
  stderr 会逐段打「第 i/n 段 x.xs / n 字」+ 成品总时长——**字不少却时长偏短就是截断**，一眼可见。
- **参考音频 ≤ 7.5MB**（base64 后），超了直接走兜底。
- **克隆不吃 rate/pitch**：情绪靠 `clone_styles` 文案注入，物理提速只能靠 `--speed`（ffmpeg atempo）。
- **只做出向**：本包是「文本 → 音频」，不做语音识别。

## 11. 许可

MIT，见 [LICENSE](LICENSE)。
