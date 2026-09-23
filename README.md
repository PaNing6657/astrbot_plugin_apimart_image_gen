# APIMart GPT Image 2.5 AstrBot 插件

AstrBot 插件通过 APIMart 的 GPT Image 2.5 接口生成图片。LLM 可在用户明确提出画图、生图或图片创作需求时自动调用 `generate_image` 工具；也可以使用 `/画图 提示词` 等显式指令。

## 功能

- 支持 `gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`
- 提交异步生成任务并轮询任务状态，完成后将图片直接发送到当前会话
- 支持聊天中指定提示词、比例/尺寸、分辨率、质量和张数
- 插件配置页可设置 API Key、兼容接口 Base URL、默认模型、分辨率、质量、图片格式和超时
- 支持 `APIMART_API_KEY` 环境变量提供密钥
- Base64 图片返回会保存到 AstrBot 插件数据目录下的 `generated/`

## 安装

将 `astrbot_plugin_apimart_image_gen` 目录复制到 AstrBot 的 `data/plugins/` 下，重启或在管理面板重载插件。首次加载时 AstrBot 会依据 `requirements.txt` 安装 `aiohttp`。

## 配置

在 AstrBot 管理面板打开插件配置，填写：

1. `APIMart API Key`
2. API Base URL 默认 `https://api.apimart.ai`（不要附加 `/v1`）
3. 模型及默认输出质量、分辨率等

也可为 AstrBot 进程设置环境变量 `APIMART_API_KEY`。配置项中的 API Key 为空时会读取该环境变量。

## 使用

自然语言表达图片需求：

- `帮我画一只戴围巾的橘猫，水彩风格，1:1，2k`
- `生成一张赛博朋克城市夜景海报，16:9，高质量`
- `画一张极简风格的咖啡品牌主视觉`

显式命令（不依赖模型是否支持工具调用，最可靠）：

- `/画图 一只戴围巾的橘猫，水彩风格`
- `/生图 未来城市的日落，16:9`
- `/生成图片 一座山间木屋，清晨雾气`

## 工具注册自检

插件加载时会写入两条日志，便于确认工具是否真的注册成功：

- `已注册 LLM 工具 generate_image：...`
- `当前共有 N 个 LLM 工具，包含 generate_image：True`

若第二条显示 `False`，说明注册被其它插件覆盖或失败，请检查同名的工具或插件的异常日志。

## 排查：模型没有调用生图工具

若日志显示模型调用了别的工具（例如 `astrbot_execute_ipython`）而没有调用 `generate_image`，属于模型侧的工具有限选择问题，与插件本身无关。可采取以下任一措施：

1. 使用 `/画图` 指令，直接触发插件，不经过模型的工具选择。
2. 在人格设定或 Agent 提示词中明确要求：「需要生成图片时必须调用 `generate_image` 工具，不要用代码解释器绘图」。
3. 若不需要代码解释器，在 AstrBot 的 Agent / 工具配置中停用 `astrbot_execute_ipython`，减少干扰项。
4. 换用工具调用能力更强、更遵循指令的模型。

注意：代码解释器报 `Failed to connect to Docker daemon` 属于 AstrBot 沙箱环境问题（容器内未安装或未运行 Docker），与生图插件的调用无关。

## API 行为

按 APIMart 官方文档调用：

- `POST /v1/images/generations` 创建任务
- `GET /v1/tasks/{task_id}?language=zh` 轮询任务
- 从 `data.result.images[].url[]` 获取结果图像 URL 或 Base64 图片

任务等待超时后会在回复中返回任务 ID，方便定位问题；插件不会在本地持久化或记录 API Key。
