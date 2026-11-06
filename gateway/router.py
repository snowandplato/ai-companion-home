"""
智能路由模块
根据模型名称决定使用哪个后端API
"""
import re
import logging
import httpx
import codecs
import json
from typing import Dict, Any, Optional, Tuple, AsyncGenerator, List
from config import config
from formatter import RequestFormatter
from prompt_cache import apply_anthropic_prompt_cache

logger = logging.getLogger(__name__)


class Router:
    """智能路由器"""

    # Claude模型关键词
    CLAUDE_PATTERNS = [
        r"claude",
        r"claude-?3",
        r"claude-?haiku",
        r"claude-?sonnet",
        r"claude-?opus"
    ]

    # OpenAI模型关键词
    OPENAI_PATTERNS = [
        r"gpt-?4",
        r"gpt-?3\.?5",
        r"o1",
        r"o3",
        r"o4-mini"
    ]

    # Google模型关键词
    GOOGLE_PATTERNS = [
        r"gemini",
        r"gemini-?pro",
        r"gemini-?ultra"
    ]

    # DeepSeek模型关键词
    DEEPSEEK_PATTERNS = [
        r"deepseek",
        r"deepseek-?chat",
        r"deepseek-?coder"
    ]

    def __init__(self):
        self.formatter = RequestFormatter()

    def identify_model_type(self, model: str) -> str:
        """
        识别模型类型

        Args:
            model: 模型名称

        Returns:
            str: 模型类型 (claude, openai, google, deepseek, zenmux)
        """
        model_lower = model.lower()

        # 检查Claude
        for pattern in self.CLAUDE_PATTERNS:
            if re.search(pattern, model_lower):
                return "anthropic"

        # 检查OpenAI
        for pattern in self.OPENAI_PATTERNS:
            if re.search(pattern, model_lower):
                return "openai"

        # 检查Google
        for pattern in self.GOOGLE_PATTERNS:
            if re.search(pattern, model_lower):
                return "google"

        # 检查DeepSeek
        for pattern in self.DEEPSEEK_PATTERNS:
            if re.search(pattern, model_lower):
                return "zenmux"

        # 默认返回zenmux
        return "zenmux"

    def identify_backend_by_api_key(self, api_key: str) -> str:
        """
        根据API Key格式识别后端类型

        Args:
            api_key: API Key

        Returns:
            str: 后端类型 (anthropic, openai, zenmux)
        """
        if not api_key:
            return "zenmux"  # 默认走zenmux

        # sk-ant-xxx -> Anthropic
        if api_key.startswith("sk-ant-"):
            return "anthropic"

        # ZenMux 按量付费与订阅 Key
        if api_key.startswith(("sk-payg-", "sk-ai-v1-", "sk-ss-v1-")):
            return "zenmux"

        # 其他 sk-xxx -> OpenAI
        if api_key.startswith("sk-"):
            return "openai"

        # 默认返回zenmux
        return "zenmux"

    def get_backend(self, model: str) -> Tuple[str, Dict[str, str]]:
        """
        获取后端配置

        Args:
            model: 模型名称

        Returns:
            Tuple[str, Dict]: (后端类型, 后端配置)
        """
        backend_type = self.identify_model_type(model)
        backend_config = config.get_backend_config(backend_type)

        logger.info(f"模型 {model} 识别为 {backend_type}，使用后端 {backend_config['base_url']}")

        return backend_type, backend_config

    def prepare_request(
        self,
        parsed_request: Dict[str, Any],
        backend_type: str,
        api_key: str = None
    ) -> Tuple[str, Dict[str, Any], Dict[str, str]]:
        """
        准备转发请求

        Args:
            parsed_request: 解析后的请求数据
            backend_type: 后端类型
            api_key: 可选的API Key（如果不为空则覆盖配置）

        Returns:
            Tuple[str, Dict, Dict]: (目标URL, 请求体, 请求头)
        """
        # 直传图片模式强制走LA中转
        if parsed_request.get("force_anthropic"):
            backend_type = "anthropic"
            import os
            anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
            if anthropic_key:
                api_key = anthropic_key

        backend_config = config.get_backend_config(backend_type)
        base_url = backend_config["base_url"]

        # 格式化请求体
        if backend_type == "openai":
            formatted_body = self.formatter.to_openai_responses_format(parsed_request)
        else:
            formatted_body = self.formatter.format_for_backend(parsed_request, backend_type)

        # Anthropic官方Messages API使用原生content blocks设置显式缓存断点。
        # ZenMux/OpenAI兼容路径仍沿用普通system字符串，不受此开关影响。
        if backend_type == "anthropic" and not parsed_request.get("force_anthropic"):
            apply_anthropic_prompt_cache(
                formatted_body,
                parsed_request,
                parsed_request.get("prompt_cache_ttl", config.PROMPT_CACHE_TTL)
            )

        # 去掉zenmux格式的模型名前缀（放在formatter之后）
        if parsed_request.get("force_anthropic"):
            model = formatted_body.get("model", "")
            if "/" in model:
                model = model.split("/", 1)[1]
            # 把小数点版本号格式转换为破折号格式（如 4.6 → 4-6）
            import re
            model = re.sub(r'(\d+)\.(\d+)', r'\1-\2', model)
            formatted_body["model"] = model

        # 构建请求头
        headers = {
            "Content-Type": "application/json"
        }

        # API Key优先级：传入的 > 后端配置的
        final_api_key = api_key or backend_config.get("api_key", "")

        if backend_type == "anthropic":
            headers["x-api-key"] = final_api_key
            headers["anthropic-version"] = "2023-06-01"
            url = f"{base_url}/v1/messages"
        elif backend_type == "google":
            headers["Authorization"] = f"Bearer {final_api_key}"
            url = f"{base_url}/v1beta/models/{formatted_body.get('model', 'gemini-pro')}:generateContent"
        elif backend_type == "zenmux":
            # zenmux 的 base_url 已经包含 /v1，直接用 /chat/completions
            headers["Authorization"] = f"Bearer {final_api_key}"
            url = f"{base_url}/chat/completions"
        else:
            # OpenAI官方主对话使用原生Responses API。
            headers["Authorization"] = f"Bearer {final_api_key}"
            url = f"{base_url}/v1/responses"

        # Thinking模式（仅Anthropic原生支持）
        if backend_type == "anthropic" and parsed_request.get("enable_thinking"):
            formatted_body["thinking"] = {
                "type": "enabled",
                "budget_tokens": 8000
            }
            formatted_body["max_tokens"] = 16000
            formatted_body["temperature"] = 1  # thinking模式要求temperature=1

        # 联网搜索（仅Anthropic原生支持，开启时传tools）
        if backend_type == "anthropic" and parsed_request.get("enable_search"):
            formatted_body["tools"] = [
                {"type": "web_search_20250305", "name": "web_search"},
            ]

        if parsed_request.get("enable_memory_recall"):
            from memory_recall import TOOL_DESCRIPTION, TOOL_NAME, TOOL_PARAMETERS
            from app_identity import names_in_template
            if backend_type == "anthropic":
                formatted_body.setdefault("tools", []).append({
                    "name": TOOL_NAME,
                    "description": names_in_template(TOOL_DESCRIPTION),
                    "input_schema": TOOL_PARAMETERS,
                })
            elif backend_type == "zenmux":
                formatted_body.setdefault("tools", []).append({
                    "type": "function",
                    "function": {
                        "name": TOOL_NAME,
                        "description": names_in_template(TOOL_DESCRIPTION),
                        "parameters": TOOL_PARAMETERS,
                    },
                })
                formatted_body["tool_choice"] = "auto"

        if formatted_body.get("thinking"):
            logger.info("思考模式已启用 backend=%s", backend_type)

        if parsed_request.get("force_anthropic"):
            logger.info("force_anthropic enabled")

        return url, formatted_body, headers

    @staticmethod
    async def normalize_openai_responses_stream(stream_gen) -> AsyncGenerator[bytes, None]:
        """Translate Responses SSE events to the small SSE vocabulary used by M-Home."""
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        reasoning_started = False
        done_sent = False
        native_images: List[Dict[str, str]] = []
        seen_image_results = set()
        native_image_started = False

        def capture_image(item: Any) -> None:
            if not isinstance(item, dict) or item.get("type") != "image_generation_call":
                return
            result = item.get("result")
            if not isinstance(result, str) or not result or result in seen_image_results:
                return
            seen_image_results.add(result)
            if len(native_images) < 3:
                native_images.append({
                    "b64_json": result,
                    "revised_prompt": str(item.get("revised_prompt") or ""),
                })

        def native_image_event() -> Optional[bytes]:
            if not native_images:
                return None
            payload = {"type": "mhome_native_images", "images": native_images[:3]}
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

        async for chunk in stream_gen:
            buffer += decoder.decode(chunk).replace("\r\n", "\n")
            while "\n\n" in buffer:
                event, buffer = buffer.split("\n\n", 1)
                data_lines = [line[5:].strip() for line in event.splitlines() if line.startswith("data:")]
                if not data_lines:
                    continue
                raw = "\n".join(data_lines)
                if raw == "[DONE]":
                    if reasoning_started:
                        yield b'data: {"type":"content_block_stop"}\n\n'
                    if not done_sent:
                        yield b"data: [DONE]\n\n"
                    return
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event_type = payload.get("type", "")
                if event_type in {"memory_recall_started", "memory_recall_finished", "memory_recall_usage", "memory_recall_next_request"}:
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                elif event_type == "response.output_text.delta":
                    # Some models begin visible output without a separate reasoning
                    # summary "done" event. Close the UI block at that boundary.
                    if reasoning_started:
                        reasoning_started = False
                        yield b'data: {"type":"content_block_stop"}\n\n'
                    normalized = {"choices": [{"delta": {"content": payload.get("delta", "")}}]}
                    yield f"data: {json.dumps(normalized, ensure_ascii=False)}\n\n".encode("utf-8")
                elif event_type == "response.refusal.delta":
                    if reasoning_started:
                        reasoning_started = False
                        yield b'data: {"type":"content_block_stop"}\n\n'
                    normalized = {"choices": [{"delta": {"content": payload.get("delta", "")}}]}
                    yield f"data: {json.dumps(normalized, ensure_ascii=False)}\n\n".encode("utf-8")
                elif event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                    if not reasoning_started:
                        reasoning_started = True
                        yield b'data: {"type":"content_block_start","content_block":{"type":"thinking"}}\n\n'
                    normalized = {
                        "type": "content_block_delta",
                        "delta": {"type": "thinking_delta", "thinking": payload.get("delta", "")},
                    }
                    yield f"data: {json.dumps(normalized, ensure_ascii=False)}\n\n".encode("utf-8")
                elif event_type in {"response.reasoning_summary_text.done", "response.reasoning_text.done"}:
                    if reasoning_started:
                        reasoning_started = False
                        yield b'data: {"type":"content_block_stop"}\n\n'
                elif "web_search_call" in event_type:
                    # Keep the native event long enough for main.py to emit the UI notice.
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                elif event_type.startswith("response.image_generation_call.") and event_type not in {
                    "response.image_generation_call.partial_image",
                    "response.image_generation_call.completed",
                }:
                    if not native_image_started:
                        native_image_started = True
                        yield b'data: {"type":"mhome_native_image_started"}\n\n'
                elif event_type == "response.output_item.added":
                    item = payload.get("item") or {}
                    if item.get("type") == "image_generation_call" and not native_image_started:
                        native_image_started = True
                        yield b'data: {"type":"mhome_native_image_started"}\n\n'
                elif event_type == "response.output_item.done":
                    capture_image(payload.get("item"))
                elif event_type == "response.image_generation_call.completed":
                    capture_image({
                        "type": "image_generation_call",
                        "result": payload.get("result"),
                        "revised_prompt": payload.get("revised_prompt"),
                    })
                elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
                    response = payload.get("response") or {}
                    for item in response.get("output", []) or []:
                        capture_image(item)
                    image_event = native_image_event()
                    if image_event:
                        yield image_event
                    usage = response.get("usage")
                    if isinstance(usage, dict):
                        yield f"data: {json.dumps({'usage': usage}, ensure_ascii=False)}\n\n".encode("utf-8")
                    if reasoning_started:
                        reasoning_started = False
                        yield b'data: {"type":"content_block_stop"}\n\n'
                    done_sent = True
                    yield b"data: [DONE]\n\n"
                    return
                elif event_type == "error":
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

        buffer += decoder.decode(b"", final=True).replace("\r\n", "\n")
        if reasoning_started:
            yield b'data: {"type":"content_block_stop"}\n\n'
        if not done_sent:
            yield b"data: [DONE]\n\n"

    @staticmethod
    def normalize_openai_responses_response(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Translate a non-streaming Responses result to Chat Completions shape."""
        text_parts = []
        reasoning_parts = []
        for item in payload.get("output", []) or []:
            if item.get("type") == "message":
                for block in item.get("content", []) or []:
                    if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                        text_parts.append(block["text"])
            elif item.get("type") == "reasoning":
                for block in item.get("summary", []) or []:
                    if isinstance(block.get("text"), str):
                        reasoning_parts.append(block["text"])

        message = {"role": "assistant", "content": "".join(text_parts)}
        if reasoning_parts:
            message["reasoning"] = "\n".join(reasoning_parts)
        return {
            "id": payload.get("id"),
            "object": "chat.completion",
            "model": payload.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": payload.get("usage") or {},
        }

    async def forward_request(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str]
    ) -> httpx.Response:
        """
        转发请求到后端

        Args:
            url: 目标URL
            body: 请求体
            headers: 请求头

        Returns:
            httpx.Response: 响应对象
        """
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers=headers
                )
                response.raise_for_status()
                return response

        except httpx.HTTPStatusError as e:
            logger.error("后端返回错误 status=%s", e.response.status_code)
            raise
        except Exception as e:
            logger.error(f"转发请求失败: {e}")
            raise

    async def forward_request_stream(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str]
    ) -> AsyncGenerator[bytes, None]:
        """
        流式转发请求到后端

        Args:
            url: 目标URL
            body: 请求体
            headers: 请求头

        Yields:
            bytes: SSE数据块
        """
        # Native image generation can legitimately take longer than a text-only turn.
        client = httpx.AsyncClient(timeout=300.0, follow_redirects=True)
        try:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers=headers
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk

        except httpx.HTTPStatusError as e:
            logger.error("后端返回错误 status=%s", e.response.status_code)
            raise
        except Exception as e:
            logger.error(f"流式转发失败: {e}")
            raise
        finally:
            await client.aclose()


class RouterService:
    """路由服务"""

    def __init__(self):
        self.router = Router()

    def prepare_model_list_request(
        self,
        api_key: str
    ) -> Tuple[str, str, Dict[str, str], Dict[str, Any]]:
        """根据 API Key 准备对应服务商的模型列表请求。"""
        backend_type = self.router.identify_backend_by_api_key(api_key)
        backend_config = config.get_backend_config(backend_type)
        base_url = backend_config["base_url"].rstrip("/")

        if backend_type == "anthropic":
            url = f"{base_url}/v1/models"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            params = {"limit": 1000}
        elif backend_type == "zenmux":
            url = f"{base_url}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            params = {}
        else:
            url = f"{base_url}/v1/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            params = {}

        return backend_type, url, headers, params

    @staticmethod
    def extract_model_ids(payload: Dict[str, Any], backend_type: str) -> List[str]:
        """从兼容 OpenAI/Anthropic 的模型列表响应中提取可选模型。"""
        data = payload.get("data", [])
        if not isinstance(data, list):
            return []

        model_ids = []
        for item in data:
            model_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(model_id, str) and model_id.strip():
                model_ids.append(model_id.strip())

        if backend_type == "openai":
            blocked_fragments = (
                "embedding", "moderation", "whisper", "tts", "transcribe",
                "realtime", "audio", "image", "search-preview",
            )
            model_ids = [
                model_id for model_id in model_ids
                if model_id.lower().startswith(("gpt-", "o1", "o3", "o4", "o5"))
                and not any(fragment in model_id.lower() for fragment in blocked_fragments)
            ]

        # 保留服务商给出的顺序（Anthropic 会把较新的模型放在前面），同时去重。
        return list(dict.fromkeys(model_ids))

    async def list_available_models(self, api_key: str) -> Tuple[str, List[str]]:
        """向识别出的服务商查询当前 API Key 可用的模型。"""
        backend_type, url, headers, params = self.prepare_model_list_request(api_key)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("模型列表接口返回了无法解析的数据") from exc

        model_ids = self.extract_model_ids(payload, backend_type)
        if not model_ids:
            raise ValueError("没有从服务商响应中读取到可用模型")
        return backend_type, model_ids

    async def route_request(
        self,
        parsed_request: Dict[str, Any],
        api_key: str = None
    ) -> Tuple[str, httpx.Response]:
        """
        路由请求到合适的后端

        Args:
            parsed_request: 解析后的请求数据
            api_key: API Key（优先用于路由判断）

        Returns:
            Tuple[str, httpx.Response]: (后端类型, 响应对象)
        """
        # 优先根据API Key格式判断后端
        if api_key:
            backend_type = self.router.identify_backend_by_api_key(api_key)
            backend_config = config.get_backend_config(backend_type)
            logger.info(f"根据API Key格式识别后端: {backend_type}")
        else:
            # 没有API Key时，使用模型名称判断
            model = parsed_request.get("model", "")
            backend_type, backend_config = self.router.get_backend(model)

        url, body, headers = self.router.prepare_request(
            parsed_request,
            backend_type,
            api_key
        )

        # 如果没有传入API Key，使用后端配置的
        if not api_key and backend_config.get("api_key"):
            if backend_type == "anthropic":
                headers["x-api-key"] = backend_config["api_key"]
            else:
                headers["Authorization"] = f"Bearer {backend_config['api_key']}"

        response = await self.router.forward_request(url, body, headers)

        if backend_type == "openai":
            normalized = self.router.normalize_openai_responses_response(response.json())
            response = httpx.Response(
                status_code=response.status_code,
                json=normalized,
                headers=response.headers,
                request=response.request,
            )

        return backend_type, response

    async def route_request_stream(
        self,
        parsed_request: Dict[str, Any],
        api_key: str = None
    ) -> Tuple[str, AsyncGenerator[bytes, None]]:
        """
        流式路由请求

        Args:
            parsed_request: 解析后的请求数据
            api_key: API Key（优先用于路由判断）

        Returns:
            Tuple[str, AsyncGenerator]: (后端类型, 流式数据生成器)
        """
        # 优先根据API Key格式判断后端
        if api_key:
            backend_type = self.router.identify_backend_by_api_key(api_key)
            backend_config = config.get_backend_config(backend_type)
            logger.info(f"根据API Key格式识别后端: {backend_type}")
        else:
            # 没有API Key时，使用模型名称判断
            model = parsed_request.get("model", "")
            backend_type, backend_config = self.router.get_backend(model)

        url, body, headers = self.router.prepare_request(
            parsed_request,
            backend_type,
            api_key
        )

        # 如果没有传入API Key，使用后端配置的
        if not api_key and backend_config.get("api_key"):
            if backend_type == "anthropic":
                headers["x-api-key"] = backend_config["api_key"]
            else:
                headers["Authorization"] = f"Bearer {backend_config['api_key']}"

        # 确保请求体里 stream=true
        body["stream"] = True

        stream_generator = self.router.forward_request_stream(url, body, headers)
        memory_backend = "anthropic" if parsed_request.get("force_anthropic") else backend_type
        if parsed_request.get("enable_memory_recall") and memory_backend in {"anthropic", "openai", "zenmux"}:
            from memory_tool_stream import stream_with_memory_tool
            stream_generator = stream_with_memory_tool(
                stream_generator,
                memory_backend,
                body,
                lambda next_body: self.router.forward_request_stream(url, next_body, headers),
            )
        if backend_type == "openai":
            stream_generator = self.router.normalize_openai_responses_stream(stream_generator)

        return backend_type, stream_generator


# 全局实例
router_service = RouterService()
