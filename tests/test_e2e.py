#!/usr/bin/env python3
"""端到端黑盒 e2e 测试（子进程调用 CLI 入口，不触网、不碰用户真实家目录）。

覆盖范围（真实 CLI 子进程；入口 `python3 -m voice_skill.speak ...` +
引擎直入口 `python3 -m voice_skill.voice ...`）：
1. 离线纯配置命令：--list-voice-modes / --list-providers / --list-moods /
   --list-presets / --list-styles，断言退出码 0 与 stdout 关键内容。
2. 引擎直入口 `voice_skill.voice`：--list-providers / --list-styles / 缺文本 /
   缺 key fail-fast（覆盖 README §3/§5 记录的引擎调用入口）。
3. 配置三段式优先级：VOICE_CONFIG_PATH > VOICE_HOME/voice-config.json > 包内默认。
4. 无 key 缺 key 路径 fail-fast：--provider mimo-clone 且无 MIMO_API_KEY → 退出码 1，
   stderr 报「缺少 MIMO_API_KEY」，不触网（key 缺失即短路）。
5. 成功链（注入 fake openai + fake ffmpeg，走真实 CLI）：验证 mimo-clone 的
   OpenAI 调用参数契约（model/messages/audio.voice base64）与 ffmpeg 转码落盘。
6. 真合成（需 MIMO_API_KEY + 网络 + 显式开关）：默认 skipif 跳过并注明原因。

隔离：每测用 tmp_path fixture；子进程 env 全量重建（HOME 指向 tmp，杜绝读到真实
用户家目录 .env），VOICE_HOME 指向 tmp，绝不写入用户环境。

运行：python3 -m pytest tests/ -q
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# 真合成开关：需真实 key + 显式网络授权，默认（离线）跳过。
_REAL_KEY = os.environ.get("MIMO_API_KEY")
_REAL_NET = os.environ.get("VOICE_E2E_NETWORK") == "1"

_VOICE_MODE_LABELS = [
    "🌙 温柔", "🔥 魅惑", "🧠 清醒", "❄️ 冷面",
    "⚡ 热烈", "😋 俏皮", "🌘 深沉", "😴 慵懒",
]


def _base_env(tmp_path: Path, *, voice_config_path=None, extra=None) -> dict:
    """全量重建子进程 env：PATH/PYTHONPATH 保留，HOME 指向 tmp，绝不继承 MIMO_API_KEY。"""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "VOICE_HOME": str(tmp_path),
        "HOME": str(home),
        "PYTHONIOENCODING": "utf-8",
    }
    if voice_config_path is not None:
        env["VOICE_CONFIG_PATH"] = str(voice_config_path)
    if extra:
        env.update(extra)
    return env


def _run(args, env) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "voice_skill.speak", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=180,
    )


def _run_engine(args, env) -> subprocess.CompletedProcess:
    """引擎直入口（README §3/§5：`python -m voice_skill.voice`）。"""
    return subprocess.run(
        [sys.executable, "-m", "voice_skill.voice", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=180,
    )


def _marker_config(tag: str) -> dict:
    return {
        "active": {"mode": "primary", "primary": f"{tag}-p", "secondary": f"{tag}-s"},
        "providers": [{"name": f"{tag}-p", "priority": 0, "status": "active", "desc": tag}],
        "roles": {
            "default": {
                "voice_primary": {"label": f"{tag}-主"},
                "voice_secondary": {"label": f"{tag}-备"},
            }
        },
    }


# ---------------------------------------------------------------------------
# 1. 离线纯配置命令
# ---------------------------------------------------------------------------

def test_e2e_list_voice_modes(tmp_path):
    r = _run(["--list-voice-modes"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "未知" not in r.stderr
    for label in _VOICE_MODE_LABELS:
        assert label in r.stdout, f"缺少声线 {label}\n{r.stdout}"
    body = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(body) == 8, r.stdout


def test_e2e_list_providers(tmp_path):
    r = _run(["--list-providers"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "mimo-clone" in r.stdout
    assert "edge" in r.stdout
    assert "主线" in r.stdout


def test_e2e_list_moods(tmp_path):
    r = _run(["--list-moods"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "温柔" in r.stdout
    assert "随机池" in r.stdout


def test_e2e_list_presets(tmp_path):
    r = _run(["--list-presets"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "标准女声" in r.stdout


def test_e2e_list_styles(tmp_path):
    r = _run(["--list-styles"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "温柔" in r.stdout
    assert "轻声细语" in r.stdout


# ---------------------------------------------------------------------------
# 1b. 引擎直入口 voice_skill.voice（README §3/§5 记录的引擎调用入口）
# ---------------------------------------------------------------------------

def test_e2e_engine_entry_list_providers(tmp_path):
    r = _run_engine(["--list-providers"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "mimo-clone" in r.stdout
    assert "edge" in r.stdout
    assert "主线" in r.stdout


def test_e2e_engine_entry_list_styles(tmp_path):
    r = _run_engine(["--list-styles"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "温柔" in r.stdout
    assert "轻声细语" in r.stdout


def test_e2e_engine_entry_no_text_returns_2(tmp_path):
    r = _run_engine([], _base_env(tmp_path))
    assert r.returncode == 2
    assert "必须提供 --text" in r.stderr


def test_e2e_engine_entry_missing_key_fail_fast(tmp_path):
    out = tmp_path / "o.mp3"
    r = _run_engine(["--text", "你好", "--provider", "mimo-clone", "--out", str(out)],
                    _base_env(tmp_path))
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "缺少 MIMO_API_KEY" in r.stderr
    assert not out.exists()


# ---------------------------------------------------------------------------
# 2. 配置三段式优先级
# ---------------------------------------------------------------------------

def test_e2e_config_path_overrides_voice_home(tmp_path):
    (tmp_path / "voice-config.json").write_text(
        json.dumps(_marker_config("HOME")), encoding="utf-8")
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps(_marker_config("PATHCFG")), encoding="utf-8")

    r = _run(["--list-providers"], _base_env(tmp_path, voice_config_path=explicit))
    assert r.returncode == 0, r.stderr
    assert "PATHCFG-p" in r.stdout
    assert "HOME-p" not in r.stdout


def test_e2e_config_from_voice_home(tmp_path):
    (tmp_path / "voice-config.json").write_text(
        json.dumps(_marker_config("HOME")), encoding="utf-8")

    r = _run(["--list-providers"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "HOME-p" in r.stdout


def test_e2e_config_falls_back_to_package_default(tmp_path):
    r = _run(["--list-providers"], _base_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "mimo-clone" in r.stdout


# ---------------------------------------------------------------------------
# 3. 缺 key / 参数错误 fail-fast
# ---------------------------------------------------------------------------

def test_e2e_missing_key_fail_fast_no_network(tmp_path):
    out = tmp_path / "o.mp3"
    r = _run(["--text", "你好", "--provider", "mimo-clone", "--out", str(out)],
             _base_env(tmp_path))
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "缺少 MIMO_API_KEY" in r.stderr
    assert "语音合成失败" in r.stderr
    assert not out.exists()


def test_e2e_no_text_returns_usage_error(tmp_path):
    r = _run([], _base_env(tmp_path))
    assert r.returncode == 2
    assert "必须提供 --text" in r.stderr


def test_e2e_unknown_voice_mode_returns_2(tmp_path):
    r = _run(["--text", "你好", "--voice-mode", "不存在"], _base_env(tmp_path))
    assert r.returncode == 2
    assert "未知声线" in r.stderr


# ---------------------------------------------------------------------------
# 4. 成功链（注入 fake openai + fake ffmpeg，走真实 CLI，不触网）
# ---------------------------------------------------------------------------

def _install_fake_providers(tmp_path: Path):
    """在 tmp 生成 fake openai 模块 + fake ffmpeg 可执行；返回 (bin_dir, log_path)。"""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "openai_calls.jsonl"

    (bin_dir / "openai.py").write_text(
        "import base64, json, os\n"
        "_WAV = b'RIFFfake-wav-bytes'\n"
        "class _Audio:\n"
        "    def __init__(self, data): self.data = data\n"
        "class _Msg:\n"
        "    def __init__(self, a): self.audio = a\n"
        "class _Choice:\n"
        "    def __init__(self, m): self.message = m\n"
        "class _Completion:\n"
        "    def __init__(self):\n"
        "        self.choices = [_Choice(_Msg(_Audio(base64.b64encode(_WAV).decode())))]\n"
        "class _Completions:\n"
        "    def create(self, **kwargs):\n"
        "        with open(os.environ['FAKE_OPENAI_LOG'], 'a', encoding='utf-8') as f:\n"
        "            f.write(json.dumps(kwargs) + '\\n')\n"
        "        return _Completion()\n"
        "class _Chat:\n"
        "    def __init__(self): self.completions = _Completions()\n"
        "class OpenAI:\n"
        "    def __init__(self, api_key=None, base_url=None):\n"
        "        self.api_key = api_key\n"
        "        self.base_url = base_url\n"
        "        self.chat = _Chat()\n",
        encoding="utf-8")

    ffmpeg = bin_dir / "ffmpeg"
    ffmpeg.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "open(sys.argv[-1], 'wb').write(b'ID3FAKEMP3')\n"
        "sys.exit(0)\n",
        encoding="utf-8")
    ffmpeg.chmod(0o755)
    return bin_dir, log


def test_e2e_success_chain_with_fake_provider(tmp_path):
    bin_dir, log = _install_fake_providers(tmp_path)
    out = tmp_path / "out.mp3"
    ref = tmp_path / "refs" / "example-reference.mp3"
    ref.parent.mkdir()
    ref.write_bytes(b"FAKEREFBYTES" * 8)

    cfg = json.loads((REPO_ROOT / "voice_skill" / "voice-config.json").read_text(encoding="utf-8"))
    cfg["roles"]["default"]["voice_primary"]["ref"] = str(ref)
    cfg["roles"]["default"]["voice_primary"]["speed"] = 1.0  # 免 ffmpeg atempo 二次转码
    cfg_path = tmp_path / "voice-config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    env = _base_env(tmp_path, voice_config_path=cfg_path, extra={
        "MIMO_API_KEY": "sk-fake",
        "FAKE_OPENAI_LOG": str(log),
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONPATH": str(bin_dir) + os.pathsep + str(REPO_ROOT),
    })

    r = _run(["--text", "你好", "--provider", "mimo-clone", "--out", str(out)], env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert out.exists() and out.read_bytes() == b"ID3FAKEMP3"
    assert "语音已生成" in r.stdout

    calls = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == "mimo-v2.5-tts-voiceclone"
    assert call["messages"][-1] == {"role": "assistant", "content": "你好"}
    assert call["audio"]["format"] == "wav"
    assert call["audio"]["voice"].startswith("data:audio/mpeg;base64,")
    payload = call["audio"]["voice"].split(";base64,", 1)[1]
    assert base64.b64decode(payload) == ref.read_bytes()


# ---------------------------------------------------------------------------
# 5. 真合成（默认跳过：需 key + 网络）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (_REAL_KEY and _REAL_NET),
    reason="真合成需真实 MIMO_API_KEY 且网络可达小米 MiMo 接口；"
           "默认离线环境跳过（设 MIMO_API_KEY + VOICE_E2E_NETWORK=1 才启用）。",
)
def test_e2e_real_mimo_clone_synthesis(tmp_path):
    out = tmp_path / "real.mp3"
    env = dict(os.environ)
    env["VOICE_HOME"] = str(tmp_path)
    env.pop("VOICE_CONFIG_PATH", None)
    env.pop("VOICE_MOOD_JSON", None)
    r = _run(["--text", "睡吧，我在", "--provider", "mimo-clone", "--out", str(out)], env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert out.exists() and out.stat().st_size > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
