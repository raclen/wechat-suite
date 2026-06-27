"""Summarize an exported WeChat chat JSON into Markdown, optionally PNG."""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI

from proxy_env import normalize_proxy_env


LOG = logging.getLogger(__name__)

API_BASES = {
    "deepseek": "https://api.deepseek.com",
    "newapi": "http://127.0.0.1:3000/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
}

DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "newapi": "gpt-4o-mini",
    "qwen": "qwen-plus",
    "openai": "gpt-4o",
}

FILTERED_TYPES = {"system", "sticker"}
FILTERED_CONTENT_PATTERNS = [
    re.compile(r"^\[表情\]$"),
]
SUMMARY_MODES = {"group", "private", "speaker"}


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_config_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def get_group_daily_config(config: dict) -> dict[str, Any]:
    return config.get("group_daily", {}) or {}


def get_chat_summary_config(config: dict) -> dict[str, Any]:
    return config.get("chat_summary", {}) or get_group_daily_config(config)


def resolve_target_date(raw_value: str | None) -> str:
    if not raw_value or raw_value.lower() == "today":
        return datetime.now().strftime("%Y-%m-%d")
    datetime.strptime(raw_value, "%Y-%m-%d")
    return raw_value


def parse_time_boundary(raw_value: str | None, target_date: str, is_end: bool = False) -> datetime | None:
    if not raw_value:
        return None

    value = raw_value.strip()
    if not value:
        return None

    normalized = value.replace("T", " ")
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%H:%M:%S",
        "%H:%M",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(normalized, fmt)
        except ValueError:
            continue

        if fmt == "%Y-%m-%d":
            return parsed.replace(hour=23, minute=59, second=59) if is_end else parsed
        if fmt in ("%H:%M:%S", "%H:%M"):
            day = datetime.strptime(target_date, "%Y-%m-%d")
            return day.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second)
        return parsed

    raise ValueError(
        f"invalid time value: {raw_value}. Use HH:MM, HH:MM:SS, "
        "YYYY-MM-DD HH:MM, or YYYY-MM-DD HH:MM:SS"
    )


def build_period_label(target_date: str, start_at: datetime | None, end_at: datetime | None) -> str:
    if not start_at and not end_at:
        return target_date
    if start_at and end_at:
        if start_at.date() == end_at.date():
            return f"{start_at.strftime('%Y-%m-%d %H:%M')} - {end_at.strftime('%H:%M')}"
        return f"{start_at.strftime('%Y-%m-%d %H:%M')} - {end_at.strftime('%Y-%m-%d %H:%M')}"
    if start_at:
        return f"{start_at.strftime('%Y-%m-%d %H:%M')} 之后"
    return f"{end_at.strftime('%Y-%m-%d %H:%M')} 之前"


def build_period_suffix(start_at: datetime | None, end_at: datetime | None) -> str:
    if not start_at and not end_at:
        return ""
    if start_at and end_at:
        if start_at.date() == end_at.date():
            return f"{start_at.strftime('%H%M')}-{end_at.strftime('%H%M')}"
        return f"{start_at.strftime('%Y%m%d%H%M')}-{end_at.strftime('%Y%m%d%H%M')}"
    if start_at:
        return f"from-{start_at.strftime('%Y%m%d%H%M')}"
    return f"until-{end_at.strftime('%Y%m%d%H%M')}"


