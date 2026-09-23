# APIMart GPT Image 2.5 AstrBot 插件

AstrBot 插件通过 APIMart 的 GPT Image 2.5 接口生成图片。LLM 可依据聊天上下文，在用户明确提出画图、生图或图片创作需求时调用 `generate_image` 工具。插件也支持 `/画图 提示词` 等显式指令。

## 功能

- 支持 `gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`
- 提交异步生成任务并轮询任务状态，完成后将图片直接发送到当前会话
- 支持聊天中指定提示词、比例/尺寸、分辨率、质量和张数
- 插件配置页可设置 API Key、兼容接口 Base URL、默认模型、分辨率、质量、图片格式和超时
- 支持 `APIMART_API_KEY` 环境变量提供密钥
- 无 AstrBot 运行时也可通过内置纯函数离线自测

## 安装

将 `astrbot_plugin_apimart_image_gen` 目录复制到 AstrBot 的 `data/plugins/` 下，重启或在管理面板重载插件。首次加载时 AstrBot 会依据 `requirements.txt` 安装 `aiohttp`。

## 配置

在 AstrBot 管理面板打开插件配置，填写：

1. `APIMart API Key`
2. API Base URL 默认 `https://api.apimart.ai`（不要附加 `/v1`）
3. 模型及默认输出质量、分辨率等

也可为 AstrBot 进程设置环境变量 `APIMART_API_KEY`。配置项中的 API Key 为空时会读取该环境变量。

## 使用

可以直接自然语言表达图片需求，例如：

- `帮我画一只戴围巾的橘猫，水彩风格，1:1，2k`
- `生成一张赛博朋克城市夜景海报，16:9，高质量`
- `画一张极简风格的咖啡品牌主视觉`

也可以使用命令：

- `/画图 一只戴围巾的橘猫，水彩风格`
- `/生图 未来城市的日落，16:9`
- `/生成图片 一座山间木屋，清晨雾气`

LLM 工具调用要求当前会话的模型支持 Function Calling/工具调用。如果模型不支持，可使用显式命令。

## API 行为

按 APIMart 官方文档调用：

- `POST /v1/images/generations` 创建任务
- `GET /v1/tasks/{task_id}?language=zh` 轮询任务
- 从 `data.result.images[].url[]` 获取结果图像 URL

任务等待超时后会在回复中返回任务 ID，方便定位问题；插件不会在本地持久化或记录 API Key。

## 离线自测

在插件目录运行：

```bash
python tests/test_plugin_selftest.py
```

测试通过 AstrBot/AIOHTTP stub 覆盖请求参数校验、提交与轮询、工具注册、图片发送和显式命令，不会访问真实 API。
