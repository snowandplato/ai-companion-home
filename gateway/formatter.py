"""
格式转译模块
负责在不同API格式之间转换（OpenAI、Claude、Google）
"""
import json
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class RequestFormatter:
    """请求格式转译器"""

    @staticmethod
    def parse_openai_request(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析OpenAI格式请求

        Args:
            data: OpenAI格式的请求体

        Returns:
            Dict: 标准化后的请求数据
        """
        result = {
            "format": "openai",
            "model": data.get("model", ""),
            "messages": data.get("messages", []),
            "system": "",
            "stream": data.get("stream", False),
            "temperature": data.get("temperature"),
            "max_tokens": data.get("max_tokens"),
            "top_p": data.get("top_p"),
            "extra": {}
        }

        # 从messages中提取system message
        messages = result["messages"]
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                result["system"] = msg.get("content", "")
                break

        return result

    @staticmethod
    def parse_claude_request(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析Claude格式请求

        Args:
            data: Anthropic Claude格式的请求体

        Returns:
            Dict: 标准化后的请求数据
        """
        result = {
            "format": "claude",
            "model": data.get("model", ""),
            "messages": data.get("messages", []),
            "system": data.get("system", ""),
            "stream": data.get("stream", True),
            "max_tokens": data.get("max_tokens", 4096),
            "temperature": data.get("temperature"),
            "extra": {}
        }

        return result

    @staticmethod
    def parse_google_request(data: Dict[str, Any], path: str) -> Dict[str, Any]:
        """
        解析Google格式请求

        Args:
            data: Google格式的请求体
            path: 请求路径，用于提取模型名

        Returns:
            Dict: 标准化后的请求数据
        """
        # 从路径中提取模型名
        # 路径格式: /v1beta/models/{model}:generateContent
        model = "gemini-pro"
        if "models/" in path:
            try:
                parts = path.split("models/")
                if len(parts) > 1:
                    model_part = parts[1].split(":")[0]
                    model = model_part
            except Exception:
                pass

        # 提取system instruction
        system_instruction = ""
        contents = data.get("contents", [])
        if data.get("systemInstruction"):
            system_instruction = data["systemInstruction"].get("parts", [{}])[0].get("text", "")

        # 转换为标准格式
        messages = []
        for content in contents:
            parts = content.get("parts", [])
            for part in parts:
                if "text" in part:
                    role = "user" if content.get("role", "user") == "user" else "model"
                    messages.append({
                        "role": role,
                        "content": part["text"]
                    })

        result = {
            "format": "google",
            "model": model,
            "messages": messages,
            "system": system_instruction,
            "stream": True,
            "temperature": data.get("generationConfig", {}).get("temperature"),
            "max_tokens": data.get("generationConfig", {}).get("maxOutputTokens"),
            "extra": {
                "contents": contents,
                "generation_config": data.get("generationConfig", {})
            }
        }

        return result

    @staticmethod
    def to_openai_format(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        转换为OpenAI格式

        Args:
            data: 标准化后的请求数据

        Returns:
            Dict: OpenAI格式的请求体
        """
        result = {
            "model": data["model"],
            "messages": data["messages"].copy(),
            "stream": data.get("stream", False)
        }
        if result["stream"]:
            result["stream_options"] = {"include_usage": True}

        # 将system注入到messages中
        if data.get("system"):
            system_injected = False
            for i, msg in enumerate(result["messages"]):
                if msg.get("role") == "system":
                    msg["content"] = data["system"]
                    system_injected = True
                    break

            if not system_injected:
                result["messages"].insert(0, {
                    "role": "system",
                    "content": data["system"]
                })

        # 添加可选参数
        if data.get("temperature") is not None:
            result["temperature"] = data["temperature"]
        if data.get("max_tokens") is not None:
            result["max_tokens"] = data["max_tokens"]
        if data.get("top_p") is not None:
            result["top_p"] = data["top_p"]

        return result

    @staticmethod
    def to_claude_format(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        转换为Claude格式

        Args:
            data: 标准化后的请求数据

        Returns:
            Dict: Claude格式的请求体
        """
        result = {
            "model": data["model"],
            "messages": data["messages"].copy(),
            "stream": data.get("stream", True),
         "max_tokens": data.get("max_tokens") or 4096
        }

        # 将system添加到system字段
        if data.get("system"):
            result["system"] = data["system"]

        # 添加可选参数
        if data.get("temperature") is not None:
            result["temperature"] = data["temperature"]
        if data.get("output_config"):
            result["output_config"] = data["output_config"]

        return result

    @staticmethod
    def to_openai_responses_format(data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert the normalized request into OpenAI's native Responses shape."""
        input_items = []

        cache_sections = data.get("prompt_cache_system_sections") or []
        cache_enabled = bool(data.get("enable_prompt_cache"))
        model_lower = str(data.get("model", "")).lower()
        supports_explicit_cache = model_lower.startswith("gpt-6") or model_lower.startswith("gpt-5.6")

        if cache_sections and supports_explicit_cache:
            developer_content = []
            breakpoint_count = 0
            for section in cache_sections:
                if not isinstance(section, dict) or not section.get("text"):
                    continue
                block = {"type": "input_text", "text": section["text"]}
                if cache_enabled and section.get("cache_boundary") and breakpoint_count < 3:
                    block["prompt_cache_breakpoint"] = {"mode": "explicit"}
                    breakpoint_count += 1
                developer_content.append(block)
            if developer_content:
                input_items.append({"role": "developer", "content": developer_content})

        for message in data.get("messages", []):
            role = message.get("role", "user")
            if role == "system":
                # The injected system prompt is carried by ``instructions`` below.
                continue

            content = message.get("content", "")
            if isinstance(content, str):
                input_items.append({"role": role, "content": content})
                continue

            converted = []
            for block in content if isinstance(content, list) else []:
                block_type = block.get("type")
                if block_type in {"text", "input_text"}:
                    converted.append({"type": "input_text", "text": block.get("text", "")})
                elif block_type == "image":
                    source = block.get("source") or {}
                    if source.get("type") == "base64" and source.get("data"):
                        media_type = source.get("media_type", "image/jpeg")
                        converted.append({
                            "type": "input_image",
                            "image_url": f"data:{media_type};base64,{source['data']}",
                            "detail": "auto",
                        })
                elif block_type in {"image_url", "input_image"}:
                    image_url = block.get("image_url")
                    if isinstance(image_url, dict):
                        image_url = image_url.get("url")
                    if image_url:
                        converted.append({
                            "type": "input_image",
                            "image_url": image_url,
                            "detail": block.get("detail", "auto"),
                        })

            input_items.append({"role": role, "content": converted or [{"type": "input_text", "text": ""}]})

        result = {
            "model": data["model"],
            "input": input_items,
            "stream": data.get("stream", False),
            # M-Home owns conversation persistence; avoid creating a second server-side history.
            "store": False,
        }
        if data.get("system") and not (cache_sections and supports_explicit_cache):
            result["instructions"] = data["system"]
        if data.get("max_tokens") is not None:
            result["max_output_tokens"] = data["max_tokens"]
        if data.get("temperature") is not None:
            result["temperature"] = data["temperature"]
        if data.get("top_p") is not None:
            result["top_p"] = data["top_p"]

        output_config = data.get("output_config") or {}
        output_format = output_config.get("format") if isinstance(output_config, dict) else None
        if isinstance(output_format, dict) and output_format.get("type") == "json_schema":
            result["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": output_format.get("name") or "structured_output",
                    "strict": output_format.get("strict", True),
                    "schema": output_format.get("schema") or {},
                }
            }

        if data.get("enable_thinking"):
            result["reasoning"] = {"effort": "medium", "summary": "auto"}

        tools = []
        if data.get("enable_search"):
            tools.append({"type": "web_search"})
        if data.get("enable_native_image_generation"):
            image_tool = {
                "type": "image_generation",
                "quality": "medium",
            }
            if data.get("native_image_model"):
                image_tool["model"] = data["native_image_model"]
            tools.append(image_tool)
            # Responses currently exposes one total built-in-tool budget rather
            # than a separate image-call budget. Three keeps the user's hard
            # ceiling even when search and drawing are enabled together.
            result["max_tool_calls"] = 3
        if data.get("enable_memory_recall"):
            from memory_recall import TOOL_DESCRIPTION, TOOL_NAME, TOOL_PARAMETERS
            tools.append({
                "type": "function",
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "parameters": TOOL_PARAMETERS,
                "strict": False,
            })
        if tools:
            result["tools"] = tools
            result["tool_choice"] = "auto"

        if supports_explicit_cache:
            # Explicit mode with no marked breakpoints is the intentional "off" state.
            result["prompt_cache_options"] = {"mode": "explicit"}
            if cache_enabled:
                result["prompt_cache_options"]["ttl"] = "30m"
            if data.get("prompt_cache_key"):
                result["prompt_cache_key"] = data["prompt_cache_key"]

        return result

    @staticmethod
    def to_google_format(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        转换为Google格式

        Args:
            data: 标准化后的请求数据

        Returns:
            Dict: Google格式的请求体
        """
        # 将messages转换为Google的contents格式
        contents = []
        for msg in data["messages"]:
            content = {
                "role": "user" if msg.get("role") == "user" else "model",
                "parts": [{"text": msg.get("content", "")}]
            }
            contents.append(content)

        result = {
            "contents": contents,
            "generationConfig": data.get("extra", {}).get("generation_config", {})
        }

        # 添加system instruction
        if data.get("system"):
            result["systemInstruction"] = {
                "parts": [{"text": data["system"]}]
            }

        # 添加生成配置
        if not result["generationConfig"]:
            result["generationConfig"] = {}
        if data.get("temperature") is not None:
            result["generationConfig"]["temperature"] = data["temperature"]
        if data.get("max_tokens") is not None:
            result["generationConfig"]["maxOutputTokens"] = data["max_tokens"]

        return result

    @classmethod
    def parse_request(cls, data: Dict[str, Any], path: str, method: str) -> Optional[Dict[str, Any]]:
        """
        根据路径和方法解析请求

        Args:
            data: 请求体数据
            path: 请求路径
            method: HTTP方法

        Returns:
            Dict: 标准化后的请求数据，解析失败返回None
        """
        try:
            if "/v1/chat/completions" in path or path == "/v1/chat/completions":
                return cls.parse_openai_request(data)
            elif path == "/messages" or "/messages" in path:
                return cls.parse_claude_request(data)
            elif "/v1beta/models" in path:
                return cls.parse_google_request(data, path)
            else:
                # 默认按OpenAI格式解析
                return cls.parse_openai_request(data)
        except Exception as e:
            logger.error(f"解析请求失败: {e}")
            return None

    @classmethod
    def format_for_backend(cls, data: Dict[str, Any], backend_type: str) -> Dict[str, Any]:
        """
        根据后端类型格式化请求

        Args:
            data: 标准化后的请求数据
            backend_type: 后端类型 (anthropic, openai, zenmux)

        Returns:
            Dict: 格式化后的请求体
        """
        if backend_type == "anthropic":
            return cls.to_claude_format(data)
        elif backend_type in ["openai", "zenmux"]:
            return cls.to_openai_format(data)
        else:
            # 默认按OpenAI格式
            return cls.to_openai_format(data)


class ResponseFormatter:
    """响应格式转译器"""

    @staticmethod
    def parse_openai_response(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析OpenAI格式响应

        Args:
            data: OpenAI格式的响应体

        Returns:
            Dict: 标准化后的响应数据
        """
        return {
            "format": "openai",
            "original": data
        }

    @staticmethod
    def parse_claude_response(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析Claude格式响应

        Args:
            data: Claude格式的响应体

        Returns:
            Dict: 标准化后的响应数据
        """
        return {
            "format": "claude",
            "original": data
        }

    @staticmethod
    def parse_google_response(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析Google格式响应

        Args:
            data: Google格式的响应体

        Returns:
            Dict: 标准化后的响应数据
        """
        return {
            "format": "google",
            "original": data
        }

    @classmethod
    def parse_response(cls, data: Dict[str, Any], backend_type: str) -> Dict[str, Any]:
        """
        根据后端类型解析响应

        Args:
            data: 响应数据
            backend_type: 后端类型

        Returns:
            Dict: 标准化后的响应数据
        """
        if backend_type == "anthropic":
            return cls.parse_claude_response(data)
        elif backend_type in ["openai", "zenmux"]:
            return cls.parse_openai_response(data)
        else:
            return cls.parse_openai_response(data)