def filter_messages(
    messages: list[dict],
    target_date: str,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> list[dict]:
    kept: list[dict] = []
    for msg in messages:
        timestamp = msg.get("timestamp")
        if not timestamp:
            continue

        msg_time = datetime.fromtimestamp(timestamp)
        if start_at or end_at:
            if start_at and msg_time < start_at:
                continue
            if end_at and msg_time > end_at:
                continue
        else:
            msg_date = msg_time.strftime("%Y-%m-%d")
            if msg_date != target_date:
                continue

        msg_type = msg.get("type", "text")
        if msg_type in FILTERED_TYPES:
            continue

        content = (msg.get("content") or "").strip()
        if not content:
            continue

        if any(pattern.search(content) for pattern in FILTERED_CONTENT_PATTERNS):
            continue

        kept.append(msg)

    return kept


def filter_messages_by_speaker(messages: list[dict], speaker: str) -> list[dict]:
    speaker = speaker.strip()
    if not speaker:
        return messages
    return [
        msg for msg in messages
        if (msg.get("sender") or "").strip() == speaker
    ]


def build_chat_text(
    chat_name: str,
    period_label: str,
    messages: list[dict],
    max_messages: int,
    mode: str,
    speaker: str = "",
) -> str:
    if len(messages) > max_messages:
        messages = messages[-max_messages:]

    sender_counts = Counter((msg.get("sender") or "匿名成员") for msg in messages)
    top_senders = "，".join(f"{sender}({count})" for sender, count in sender_counts.most_common(12))
    mode_titles = {
        "group": "群聊",
        "private": "个人对话",
        "speaker": "群聊指定成员",
    }

    lines = [
        f"总结类型: {mode_titles.get(mode, mode)}",
        f"会话: {chat_name}",
        f"时间段: {period_label}",
        f"消息数: {len(messages)}",
    ]
    if mode == "speaker":
        lines.append(f"指定成员: {speaker}")
    if top_senders:
        lines.append(f"发言统计: {top_senders}")

    lines.extend(["", "聊天记录:"])

    for msg in messages:
        sender = (msg.get("sender") or "匿名成员").strip() or "匿名成员"
        content = (msg.get("content") or "").replace("\n", " ").strip()
        if len(content) > 500:
            content = content[:500] + "..."
        time_text = datetime.fromtimestamp(msg["timestamp"]).strftime("%H:%M")
        lines.append(f"[{time_text}] {sender}: {content}")

    return "\n".join(lines)


def build_prompt(chat_text: str, mode: str, chat_name: str, speaker: str = "") -> str:
    if mode == "private":
        return f"""你是一个微信个人对话分析助手。请根据下面某个微信一对一会话在指定日期的聊天记录，输出一份可直接阅读的 Markdown 对话总结。

要求：
1. 站在旁观者视角总结双方当天聊了什么，不要替任何一方编造未出现的信息。
2. 重点提炼真正有信息量的内容，忽略寒暄、刷屏、纯表情、纯图片。
3. 总结主要话题、双方需求/承诺、情绪变化、关键结论、值得后续跟进的问题。
4. 如果出现明确的时间、地点、金额、工具名、产品名、链接方向、配置方案，可以点出来。
5. 如果没有明确待办，就写“未见明确待办”。
6. 输出必须是 Markdown，格式严格如下：

# {{日期}} {chat_name} 个人对话总结

## 今日概览
用 120-220 字概括当天核心内容。

## 重点内容
- 3 到 6 条，每条尽量具体

## 值得关注的信息
- 列出时间、地点、金额、工具、方案、链接方向、资源推荐等

## 后续待跟进
- 如果有就列出
- 没有就写“未见明确待办”

下面是聊天记录：

{chat_text}
"""

    if mode == "speaker":
        return f"""你是一个微信群聊成员发言分析助手。请根据下面某个微信群在指定日期中“{speaker}”的发言记录，输出一份 Markdown 个人发言总结。

要求：
1. 只总结“{speaker}”本人发言中能确认的信息，不要把其他成员的观点归给这个人。
2. 如果记录只包含该成员发言，可以基于上下文极少这一点保持谨慎，不编造对话背景。
3. 提炼该成员当天关注的主题、提出的问题、给出的建议、表达的需求、承诺或待办。
4. 如果出现明确的工具名、产品名、仓库名、报错、价格、配置方案，可以点出来。
5. 如果没有明确待办，就写“未见明确个人待办”。
6. 输出必须是 Markdown，格式严格如下：

# {{日期}} {chat_name} - {speaker} 发言总结

## 今日概览
用 100-180 字概括该成员当天发言重点。

## 主要关注
- 3 到 6 条，每条尽量具体

## 明确信息
- 列出工具、方案、价格、报错、链接方向、资源推荐等

## 后续待跟进
- 如果有就列出
- 没有就写“未见明确个人待办”

下面是聊天记录：

{chat_text}
"""

    return f"""你是一个微信群聊分析助手。请根据下面某个微信群在指定日期的聊天记录，输出一份面向群主/成员都能直接阅读的 Markdown 日报。

要求：
1. 不要使用第一人称“我”，而是站在旁观者视角总结整个群当天发生了什么。
2. 重点提炼真正有信息量的内容，忽略寒暄、刷屏、纯表情、纯图片。
3. 总结群内讨论的主要主题、大家给出的建议、达成的共识、争议点、值得后续跟进的问题。
4. 如果聊天里出现明确的工具名、产品名、仓库名、报错、价格、配置方案，可以点出来。
5. 如果没有明确待办，就写“未见明确群内待办”。
6. 输出必须是 Markdown，格式严格如下：

# {{日期}} {{群名}} 群聊日报

## 今日概览
用 120-220 字概括当天核心讨论。

## 重点话题
- 3 到 6 条，每条尽量具体

## 值得关注的信息
- 列出工具、方案、价格、报错、链接方向、资源推荐等

## 后续待跟进
- 如果有就列出
- 没有就写“未见明确群内待办”

下面是聊天记录：

{chat_text}
"""


def call_model(config: dict, prompt: str) -> str:
    ai_cfg = config["ai"]
    provider = ai_cfg.get("provider", "deepseek").lower()
    api_key = ai_cfg["api_key"]
    base_url = ai_cfg.get("base_url") or API_BASES.get(provider, API_BASES["deepseek"])
    requested_model = ai_cfg.get("model") or DEFAULT_MODELS.get(provider, DEFAULT_MODELS["deepseek"])
    fallback_models = list(dict.fromkeys([requested_model, DEFAULT_MODELS.get(provider, requested_model)]))

    client = OpenAI(api_key=api_key, base_url=base_url)
    last_error: Exception | None = None

    for model in fallback_models:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=ai_cfg.get("max_tokens", 4096),
                temperature=0.3,
            )
            LOG.info("summary generated with model=%s", model)
            return response.choices[0].message.content or ""
        except Exception as exc:  # pragma: no cover - network/API error path
            last_error = exc
            LOG.warning("model=%s failed: %s", model, exc)

    if last_error is not None:
        raise last_error
    raise RuntimeError("no model available")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-") or "chat"


