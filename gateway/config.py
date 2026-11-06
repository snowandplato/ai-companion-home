"""
配置管理模块
从环境变量加载配置，提供统一的配置访问接口
"""
import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Optional

# 加载gateway目录下的.env文件，避免服务从其他工作目录启动时读不到配置。
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))


def default_data_path(name: str) -> str:
    """Keep local data beside the gateway unless DATA_DIR points elsewhere."""
    data_dir = Path(os.getenv("DATA_DIR", "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = Path(__file__).resolve().parent / data_dir
    return str(data_dir / name)


class Config:
    """网关配置类"""

    # ========== 主模型配置 ==========
    MAIN_API_PROVIDER: str = os.getenv("MAIN_API_PROVIDER", "zenmux")
    MAIN_BASE_URL: str = os.getenv("MAIN_BASE_URL", "https://zenmux.ai/api/v1")
    MAIN_API_KEY: str = os.getenv("MAIN_API_KEY", "")
    MAIN_MODEL: str = os.getenv("MAIN_MODEL", "claude-3-5-haiku-20250620")

    # ========== 总结模型配置 ==========
    SUMMARY_API_PROVIDER: str = os.getenv("SUMMARY_API_PROVIDER", "zenmux")
    SUMMARY_BASE_URL: str = os.getenv("SUMMARY_BASE_URL", "https://zenmux.ai/api/v1")
    SUMMARY_API_KEY: str = os.getenv("SUMMARY_API_KEY", "")
    SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", "claude-haiku-4-20250514")

    # ========== 总结触发条件 ==========
    SUMMARY_TRIGGER_ROUNDS: int = int(os.getenv("SUMMARY_TRIGGER_ROUNDS", "5"))
    CONTEXT_WINDOW_ROUNDS: int = int(os.getenv("CONTEXT_WINDOW_ROUNDS", "30"))  # 即时总结覆盖范围
    RECENT_CONTEXT_ROUNDS: int = int(os.getenv("RECENT_CONTEXT_ROUNDS", "10"))  # 注入原文轮数

    # ========== 记忆配置 ==========
    MEMORY_TOP_N: int = int(os.getenv("MEMORY_TOP_N", "4"))
    MEMORY_RANDOM_N: int = int(os.getenv("MEMORY_RANDOM_N", "1"))
    MEMORY_INJECTION_MODE: str = os.getenv("MEMORY_INJECTION_MODE", "v2").lower()
    MEMORY_WRITE_MODE: str = os.getenv("MEMORY_WRITE_MODE", "v2").lower()
    MEMORY_V2_THREAD_TOP_N: int = int(os.getenv("MEMORY_V2_THREAD_TOP_N", "2"))
    MEMORY_V2_EVENT_TOP_N: int = int(os.getenv("MEMORY_V2_EVENT_TOP_N", "5"))
    MEMORY_V2_EMBEDDING_EVENT_MIN: int = int(os.getenv("MEMORY_V2_EMBEDDING_EVENT_MIN", "2"))
    MEMORY_V2_EMBEDDING_EVENT_MAX: int = int(os.getenv("MEMORY_V2_EMBEDDING_EVENT_MAX", "2"))
    MEMORY_V2_SIBLING_N: int = int(os.getenv("MEMORY_V2_SIBLING_N", "1"))
    MEMORY_V2_SIBLING_TOTAL_N: int = int(os.getenv("MEMORY_V2_SIBLING_TOTAL_N", "3"))
    MEMORY_V2_RANDOM_RESURFACE_MAX: int = int(os.getenv("MEMORY_V2_RANDOM_RESURFACE_MAX", "1"))
    MEMORY_V2_RANDOM_RESURFACE_CASUAL_RATE: float = float(os.getenv("MEMORY_V2_RANDOM_RESURFACE_CASUAL_RATE", "0.25"))
    MEMORY_V2_RANDOM_RESURFACE_NORMAL_RATE: float = float(os.getenv("MEMORY_V2_RANDOM_RESURFACE_NORMAL_RATE", "0.15"))
    MEMORY_V2_RANDOM_RESURFACE_LOW_RATE: float = float(os.getenv("MEMORY_V2_RANDOM_RESURFACE_LOW_RATE", "0.03"))
    SHORT_TERM_MEMORY_DAYS: int = int(os.getenv("SHORT_TERM_MEMORY_DAYS", "15"))
    MAV_NOTES_LIMIT: int = int(os.getenv("MAV_NOTES_LIMIT", "10"))
    MEMORY_JUDGE_MODEL: str = os.getenv("MEMORY_JUDGE_MODEL", "claude-haiku-4-20250514")

    # ========== 岚的画像配置 ==========
    PORTRAIT_UPDATES_ENABLED: bool = os.getenv("PORTRAIT_UPDATES_ENABLED", "false").lower() == "true"
    PORTRAIT_ADMIN_PASSWORD: str = os.getenv("PORTRAIT_ADMIN_PASSWORD", "")
    PROMPT_CACHE_TTL: str = os.getenv("PROMPT_CACHE_TTL", "off").lower()

    # ========== Embedding配置 ==========
    EMBEDDING_ENABLED: bool = os.getenv("EMBEDDING_ENABLED", "false").lower() == "true"
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", SUMMARY_BASE_URL)
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", SUMMARY_API_KEY)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
    EMBEDDING_EVENT_TOP_N: int = int(os.getenv("EMBEDDING_EVENT_TOP_N", "8"))
    EMBEDDING_THREAD_TOP_N: int = int(os.getenv("EMBEDDING_THREAD_TOP_N", "4"))
    EMBEDDING_MIN_SCORE: float = float(os.getenv("EMBEDDING_MIN_SCORE", "0.45"))

    # ========== 视觉模型配置（图片中间层）==========
    VL_BASE_URL: str = os.getenv("VL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    VL_API_KEY: str = os.getenv("VL_API_KEY", "")
    VL_MODEL: str = os.getenv("VL_MODEL", "qwen3-vl-plus")

    # ========== 搜索配置（Tavily）==========
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # ========== 数据库配置 ==========
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", default_data_path("memory.db"))
    NOTE_IMAGE_DIR: str = os.getenv("NOTE_IMAGE_DIR", default_data_path("note_images"))
    CHAT_IMAGE_DIR: str = os.getenv("CHAT_IMAGE_DIR", default_data_path("chat_images"))
    ART_IMAGE_DIR: str = os.getenv("ART_IMAGE_DIR", default_data_path("art_images"))
    EPUB_DIR: str = os.getenv("EPUB_DIR", default_data_path("epubs"))

    # ========== Mav图片创作配置 ==========
    ART_IMAGE_BASE_URL: str = os.getenv("ART_IMAGE_BASE_URL", "https://api.openai.com")
    ART_IMAGE_API_KEY: str = os.getenv("ART_IMAGE_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    ART_IMAGE_MODEL: str = os.getenv("ART_IMAGE_MODEL", "gpt-image-2")
    ART_REVIEW_MODEL: str = os.getenv("ART_REVIEW_MODEL", "claude-sonnet-4-6")
    ART_DRAFT_COUNT: int = int(os.getenv("ART_DRAFT_COUNT", "3"))
    ART_MAX_RETRIES: int = int(os.getenv("ART_MAX_RETRIES", "1"))

    # ========== 服务配置 ==========
    API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ========== 后端地址配置 ==========
    # Official Anthropic endpoint; a private reverse proxy can be configured in .env.
    ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Official OpenAI endpoint; a private reverse proxy can be configured in .env.
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # zenmux中转API（直连）
    ZENMUX_BASE_URL: str = os.getenv("ZENMUX_BASE_URL", "https://zenmux.ai/api/v1")
    ZENMUX_API_KEY: str = os.getenv("ZENMUX_API_KEY", "")

    @classmethod
    def get_backend_config(cls, provider: str) -> dict:
        """
        根据提供商获取后端配置

        Args:
            provider: 提供商名称 (anthropic, openai, zenmux)

        Returns:
            dict: 包含base_url和api_key的字典
        """
        configs = {
            "anthropic": {
                "base_url": cls.ANTHROPIC_BASE_URL,
                "api_key": cls.ANTHROPIC_API_KEY
            },
            "openai": {
                "base_url": cls.OPENAI_BASE_URL,
                "api_key": cls.OPENAI_API_KEY
            },
            "zenmux": {
                "base_url": cls.ZENMUX_BASE_URL,
                "api_key": cls.ZENMUX_API_KEY
            }
        }
        return configs.get(provider, configs["zenmux"])

    @classmethod
    def get_summary_config(cls) -> dict:
        """
        获取总结模型配置

        Returns:
            dict: 包含base_url, api_key, model的字典
        """
        return {
            "provider": cls.SUMMARY_API_PROVIDER,
            "base_url": cls.SUMMARY_BASE_URL,
            "api_key": cls.SUMMARY_API_KEY,
            "model": cls.SUMMARY_MODEL
        }

    @classmethod
    def get_main_config(cls) -> dict:
        """
        获取主模型配置

        Returns:
            dict: 包含provider, base_url, api_key, model的字典
        """
        return {
            "provider": cls.MAIN_API_PROVIDER,
            "base_url": cls.MAIN_BASE_URL,
            "api_key": cls.MAIN_API_KEY,
            "model": cls.MAIN_MODEL
        }


# 全局配置实例
config = Config()
