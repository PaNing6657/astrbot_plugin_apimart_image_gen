import asyncio
import base64
import binascii
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import aiohttp
from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register


DEFAULT_BASE_URL = "https://api.apimart.ai"
DEFAULT_MODEL = "gpt-image-2.5-flare"
DEFAULT_TIMEOUT = 180
DEFAULT_POLL_INTERVAL = 3
DEFAULT_MAX_WAIT = 600


def normalize_base_url(value: str) -> str:
    value = (value or DEFAULT_BASE_URL).strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ValueError("API Base URL 必须以 http:// 或 https:// 开头")
    return value


def extract_image_urls(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    result = data.get("result") or {}
    for item in result.get("images") or []:
        values = item.get("url") or []
        if isinstance(values, str):
            values = [values]
        urls.extend(value for value in values if isinstance(value, str) and value.startswith(("http://", "https://")))
    return urls


def decode_data_image(value: str) -> tuple[bytes, str] | None:
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", value or "", flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return base64.b64decode(match.group(2), validate=True), match.group(1).lower()
    except (binascii.Error, ValueError) as exc:
        raise ValueError("返回了格式错误的 Base64 图片") from exc


def build_generation_payload(
    prompt: str,
    model: str = DEFAULT_MODEL,
    size: str = "auto",
    resolution: str = "1k",
    quality: str = "medium",
    output_format: str = "png",
    n: int = 1,
) -> dict[str, Any]:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("提示词不能为空")
    if not model.strip():
        raise ValueError("模型名称不能为空")
    if not 1 <= n <= 4:
        raise ValueError("生成数量 n 必须在 1 到 4 之间")
    if resolution not in {"1k", "2k", "4k"}:
        raise ValueError("分辨率必须是 1k、2k 或 4k")
    if quality not in {"auto", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("quality 必须为 auto、low、medium、high、xhigh 或 max")
    if output_format not in {"png", "jpeg", "webp"}:
        raise ValueError("output_format 必须为 png、jpeg 或 webp")
    return {
        "model": model,
        "prompt": prompt,
        "size": size.strip() or "auto",
        "resolution": resolution,
        "quality": quality,
        "output_format": output_format,
        "n": n,
    }


@dataclass
class GenerateImageTool(FunctionTool):
    plugin: Any = field(repr=False)
    name: str = "generate_image"
    description: str = "使用 GPT Image 2.5 根据用户描述生成图片。仅当用户明确要求生成、绘制、创作或编辑图片时调用。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "图片的详细画面描述，包含主体、场景、构图和风格"},
            "size": {"type": "string", "description": "画面比例或尺寸，如 1:1、16:9、1024x1024；默认 auto"},
            "resolution": {"type": "string", "enum": ["1k", "2k", "4k"], "description": "输出分辨率，默认 1k"},
            "quality": {"type": "string", "enum": ["auto", "low", "medium", "high", "xhigh", "max"], "description": "生成质量，默认 medium"},
            "n": {"type": "number", "description": "生成数量，1 到 4，默认 1"},
        },
        "required": ["prompt"],
    })

    async def run(
        self,
        event: AstrMessageEvent,
        prompt: str,
        size: str | None = None,
        resolution: str | None = None,
        quality: str | None = None,
        n: int = 1,
    ) -> str:
        """调用图像生成服务，并将生成结果发送给当前会话。"""
        try:
            urls = await self.plugin.generate_images(
                prompt=prompt,
                size=size,
                resolution=resolution,
                quality=quality,
                n=int(n),
            )
        except Exception as exc:
            logger.exception("APIMart 图像生成失败")
            message = f"图片生成失败：{exc}"
            await event.send(event.plain_result(message))
            return message

        for image_ref in urls:
            await event.send(event.image_result(image_ref))
        return f"图片已生成，共 {len(urls)} 张。"