def write_markdown(
    output_dir: Path,
    target_date: str,
    chat_name: str,
    content: str,
    mode: str = "group",
    speaker: str = "",
    period_suffix: str = "",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    name_parts = [target_date, safe_name(chat_name)]
    if period_suffix:
        name_parts.append(safe_name(period_suffix))
    if mode == "speaker" and speaker:
        name_parts.append(safe_name(speaker))
    if mode == "private":
        name_parts.append("private")
    elif mode == "speaker":
        name_parts.append("speaker")
    name_parts.append("summary.md")
    path = output_dir / "-".join(name_parts)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def inline_markdown(value: str) -> str:
    escaped = html.escape(value)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


def markdown_to_html_body(markdown: str) -> str:
    lines = markdown.splitlines()
    html_lines: list[str] = []
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            html_lines.append("</ul>")
            list_open = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            close_list()
            continue

        if stripped.startswith("# "):
            close_list()
            html_lines.append(f"<h1>{inline_markdown(stripped[2:].strip())}</h1>")
        elif stripped.startswith("## "):
            close_list()
            html_lines.append(f"<h2>{inline_markdown(stripped[3:].strip())}</h2>")
        elif stripped.startswith("### "):
            close_list()
            html_lines.append(f"<h3>{inline_markdown(stripped[4:].strip())}</h3>")
        elif stripped.startswith(("- ", "* ")):
            if not list_open:
                html_lines.append("<ul>")
                list_open = True
            html_lines.append(f"<li>{inline_markdown(stripped[2:].strip())}</li>")
        elif re.match(r"^\d+\.\s+", stripped):
            close_list()
            text = re.sub(r"^\d+\.\s+", "", stripped)
            html_lines.append(f"<p>{inline_markdown(text)}</p>")
        else:
            close_list()
            html_lines.append(f"<p>{inline_markdown(stripped)}</p>")

    close_list()
    return "\n".join(html_lines)


def build_figma_html(markdown: str) -> str:
    body = markdown_to_html_body(markdown)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      --ink: #172033;
      --muted: #647084;
      --line: #d9dee8;
      --panel: #ffffff;
      --accent: #18a0fb;
      --accent-2: #f24e1e;
      --soft: #f5f7fb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      width: 1200px;
      min-height: 720px;
      color: var(--ink);
      font-family: Inter, "SF Pro Display", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      background:
        linear-gradient(90deg, rgba(23, 32, 51, .045) 1px, transparent 1px),
        linear-gradient(rgba(23, 32, 51, .045) 1px, transparent 1px),
        #f8fafc;
      background-size: 24px 24px;
      padding: 56px;
    }}
    .frame {{
      width: 1088px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 28px 70px rgba(23, 32, 51, .14);
      overflow: hidden;
    }}
    .toolbar {{
      height: 48px;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfcff;
    }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; }}
    .dot.red {{ background: var(--accent-2); }}
    .dot.yellow {{ background: #ffcd29; }}
    .dot.green {{ background: #0acf83; }}
    .label {{
      margin-left: 10px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }}
    .content {{
      padding: 54px 64px 64px;
    }}
    h1 {{
      margin: 0 0 28px;
      color: #0e1729;
      font-size: 34px;
      line-height: 1.22;
      font-weight: 760;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 34px 0 14px;
      color: #111827;
      font-size: 21px;
      line-height: 1.35;
      font-weight: 720;
      letter-spacing: 0;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    h2::before {{
      content: "";
      width: 7px;
      height: 22px;
      border-radius: 3px;
      background: var(--accent);
      display: inline-block;
    }}
    h3 {{
      margin: 24px 0 10px;
      font-size: 17px;
      line-height: 1.45;
    }}
    p, li {{
      color: #253047;
      font-size: 17px;
      line-height: 1.78;
      letter-spacing: 0;
    }}
    p {{
      margin: 0 0 13px;
    }}
    ul {{
      margin: 0 0 16px;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 10px;
    }}
    li {{
      position: relative;
      padding: 13px 16px 13px 38px;
      background: var(--soft);
      border: 1px solid #e7ebf2;
      border-radius: 8px;
    }}
    li::before {{
      content: "";
      position: absolute;
      left: 17px;
      top: 25px;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent);
    }}
    code {{
      padding: 2px 6px;
      border-radius: 6px;
      background: #eef2f8;
      color: #0f766e;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: .92em;
    }}
    strong {{ color: #101828; }}
  </style>
</head>
<body>
  <main class="frame">
    <div class="toolbar">
      <span class="dot red"></span>
      <span class="dot yellow"></span>
      <span class="dot green"></span>
      <span class="label">WeChat Summary</span>
    </div>
    <section class="content">
      {body}
    </section>
  </main>
</body>
</html>
"""


def markdown_file_to_png(markdown_path: Path, png_path: Path | None = None) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "PNG 导出需要安装 Playwright: .venv/bin/pip install -r requirements.txt && "
            ".venv/bin/python -m playwright install chromium"
        ) from exc

    markdown = markdown_path.read_text(encoding="utf-8")
    html_content = build_figma_html(markdown)
    png_path = png_path or markdown_path.with_suffix(".png")
    png_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900}, device_scale_factor=2)
        page.set_content(html_content, wait_until="networkidle")
        page.locator(".frame").screenshot(path=str(png_path))
        browser.close()

    return png_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize exported chat JSON to Markdown.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--input", help="Path to exported chat JSON")
    parser.add_argument("--date", help='Target date in YYYY-MM-DD, or "today"')
    parser.add_argument("--start-time", help="Start time: HH:MM or YYYY-MM-DD HH:MM")
    parser.add_argument("--end-time", help="End time: HH:MM or YYYY-MM-DD HH:MM")
    parser.add_argument("--output-dir", help="Directory for generated markdown")
    parser.add_argument("--chat-name", help="Override chat name shown in the report")
    parser.add_argument("--mode", choices=sorted(SUMMARY_MODES), help="Summary mode: group, private, or speaker")
    parser.add_argument("--speaker", help="Only summarize this sender in a group chat")
    parser.add_argument("--png", action="store_true", help="Also render the generated Markdown to a Figma-style PNG")
    parser.add_argument("--png-output", help="Optional PNG output path")
    parser.add_argument("--max-messages", type=int, help="Maximum number of messages sent to the model")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    normalize_proxy_env()

    config_path = Path(args.config).resolve()
    config = load_config(str(config_path))
    summary_cfg = get_chat_summary_config(config)

    chat_name = args.chat_name or summary_cfg.get("chat_name")
    default_input = f"markdown_exports/{safe_name(chat_name or 'chat')}-export.json"
    input_value = args.input or summary_cfg.get("input_json") or default_input
    output_value = args.output_dir or summary_cfg.get("output_dir", "group_daily_exports")
    target_date = resolve_target_date(args.date or summary_cfg.get("date"))
    start_at = parse_time_boundary(args.start_time or summary_cfg.get("start_time"), target_date)
    end_at = parse_time_boundary(args.end_time or summary_cfg.get("end_time"), target_date, is_end=True)
    if start_at and end_at and start_at > end_at:
        raise SystemExit("start_time must be earlier than or equal to end_time")
    period_label = build_period_label(target_date, start_at, end_at)
    period_suffix = build_period_suffix(start_at, end_at)
    max_messages = args.max_messages or int(summary_cfg.get("max_messages", 260))
    mode = args.mode or summary_cfg.get("mode", "group")
    speaker = args.speaker or summary_cfg.get("speaker", "")
    render_png = args.png or bool(summary_cfg.get("render_png", False))
    png_output = args.png_output or summary_cfg.get("png_output")

    if mode not in SUMMARY_MODES:
        raise SystemExit(f"invalid mode: {mode}. choose from {', '.join(sorted(SUMMARY_MODES))}")
    if mode == "speaker" and not speaker:
        raise SystemExit("speaker mode requires --speaker or chat_summary.speaker")

    input_path = resolve_config_path(config_path, input_value)
    output_dir = resolve_config_path(config_path, output_value)

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    chat_name = chat_name or payload.get("chat", "微信群")
    messages = filter_messages(payload.get("messages", []), target_date, start_at, end_at)
    if mode == "speaker":
        messages = filter_messages_by_speaker(messages, speaker)
    if not messages:
        target = period_label
        if mode == "speaker":
            target += f" and speaker {speaker}"
        raise SystemExit(f"no messages found for {target}")

    if mode == "private" and payload.get("is_group"):
        LOG.warning("input JSON is marked as group chat, but mode=private was requested")

    chat_text = build_chat_text(chat_name, period_label, messages, max_messages, mode, speaker)
    prompt = build_prompt(chat_text, mode, chat_name, speaker)
    markdown = call_model(config, prompt)
    output_path = write_markdown(output_dir, target_date, chat_name, markdown, mode, speaker, period_suffix)

    print(output_path)
    if render_png:
        png_path = resolve_config_path(config_path, png_output) if png_output else None
        print(markdown_file_to_png(output_path, png_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
