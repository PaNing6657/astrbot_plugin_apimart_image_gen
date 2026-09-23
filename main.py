import asyncio
import base64
import binascii
import json
import os
import re
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

TOOL_NAME = "generate_image"
TOOL_DESCRIPTION = (
    "根据文字描述生成或编辑图片（文生图 / 图生图）。"
    "当用户要求画图、画画、生成图片、绘制插画 / 立绘 / 头像 / 海报 / 壁纸，"
    "或要求修改、重绘已有图片时，必须调用本工具。"
    "不要用代码解释器、PIL、matplotlib 等方式绘图，也不要回复自己无法生成图片。"
)
TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "必填。画面描述，建议写清主体、外貌特征、场景、构图、风格与光线",
        },
        "size": {
            "type": "string",
            "description": "画面比例或尺寸，如 1:1、16:9、1024x1024；不填则由服务端决定",
        },
        "resolution": {
            "type": "string",
            "enum": ["1k", "2k", "4k"],
            "description": "输出分辨率，不填则使用插件默认配置",
        },
        "quality": {
            "type": "string",
            "enum": ["auto", "low", "medium", "high", "xhigh", "max"],
            "description": "生成质量，不填则使用插件默认配置",
        },
        "n": {
            "type": "number",
            "description": "生成数量，取值范围 1 到 4",
        },
    },
    "required": ["prompt"],
}


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


def to_positive_int(value: Any, default: int = 1) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


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


def build_generate_image_tool(plugin: "APIMartImageGenPlugin") -> FunctionTool:
    """构造 LLM 工具。

    AstrBot 调用工具时优先使用 ``handler`` 字段（``handler(event, **kwargs)``），
    因此这里直接实例化框架自带的 ``FunctionTool``，不做任何 dataclass 继承，
    避免不同 AstrBot / pydantic 版本的字段校验差异导致插件加载失败。
    """

    async def handler(
        event: AstrMessageEvent,
        prompt: str,
        size: str | None = None,
        resolution: str | None = None,
        quality: str | None = None,
        n: Any = None,
    ) -> str:
        """生成图片并把结果发送到当前会话。"""
        try:
            image_refs = await plugin.generate_images(
                prompt=prompt,
                size=size,
                resolution=resolution,
                quality=quality,
                n=to_positive_int(n, 1),
            )
        except Exception as exc:
            logger.exception("APIMart 图像生成失败")
            message = f"图片生成失败：{exc}"
            await send_result(event, event.plain_result(message))
            return message

        for image_ref in image_refs:
            await send_result(event, event.image_result(image_ref))
        return f"图片已生成，共 {len(image_refs)} 张。"

    return FunctionTool(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=TOOL_PARAMETERS,
        handler=handler,
    )


async def send_result(event: AstrMessageEvent, result: Any) -> None:
    send = getattr(event, "send", None)
    if callable(send):
        outcome = send(result)
        if asyncio.iscoroutine(outcome):
            await outcome