@register(
    "astrbot_plugin_apimart_image_gen",
    "STCaoMei",
    "通过 APIMart GPT Image 2.5 生成图片，并由 LLM 意图自动调用。",
    "1.0.0",
)
class APIMartImageGenPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any]):
        super().__init__(context)
        self.config = dict(config) if isinstance(config, dict) else {}
        self._session: aiohttp.ClientSession | None = None
        self._image_dir = self._get_image_dir()
        self._tools_registered = False
        if hasattr(self.context, "add_llm_tools"):
            result = self.context.add_llm_tools(GenerateImageTool(self))
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
            self._tools_registered = True

    def _config_value(self, key: str, default: Any) -> Any:
        return self.config.get(key, default)

    def _get_image_dir(self) -> str:
        image_dir = StarTools.get_data_dir() / "generated"
        image_dir.mkdir(parents=True, exist_ok=True)
        return str(image_dir)

    def _api_key(self) -> str:
        key = str(self._config_value("api_key", "")).strip()
        if not key:
            key = os.getenv("APIMART_API_KEY", "").strip()
        if not key:
            raise ValueError("尚未配置 APIMart API Key，请在插件配置中填写，或设置 APIMART_API_KEY 环境变量")
        return key

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=float(self._config_value("timeout", DEFAULT_TIMEOUT)))
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        session = await self._get_session()
        try:
            async with session.request(method, url, **kwargs) as response:
                text = await response.text()
                try:
                    payload = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    payload = {}
                if response.status >= 400:
                    detail = payload.get("error") or payload.get("message") or text[:500] or response.reason
                    if isinstance(detail, dict):
                        detail = detail.get("message") or str(detail)
                    raise RuntimeError(f"APIMart HTTP {response.status}: {detail}")
                if not isinstance(payload, dict):
                    raise RuntimeError("APIMart 返回了无效 JSON 对象")
                return payload
        except asyncio.TimeoutError as exc:
            raise RuntimeError("请求 APIMart 超时") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"连接 APIMart 失败：{exc}") from exc

    async def generate_images(
        self,
        prompt: str,
        size: str | None = None,
        resolution: str | None = None,
        quality: str | None = None,
        n: int = 1,
    ) -> list[str]:
        payload = build_generation_payload(
            prompt=prompt,
            model=str(self._config_value("model", DEFAULT_MODEL)),
            size=size or str(self._config_value("size", "auto")),
            resolution=resolution or str(self._config_value("resolution", "1k")),
            quality=quality or str(self._config_value("quality", "medium")),
            output_format=str(self._config_value("output_format", "png")),
            n=n,
        )
        base_url = normalize_base_url(str(self._config_value("api_base_url", DEFAULT_BASE_URL)))
        headers = {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}
        submit = await self._request_json(
            "POST",
            f"{base_url}/v1/images/generations",
            headers=headers,
            json=payload,
        )
        data = submit.get("data")
        task_id = data[0].get("task_id") if isinstance(data, list) and data and isinstance(data[0], dict) else None
        if not task_id:
            message = submit.get("message") or submit.get("error") or "提交生成任务后未返回 task_id"
            raise RuntimeError(str(message))

        poll_interval = max(1.0, min(15.0, float(self._config_value("poll_interval", DEFAULT_POLL_INTERVAL))))
        max_wait = max(30, int(self._config_value("max_wait", DEFAULT_MAX_WAIT)))
        deadline = asyncio.get_running_loop().time() + max_wait
        task_url = f"{base_url}/v1/tasks/{quote(str(task_id), safe='')}"
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(poll_interval, max(0, deadline - asyncio.get_running_loop().time())))
            if asyncio.get_running_loop().time() >= deadline:
                break
            status_response = await self._request_json("GET", task_url, headers=headers, params={"language": "zh"})
            task = status_response.get("data")
            if not isinstance(task, dict):
                raise RuntimeError("查询任务状态时返回了无效数据")
            status = task.get("status")
            if status == "completed":
                urls = extract_image_urls(task)
                if urls:
                    return urls
                inline_images = []
                for item in (task.get("result") or {}).get("images") or []:
                    values = item.get("url") or []
                    if isinstance(values, str):
                        values = [values]
                    inline_images.extend(value for value in values if isinstance(value, str))
                local_paths = []
                for index, value in enumerate(inline_images, start=1):
                    decoded = decode_data_image(value)
                    if decoded is None:
                        continue
                    image_bytes, extension = decoded
                    path = os.path.join(self._image_dir, f"apimart_{task_id}_{index}.{extension}")
                    with open(path, "wb") as image_file:
                        image_file.write(image_bytes)
                    local_paths.append(path)
                if local_paths:
                    return local_paths
                raise RuntimeError("任务已完成，但响应中没有可用的图片 URL 或 Base64 图片")
            if status in {"failed", "cancelled"}:
                error = task.get("error") or {}
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise RuntimeError(detail or f"图像任务状态：{status}")
            if status not in {"submitted", "pending", "processing"}:
                raise RuntimeError(f"未知的图像任务状态：{status}")
        raise RuntimeError(f"图像生成等待超时（{max_wait} 秒），任务 ID：{task_id}")

    @staticmethod
    def _extract_prompt(message: str) -> str:
        text = (message or "").strip()
        text = re.sub(r"^\s*(?:/|!|！)?(?:画图|生图|生成图片|生成图像|画一张图|帮我画|draw|image)\s*", "", text, flags=re.IGNORECASE)
        return text.strip()

    @filter.command("画图")
    async def draw_command(self, event: AstrMessageEvent):
        """直接使用命令生成图片，不依赖 LLM 工具调用能力。"""
        prompt = self._extract_prompt(getattr(event, "message_str", ""))
        if not prompt:
            yield event.plain_result("请在命令后附上画面描述，例如：/画图 一只水彩风格的橘猫")
            return
        try:
            urls = await self.generate_images(
                prompt=prompt,
                size=str(self._config_value("size", "auto")),
                resolution=str(self._config_value("resolution", "1k")),
                quality=str(self._config_value("quality", "medium")),
                n=1,
            )
        except Exception as exc:
            logger.exception("APIMart 图像生成失败")
            yield event.plain_result(f"图片生成失败：{exc}")
            return
        for url in urls:
            yield event.image_result(url)

    @filter.command("生图")
    async def image_command(self, event: AstrMessageEvent):
        """/生图 指令别名。"""
        async for result in self.draw_command(event):
            yield result

    @filter.command("生成图片")
    async def generate_command(self, event: AstrMessageEvent):
        """/生成图片 指令别名。"""
        async for result in self.draw_command(event):
            yield result

    async def terminate(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


async def _offline_selftest() -> None:
    payload = build_generation_payload("  雨中小猫  ", size="16:9", resolution="2k", quality="high", n=2)
    assert payload["prompt"] == "雨中小猫"
    assert payload["n"] == 2
    assert normalize_base_url("https://api.apimart.ai/") == "https://api.apimart.ai"
    assert extract_image_urls({"result": {"images": [{"url": ["https://example.com/a.png"]}]}}) == [
        "https://example.com/a.png"
    ]
    assert APIMartImageGenPlugin._extract_prompt("/画图 一只猫") == "一只猫"
    assert decode_data_image("data:image/png;base64,YQ==") == (b"a", "png")
    for values in (
        {"prompt": " "},
        {"prompt": "cat", "n": 5},
        {"prompt": "cat", "model": ""},
        {"prompt": "cat", "resolution": "8k"},
    ):
        try:
            build_generation_payload(**values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected validation failure for {values}")
    try:
        normalize_base_url("api.apimart.ai")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid base URL to fail")
    print("offline self-test passed")


if __name__ == "__main__":
    asyncio.run(_offline_selftest())
