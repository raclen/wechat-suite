# WeChat Daily

微信聊天自动总结工具。

它可以从真实微信数据库里导出指定群聊或个人对话，再用 DeepSeek、NewAPI 或其他 OpenAI 兼容模型生成当天 Markdown 总结。也支持把 Markdown 渲染成 Figma 风格 PNG，并继续保留原来的 Notion 流程。

## 现在能做什么

- 直接读取真实微信数据，导出指定群聊
- 按天生成群聊总结 Markdown
- 生成微信个人对话总结
- 生成指定群聊中某个人的发言总结
- 支持按开始/结束时间筛选聊天记录，跨天时间段可写完整日期时间
- 提供本地浏览器操作页面，选择配置后点击生成
- 将生成的 Markdown 转成 Figma 风格 PNG
- 也可以继续使用原来的每日总结 + 待办提取流程
- 支持 DeepSeek、NewAPI、OpenAI 兼容接口

## 适用环境

- Linux / Ubuntu
- Python 3.12+
- 已解密的微信数据库或可通过 `wechat-decrypt` 读取的微信数据
- DeepSeek、NewAPI 或其他 OpenAI 兼容模型 API Key

## 快速开始

### 1. 安装依赖

```bash
cd wechat-daily
.venv/bin/pip install -r requirements.txt
```

### 2. 配置

编辑 `config.yaml`。

如果同目录下存在 `config.local.yaml`，`./run_group_daily.sh` 会优先使用它；
命令启动时也会打印当前实际使用的配置文件路径。

如果你只想用“聊天总结”功能，只需要改这几项：

```yaml
chat_summary:
  chat_name: "Walk AI Coding"
  date: "2026-06-23"
  mode: "group"
  render_png: true
```

说明：
- `chat_name` 是群名、联系人显示名、备注名或 wxid
- `date` 是要总结的日期，格式 `YYYY-MM-DD`
- `mode` 可选 `group`、`private`、`speaker`
- `mode: speaker` 时需要配置 `speaker`
- `render_png: true` 会额外生成同名 `.png`
- 导出路径和输出目录会自动使用默认值

使用 NewAPI 时，把 `ai` 改成类似这样：

```yaml
ai:
  provider: "newapi"
  api_key: "YOUR_NEWAPI_API_KEY"
  model: "gpt-4o-mini"
  base_url: "http://127.0.0.1:3000/v1"
```

如果你想改默认输出目录或解密仓库，也可以在 `group_daily` 下继续配置。

## 一键运行

```bash
cd ..
./run.sh
```

如果你想显式指定配置文件：

```bash
./run.sh /abs/path/to/config.yaml
```

它会启动本地 Web 页面，默认地址：

```text
http://127.0.0.1:8765/
```

页面会自动：

1. 从真实微信数据导出指定群聊
2. 生成当天 Markdown
3. 在浏览器里预览 Figma 风格报告
4. 下载 PNG

操作页面示例：

![WeChat Summary Web UI](docs/images/web-ui-summary.png)

页面支持选择群聊、个人对话或群聊指定成员总结，也可以填写日期、开始时间、结束时间。PNG 由浏览器直接生成下载，不需要 Playwright。

如果仍想使用原来的命令行流水线：

```bash
./run_group_daily.sh config.local.yaml
```

## 输出文件

- 导出的 JSON：`markdown_exports/<群名>-export.json`
- 生成的 Markdown：`group_daily_exports/<日期>-<群名>-summary.md`
- 生成的 PNG：`group_daily_exports/<日期>-<群名>-summary.png`

## 常用模式

推荐把参数写进 `config.local.yaml` 的 `chat_summary`，然后只运行：

```bash
./run_group_daily.sh config.local.yaml
```

群聊日报：

```yaml
chat_summary:
  chat_name: "Walk AI Coding"
  date: "2026-06-23"
  mode: "group"
  start_time: "09:00"
  end_time: "18:30"
  render_png: true
  input_json: "markdown_exports/Walk_AI_Coding-export.json"
```

微信个人对话总结：

```yaml
chat_summary:
  chat_name: "某联系人"
  date: "2026-06-23"
  mode: "private"
  start_time: "09:00"
  end_time: "18:30"
  render_png: true
  input_json: "markdown_exports/某联系人-export.json"
```

指定群聊中某个人发言总结：

```yaml
chat_summary:
  chat_name: "Walk AI Coding"
  date: "2026-06-23"
  mode: "speaker"
  speaker: "Walk-gpt"
  start_time: "2026-06-23 23:30"
  end_time: "2026-06-24 02:00"
  render_png: true
  input_json: "markdown_exports/Walk_AI_Coding-export.json"
```

`start_time/end_time` 可留空。只写 `09:00` 这种格式时，表示 `date` 当天的时间；需要跨天时写完整日期时间。

如果使用命令行 `--png` 导出，首次运行前需要安装浏览器内核：

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

## 保留的原有功能

原来的 `main.py` 仍然可用，用于每日总结、任务提取和 Notion 写入。

```bash
python main.py --test
python main.py --console
python main.py --output-dir out --chat "某个群"
```

## 项目结构

- `main.py`：原始每日总结主流程
- `summarize_export_chat.py`：把导出的会话 JSON 生成 Markdown / PNG
- `run_group_daily_pipeline.py`：一键导出 + 一键总结
- `run_group_daily.sh`：最短入口脚本
- `wechat_core/`：直接读微信数据库的核心模块
- `prompts/`：AI prompt 模板

## 注意事项

- `config.yaml` 里包含 API Key，不要提交自己的真实 key 到公开仓库
- `markdown_exports/` 和 `group_daily_exports/` 属于运行产物，可以随时删除
- 如果导出失败，先检查微信数据目录、`wechat-decrypt` 配置和模型 API Key

## 许可证

保留原项目许可证或按你的仓库设置为准。
