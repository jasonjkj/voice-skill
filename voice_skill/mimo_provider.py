"""
MiMo TTS Provider
小米 MiMo-V2.5-TTS 语音合成 Provider
- 官方文档：https://mimo.mi.com/models/zh-CN/mimo-v2.5-tts
- Base URL: https://api.xiaomimimo.com/v1
- 认证: OpenAI 兼容格式，需环境变量 MIMO_API_KEY
- 当前限时免费
"""

import os
import base64
import logging
from typing import Optional
from openai import OpenAI

logger = logging.getLogger(__name__)


class MiMoTTSProvider:
    """MiMo TTS Provider - 通用 TTS 插件接口"""

    name = "mimo"
    display_name = "小米 MiMo TTS"
    description = "小米 MiMo-V2.5-TTS：精品音色、风格指令、内联情绪标签、零提示词理解（限时免费）"

    # 支持的音色（从官方文档示例推断，实际可用 get-voice-list 动态获取）
    VOICES = {
        "Chloe": "美式女声·温柔自然",
        "Adam": "美式男声·沉稳磁性",
        "Emma": "英式女声·优雅得体",
        "Noah": "英式男声·绅士从容",
    }

    # 支持的音频格式
    FORMATS = ["mp3", "wav", "ogg", "aac", "opus"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.api_key = os.environ.get("MIMO_API_KEY")
        self.base_url = "https://api.xiaomimimo.com/v1"
        self.model = "mimo-v2.5-tts"

        if not self.api_key:
            logger.warning("MIMO_API_KEY 环境变量未设置，MiMo TTS 将不可用")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        ) if self.api_key else None

    def is_available(self) -> bool:
        """检查 Provider 是否可用"""
        return self.client is not None

    def get_voices(self) -> dict:
        """返回支持的音色列表"""
        return self.VOICES

    def get_formats(self) -> list:
        """返回支持的音频格式"""
        return self.FORMATS

    def synthesize(
        self,
        text: str,
        voice: str = "Chloe",
        format: str = "mp3",
        speed: float = 1.0,
        **kwargs
    ) -> bytes:
        """
        合成语音

        Args:
            text: 要合成的文本
            voice: 音色名称
            format: 音频格式
            speed: 语速 (0.5-2.0)
            **kwargs: 额外参数
                - style: 风格指令（如 "温柔、沉稳、像深夜电台主持人"）
                - emotion: 情绪标签（如 "crying", "cheerful" 等，需音色支持）
                - pitch: 音调 (0.5-2.0)
                - volume: 音量 (0.1-2.0)

        Returns:
            音频字节数据
        """
        if not self.is_available():
            raise RuntimeError("MiMo TTS 不可用：请设置 MIMO_API_KEY 环境变量")

        # 验证音色
        if voice not in self.VOICES:
            logger.warning(f"音色 '{voice}' 不在已知列表中，将尝试直接传给 API")
            # 不阻止，API 会返回错误

        # 验证格式
        if format not in self.FORMATS:
            raise ValueError(f"不支持的格式: {format}，支持: {self.FORMATS}")

        # 构建消息：第一条是风格指令，第二条是实际文本
        style = kwargs.get("style", "自然、亲切、像朋友聊天")
        messages = [
            {"role": "user", "content": style},
            {"role": "assistant", "content": text}
        ]

        # 音频参数
        audio_params = {
            "format": format,
            "voice": voice
        }

        # 可选参数
        if "pitch" in kwargs:
            audio_params["pitch"] = kwargs["pitch"]
        if "volume" in kwargs:
            audio_params["volume"] = kwargs["volume"]

        try:
            logger.info(f"调用 MiMo TTS: voice={voice}, format={format}, text_len={len(text)}")
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                audio=audio_params
            )

            audio_b64 = completion.choices[0].message.audio.data
            audio_bytes = base64.b64decode(audio_b64)
            logger.info(f"MiMo TTS 合成成功: {len(audio_bytes)} bytes")
            return audio_bytes

        except Exception as e:
            logger.error(f"MiMo TTS 合成失败: {e}")
            raise

    def get_max_text_length(self) -> int:
        """最大文本长度（官方文档未明确，保守估计）"""
        return 5000


# 插件工厂入口
def create_provider(config: dict = None) -> MiMoTTSProvider:
    """插件工厂函数"""
    return MiMoTTSProvider(config)