@register(
    "astrbot_plugin_apimart_image_gen",
    "STCaoMei",
    "通过 APIMart GPT Image 2.5 生成图片，并由 LLM 意图自动调用。",
    "1.0.2",
)
class APIMartImageGenPlugin(Star):
    def __init__(self, context: Context, config: dict[str, Any] | None = None):
        super().__init__(context)
        self.config = dict(config) if isinstance(config, dict) else {}
        self._session: aiohttp.ClientSession | None = None
        self._tools_registered = False
        self._image_dir = self._resolve_image_dir()
        self._register_tool()

    def _resolve_image_dir(self) -> str:
        try:
            base_dir = StarTools.get_data_dir()
        except Exception:
            logger.exception("获取插件数据目录失败，Base64 图片将保存到插件目录下的 data/generated")
            base_dir = None
        image_dir = (base_dir / "generated") if base_dir is not None else None
        if image_dir is None:
            image_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "generated")
            os.makedirs(image_dir, exist_ok=True)
            return str(image_dir)
        image_dir.mkdir(parents=True, exist_ok=True)
        return str(image_dir)

    def _register_tool(self) -> None:
        add_llm_tools = getattr(self.context, "add_llm_tools", None)
        if not callable(add_llm_tools):
            logger.warning("当前 AstrBot 版本缺少 context.add_llm_tools，LLM 自动生图不可用，仍可使用 /画图 等指令")
            return
        try:
            outcome = add_llm_tools(build_generate_image_tool(self))
        except Exception:
            logger.exception("注册 generate_image 工具失败，仍可使用 /画图 等指令")
            return
        if asyncio.iscoroutine(outcome):
            asyncio.create_task(outcome)
        self._tools_registered = True
        logger.info(f"已注册 LLM 工具 {TOOL_NAME}：模型可在用户要求画图时自动调用，也可用 /画图 指令直接触发")
        self._log_tool_snapshot()

    def _log_tool_snapshot(self) -> None:
        get_manager = getattr(self.context, "get_llm_tool_manager", None)
        if not callable(get_manager):
            return
        try:
            names = [tool.name for tool in get_manager().func_list]
        except Exception:
            logger.exception("读取 LLM 工具列表失败")
            return
        logger.info(f"当前共有 {len(names)} 个 LLM 工具，包含 {TOOL_NAME}：{TOOL_NAME in names}")

    def _config_value(self, key: str, default: Any) -> Any:
        return self.config.get(key, default)

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
                return self._save_inline_images(task, str(task_id))
            if status in {"failed", "cancelled"}:
                error = task.get("error") or {}
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise RuntimeError(detail or f"图像任务状态：{status}")
            if status not in {"submitted", "pending", "processing"}:
                raise RuntimeError(f"未知的图像任务状态：{status}")
        raise RuntimeError(f"图像生成等待超时（{max_wait} 秒），任务 ID：{task_id}")

    def _save_inline_images(self, task: dict[str, Any], task_id: str) -> list[str]:
        values: list[str] = []
        for item in (task.get("result") or {}).get("images") or []:
            urls = item.get("url") or []
            if isinstance(urls, str):
                urls = [urls]
            values.extend(value for value in urls if isinstance(value, str))

        local_paths: list[str] = []
        for index, value in enumerate(values, start=1):
            decoded = decode_data_image(value)
            if decoded is None:
                continue
            image_bytes, extension = decoded
            path = os.path.join(self._image_dir, f"apimart_{task_id}_{index}.{extension}")
            with open(path, "wb") as image_file:
                image_file.write(image_bytes)
            local_paths.append(path)
        if not local_paths:
            raise RuntimeError("任务已完成，但响应中没有可用的图片 URL 或 Base64 图片")
        return local_paths

    @staticmethod
    def _extract_prompt(message: str) -> str:
        text = (message or "").strip()
        text = re.sub(r"^\s*(?:/|!|！)?(?:画图|生图|生成图片|生成图像|画一张图|帮我画|draw|image)\s*", "", text, flags=re.IGNORECASE)
        return text.strip()

    async def _draw(self, event: AstrMessageEvent):
        prompt = self._extract_prompt(getattr(event, "message_str", ""))
        if not prompt:
            yield event.plain_result("请在命令后附上画面描述，例如：/画图 一只水彩风格的橘猫")
            return
        try:
            image_refs = await self.generate_images(
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
        for image_ref in image_refs:
            yield event.image_result(image_ref)

    @filter.command("画图")
    async def draw_command(self, event: AstrMessageEvent):
        """/画图 提示词，直接生成图片，不依赖 LLM 工具调用能力。"""
        async for result in self._draw(event):
            yield result

    @filter.command("生图")
    async def image_command(self, event: AstrMessageEvent):
        """/生图 提示词，/画图 的指令别名。"""
        async for result in self._draw(event):
            yield result

    @filter.command("生成图片")
    async def generate_command(self, event: AstrMessageEvent):
        """/生成图片 提示词，/画图 的指令别名。"""
        async for result in self._draw(event):
            yield result

    async def terminate(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
