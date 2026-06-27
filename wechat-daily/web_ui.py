"""Local browser UI for configuring and generating WeChat summaries."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml


PROJECT_DIR = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_DIR.parent


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def default_config_path() -> Path:
    local_path = PROJECT_DIR / "config.local.yaml"
    if local_path.exists():
        return local_path
    return PROJECT_DIR / "config.yaml"


def project_python() -> Path:
    venv_python = PROJECT_DIR / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def list_export_jsons() -> list[str]:
    export_dir = PROJECT_DIR / "markdown_exports"
    if not export_dir.exists():
        return []
    return [
        str(path.relative_to(PROJECT_DIR))
        for path in sorted(export_dir.glob("*.json"))
    ]


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-") or "chat"


def find_markdown_path(output: str) -> Path | None:
    for raw in reversed(output.splitlines()):
        line = raw.strip()
        if line.endswith(".md"):
            path = Path(line)
            if path.exists():
                return path
    for match in reversed(re.findall(r"(/[^\s]+\.md)", output)):
        path = Path(match)
        if path.exists():
            return path
    return None


def run_generation(config_path: Path, payload: dict) -> dict:
    chat_name = (payload.get("chat_name") or "").strip()
    if not chat_name:
        raise ValueError("请填写 chat_name")

    date = (payload.get("date") or "today").strip()
    mode = (payload.get("mode") or "group").strip()
    speaker = (payload.get("speaker") or "").strip()
    start_time = (payload.get("start_time") or "").strip()
    end_time = (payload.get("end_time") or "").strip()
    output_dir = (payload.get("output_dir") or "group_daily_exports").strip()
    export_first = bool(payload.get("export_first", True))
    input_json = (payload.get("input_json") or "").strip()
    if not input_json:
        input_json = f"markdown_exports/{safe_name(chat_name)}-export.json"

    python = str(project_python())
    if export_first:
        command = [
            python,
            str(PROJECT_DIR / "run_group_daily_pipeline.py"),
            "--config",
            str(config_path),
            "--chat-name",
            chat_name,
            "--date",
            date,
            "--input",
            input_json,
            "--output-dir",
            output_dir,
            "--mode",
            mode,
        ]
    else:
        command = [
            python,
            str(PROJECT_DIR / "summarize_export_chat.py"),
            "--config",
            str(config_path),
            "--chat-name",
            chat_name,
            "--date",
            date,
            "--input",
            input_json,
            "--output-dir",
            output_dir,
            "--mode",
            mode,
        ]

    if speaker:
        command.extend(["--speaker", speaker])
    if start_time:
        command.extend(["--start-time", start_time])
    if end_time:
        command.extend(["--end-time", end_time])

    completed = subprocess.run(
        command,
        cwd=str(PROJECT_DIR),
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    if completed.returncode != 0:
        raise RuntimeError(output.strip() or f"生成失败，退出码 {completed.returncode}")

    markdown_path = find_markdown_path(output)
    if markdown_path is None:
        raise RuntimeError(f"生成完成，但没有找到 Markdown 输出路径。\n\n{output}")

    return {
        "markdown_path": str(markdown_path),
        "markdown": markdown_path.read_text(encoding="utf-8"),
        "log": output,
    }


def build_defaults(config_path: Path) -> dict:
    config = load_config(config_path)
    summary_cfg = config.get("chat_summary", {}) or config.get("group_daily", {}) or {}
    chat_name = summary_cfg.get("chat_name", "")
    return {
        "config_path": str(config_path),
        "chat_name": chat_name,
        "date": summary_cfg.get("date", "today"),
        "mode": summary_cfg.get("mode", "group"),
        "speaker": summary_cfg.get("speaker", ""),
        "start_time": summary_cfg.get("start_time", ""),
        "end_time": summary_cfg.get("end_time", ""),
        "render_png": bool(summary_cfg.get("render_png", True)),
        "output_dir": summary_cfg.get("output_dir", "group_daily_exports"),
        "input_json": summary_cfg.get("input_json") or (
            f"markdown_exports/{safe_name(chat_name)}-export.json" if chat_name else ""
        ),
        "export_first": True,
        "export_jsons": list_export_jsons(),
    }


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, content: str) -> None:
    body = content.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(config_path: Path):
    defaults = build_defaults(config_path)

    class WebUIHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            print("[web_ui] " + fmt % args)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                html_response(self, INDEX_HTML.replace("__DEFAULTS__", json.dumps(defaults, ensure_ascii=False)))
                return
            if parsed.path == "/api/defaults":
                json_response(self, 200, defaults)
                return
            json_response(self, 404, {"error": "not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/generate":
                json_response(self, 404, {"error": "not found"})
                return

            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = run_generation(config_path, payload)
                json_response(self, 200, result)
            except Exception as exc:
                json_response(self, 500, {"error": str(exc)})

    return WebUIHandler


def bind_server(host: str, port: int, handler) -> tuple[ThreadingHTTPServer, int]:
    for candidate in range(port, port + 20):
        try:
            return ThreadingHTTPServer((host, candidate), handler), candidate
        except OSError:
            continue
    raise OSError(f"no available port from {port} to {port + 19}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the local WeChat Summary web UI.")
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    server, actual_port = bind_server(args.host, args.port, make_handler(config_path))
    url = f"http://{args.host}:{actual_port}/"

    print(f"[web_ui] config={config_path}")
    print(f"[web_ui] serving {url}")
    print("[web_ui] press Ctrl+C to stop")

    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[web_ui] stopped")
    finally:
        server.server_close()
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WeChat Summary</title>
  <style>
    :root {
      --bg: #f6f8fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9dee8;
      --soft: #f5f7fb;
      --accent: #18a0fb;
      --accent-2: #f24e1e;
      --ok: #0f8f62;
      --bad: #c2410c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: Inter, "SF Pro Display", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      background:
        linear-gradient(90deg, rgba(23, 32, 51, .045) 1px, transparent 1px),
        linear-gradient(rgba(23, 32, 51, .045) 1px, transparent 1px),
        var(--bg);
      background-size: 24px 24px;
    }
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
    }
    aside {
      min-height: 100vh;
      padding: 18px;
      border-right: 1px solid var(--line);
      background: rgba(255, 255, 255, .86);
      backdrop-filter: blur(12px);
    }
    main {
      padding: 28px;
      overflow: auto;
    }
    .brand {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 18px;
    }
    .brand h1 {
      margin: 0;
      font-size: 18px;
      line-height: 1.25;
      letter-spacing: 0;
    }
    .config-path {
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .field {
      display: grid;
      gap: 7px;
      margin-bottom: 13px;
    }
    label {
      color: #344054;
      font-size: 12px;
      font-weight: 650;
    }
    input, select {
      width: 100%;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      color: var(--ink);
      padding: 0 10px;
      font-size: 14px;
      outline: none;
    }
    input:focus, select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(24, 160, 251, .14);
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .check {
      min-height: 36px;
      display: flex;
      align-items: center;
      gap: 9px;
      color: #344054;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 10px;
    }
    .check input {
      width: 16px;
      height: 16px;
      padding: 0;
    }
    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 16px;
    }
    button {
      height: 40px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      color: var(--ink);
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    button:disabled {
      opacity: .55;
      cursor: progress;
    }
    .status {
      min-height: 22px;
      margin: 14px 0 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
    .status.error { color: var(--bad); }
    .status.ok { color: var(--ok); }
    .workspace {
      display: grid;
      gap: 18px;
      max-width: 1120px;
      margin: 0 auto;
    }
    .report-shell {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 28px 70px rgba(23, 32, 51, .14);
      overflow: hidden;
    }
    .toolbar {
      height: 48px;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfcff;
    }
    .dot { width: 10px; height: 10px; border-radius: 50%; }
    .dot.red { background: var(--accent-2); }
    .dot.yellow { background: #ffcd29; }
    .dot.green { background: #0acf83; }
    .toolbar-label {
      margin-left: 10px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
    }
    #report {
      padding: 54px 64px 64px;
      min-height: 420px;
      background: #fff;
    }
    #report.empty {
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 15px;
    }
    #report h1 {
      margin: 0 0 28px;
      color: #0e1729;
      font-size: 34px;
      line-height: 1.22;
      font-weight: 760;
      letter-spacing: 0;
    }
    #report h2 {
      margin: 34px 0 14px;
      color: #111827;
      font-size: 21px;
      line-height: 1.35;
      font-weight: 720;
      letter-spacing: 0;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    #report h2::before {
      content: "";
      width: 7px;
      height: 22px;
      border-radius: 3px;
      background: var(--accent);
      display: inline-block;
      flex: 0 0 auto;
    }
    #report p, #report li {
      color: #253047;
      font-size: 17px;
      line-height: 1.78;
      letter-spacing: 0;
    }
    #report p { margin: 0 0 13px; }
    #report ul {
      margin: 0 0 16px;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 10px;
    }
    #report li {
      position: relative;
      padding: 13px 16px 13px 38px;
      background: var(--soft);
      border: 1px solid #e7ebf2;
      border-radius: 8px;
    }
    #report li::before {
      content: "";
      position: absolute;
      left: 17px;
      top: 25px;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent);
    }
    #report code {
      padding: 2px 6px;
      border-radius: 6px;
      background: #eef2f8;
      color: #0f766e;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: .92em;
    }
    .log {
      display: none;
      margin: 0;
      padding: 14px;
      max-height: 220px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, .78);
      color: #344054;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
    }
    .log.show { display: block; }
    @media (max-width: 900px) {
      .app { grid-template-columns: 1fr; }
      aside { min-height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
      main { padding: 16px; }
      #report { padding: 34px 24px 40px; }
      #report h1 { font-size: 26px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <div class="brand">
        <h1>WeChat Summary</h1>
      </div>
      <p class="config-path" id="configPath"></p>

      <div class="field">
        <label for="chatName">会话</label>
        <input id="chatName" autocomplete="off">
      </div>

      <div class="field">
        <label for="inputJson">导出 JSON</label>
        <input id="inputJson" list="jsonList" autocomplete="off">
        <datalist id="jsonList"></datalist>
      </div>

      <div class="row">
        <div class="field">
          <label for="date">日期</label>
          <input id="date" autocomplete="off">
        </div>
        <div class="field">
          <label for="mode">模式</label>
          <select id="mode">
            <option value="group">群聊</option>
            <option value="private">个人</option>
            <option value="speaker">成员</option>
          </select>
        </div>
      </div>

      <div class="field">
        <label for="speaker">成员</label>
        <input id="speaker" autocomplete="off">
      </div>

      <div class="row">
        <div class="field">
          <label for="startTime">开始</label>
          <input id="startTime" placeholder="09:00">
        </div>
        <div class="field">
          <label for="endTime">结束</label>
          <input id="endTime" placeholder="18:30">
        </div>
      </div>

      <div class="field">
        <label for="outputDir">输出目录</label>
        <input id="outputDir" autocomplete="off">
      </div>

      <label class="check"><input id="exportFirst" type="checkbox">重新导出聊天记录</label>
      <label class="check"><input id="autoPng" type="checkbox">生成后下载 PNG</label>

      <div class="actions">
        <button class="primary" id="generateBtn">生成</button>
        <button id="downloadBtn" disabled>下载 PNG</button>
      </div>
      <p class="status" id="status"></p>
    </aside>

    <main>
      <div class="workspace">
        <section class="report-shell" id="capture">
          <div class="toolbar">
            <span class="dot red"></span>
            <span class="dot yellow"></span>
            <span class="dot green"></span>
            <span class="toolbar-label">WeChat Summary</span>
          </div>
          <article id="report" class="empty">生成后的总结会出现在这里</article>
        </section>
        <pre class="log" id="log"></pre>
      </div>
    </main>
  </div>

  <script>
    const defaults = __DEFAULTS__;
    const fields = {
      chatName: document.getElementById("chatName"),
      inputJson: document.getElementById("inputJson"),
      date: document.getElementById("date"),
      mode: document.getElementById("mode"),
      speaker: document.getElementById("speaker"),
      startTime: document.getElementById("startTime"),
      endTime: document.getElementById("endTime"),
      outputDir: document.getElementById("outputDir"),
      exportFirst: document.getElementById("exportFirst"),
      autoPng: document.getElementById("autoPng")
    };
    const statusEl = document.getElementById("status");
    const reportEl = document.getElementById("report");
    const logEl = document.getElementById("log");
    const generateBtn = document.getElementById("generateBtn");
    const downloadBtn = document.getElementById("downloadBtn");
    let currentMarkdown = "";

    document.getElementById("configPath").textContent = defaults.config_path || "";
    fields.chatName.value = defaults.chat_name || "";
    fields.inputJson.value = defaults.input_json || "";
    fields.date.value = defaults.date || "today";
    fields.mode.value = defaults.mode || "group";
    fields.speaker.value = defaults.speaker || "";
    fields.startTime.value = defaults.start_time || "";
    fields.endTime.value = defaults.end_time || "";
    fields.outputDir.value = defaults.output_dir || "group_daily_exports";
    fields.exportFirst.checked = defaults.export_first !== false;
    fields.autoPng.checked = !!defaults.render_png;

    const jsonList = document.getElementById("jsonList");
    (defaults.export_jsons || []).forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      jsonList.appendChild(option);
    });

    function payload() {
      return {
        chat_name: fields.chatName.value.trim(),
        input_json: fields.inputJson.value.trim(),
        date: fields.date.value.trim(),
        mode: fields.mode.value,
        speaker: fields.speaker.value.trim(),
        start_time: fields.startTime.value.trim(),
        end_time: fields.endTime.value.trim(),
        output_dir: fields.outputDir.value.trim(),
        export_first: fields.exportFirst.checked
      };
    }

    function setStatus(text, kind = "") {
      statusEl.textContent = text;
      statusEl.className = "status" + (kind ? " " + kind : "");
    }

    function escapeHtml(value) {
      return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }

    function inlineMarkdown(value) {
      return escapeHtml(value)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/`(.+?)`/g, "<code>$1</code>");
    }

    function markdownToHtml(markdown) {
      const lines = markdown.split(/\r?\n/);
      const html = [];
      let listOpen = false;
      const closeList = () => {
        if (listOpen) {
          html.push("</ul>");
          listOpen = false;
        }
      };

      for (const raw of lines) {
        const line = raw.trim();
        if (!line) {
          closeList();
          continue;
        }
        if (line.startsWith("# ")) {
          closeList();
          html.push(`<h1>${inlineMarkdown(line.slice(2).trim())}</h1>`);
        } else if (line.startsWith("## ")) {
          closeList();
          html.push(`<h2>${inlineMarkdown(line.slice(3).trim())}</h2>`);
        } else if (line.startsWith("### ")) {
          closeList();
          html.push(`<h3>${inlineMarkdown(line.slice(4).trim())}</h3>`);
        } else if (line.startsWith("- ") || line.startsWith("* ")) {
          if (!listOpen) {
            html.push("<ul>");
            listOpen = true;
          }
          html.push(`<li>${inlineMarkdown(line.slice(2).trim())}</li>`);
        } else if (/^\d+\.\s+/.test(line)) {
          closeList();
          html.push(`<p>${inlineMarkdown(line.replace(/^\d+\.\s+/, ""))}</p>`);
        } else {
          closeList();
          html.push(`<p>${inlineMarkdown(line)}</p>`);
        }
      }
      closeList();
      return html.join("");
    }

    function renderMarkdown(markdown) {
      currentMarkdown = markdown || "";
      reportEl.classList.remove("empty");
      reportEl.innerHTML = markdownToHtml(markdown);
      downloadBtn.disabled = false;
    }

    async function generate() {
      generateBtn.disabled = true;
      downloadBtn.disabled = true;
      logEl.classList.remove("show");
      logEl.textContent = "";
      setStatus("生成中，模型调用可能需要一会儿...");

      try {
        const response = await fetch("/api/generate", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload())
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "生成失败");

        renderMarkdown(data.markdown || "");
        logEl.textContent = data.log || "";
        logEl.classList.add("show");
        setStatus(`已生成：${data.markdown_path}`, "ok");
        if (fields.autoPng.checked) {
          await downloadPng();
        }
      } catch (error) {
        setStatus(error.message, "error");
      } finally {
        generateBtn.disabled = false;
      }
    }

    function currentFileBase() {
      const chat = (fields.chatName.value || "wechat-summary").replace(/[^\w.-]+/g, "_");
      const date = (fields.date.value || "today").replace(/[^\w.-]+/g, "_");
      return `${date}-${chat}-summary`;
    }

    function plainMarkdown(value) {
      return value
        .replace(/\*\*(.+?)\*\*/g, "$1")
        .replace(/`(.+?)`/g, "$1")
        .replace(/<[^>]+>/g, "");
    }

    function canvasFont(size, weight = 400) {
      return `${weight} ${size}px Inter, "SF Pro Display", "PingFang SC", "Microsoft YaHei", Arial, sans-serif`;
    }

    function wrapCanvasText(ctx, text, maxWidth) {
      const chars = Array.from(text);
      const lines = [];
      let line = "";
      for (const char of chars) {
        const test = line + char;
        if (line && ctx.measureText(test).width > maxWidth) {
          lines.push(line);
          line = char.trimStart();
        } else {
          line = test;
        }
      }
      if (line) lines.push(line);
      return lines.length ? lines : [""];
    }

    function reportBlocks(markdown) {
      const blocks = [];
      for (const raw of markdown.split(/\r?\n/)) {
        const line = raw.trim();
        if (!line) continue;
        if (line.startsWith("# ")) {
          blocks.push({type: "h1", text: plainMarkdown(line.slice(2).trim())});
        } else if (line.startsWith("## ")) {
          blocks.push({type: "h2", text: plainMarkdown(line.slice(3).trim())});
        } else if (line.startsWith("### ")) {
          blocks.push({type: "h3", text: plainMarkdown(line.slice(4).trim())});
        } else if (line.startsWith("- ") || line.startsWith("* ")) {
          blocks.push({type: "li", text: plainMarkdown(line.slice(2).trim())});
        } else if (/^\d+\.\s+/.test(line)) {
          blocks.push({type: "p", text: plainMarkdown(line.replace(/^\d+\.\s+/, ""))});
        } else {
          blocks.push({type: "p", text: plainMarkdown(line)});
        }
      }
      return blocks;
    }

    function roundedRect(ctx, x, y, width, height, radius) {
      const r = Math.min(radius, width / 2, height / 2);
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + width, y, x + width, y + height, r);
      ctx.arcTo(x + width, y + height, x, y + height, r);
      ctx.arcTo(x, y + height, x, y, r);
      ctx.arcTo(x, y, x + width, y, r);
      ctx.closePath();
    }

    function drawWrappedText(ctx, lines, x, y, lineHeight) {
      for (const line of lines) {
        ctx.fillText(line, x, y);
        y += lineHeight;
      }
      return y;
    }

    function layoutReport(ctx, markdown, draw = false) {
      const width = 1088;
      const contentX = 64;
      const contentW = width - contentX * 2;
      let y = 48 + 54;

      if (draw) {
        ctx.fillStyle = "#ffffff";
        roundedRect(ctx, 0, 0, width, 48, 8);
        ctx.fill();
        ctx.fillStyle = "#fbfcff";
        ctx.fillRect(0, 0, width, 48);
        ctx.strokeStyle = "#d9dee8";
        ctx.beginPath();
        ctx.moveTo(0, 48);
        ctx.lineTo(width, 48);
        ctx.stroke();
        [["#f24e1e", 18], ["#ffcd29", 36], ["#0acf83", 54]].forEach(([color, x]) => {
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(x + 5, 24, 5, 0, Math.PI * 2);
          ctx.fill();
        });
        ctx.font = canvasFont(13, 650);
        ctx.fillStyle = "#647084";
        ctx.fillText("WeChat Summary", 82, 29);
      }

      const blocks = reportBlocks(markdown);
      for (const block of blocks) {
        if (block.type === "h1") {
          ctx.font = canvasFont(34, 760);
          const lines = wrapCanvasText(ctx, block.text, contentW);
          if (draw) {
            ctx.fillStyle = "#0e1729";
            drawWrappedText(ctx, lines, contentX, y + 34, 41);
          }
          y += lines.length * 41 + 28;
        } else if (block.type === "h2") {
          y += 34;
          ctx.font = canvasFont(21, 720);
          const lines = wrapCanvasText(ctx, block.text, contentW - 22);
          if (draw) {
            ctx.fillStyle = "#18a0fb";
            roundedRect(ctx, contentX, y + 2, 7, 22, 3);
            ctx.fill();
            ctx.fillStyle = "#111827";
            drawWrappedText(ctx, lines, contentX + 17, y + 22, 28);
          }
          y += Math.max(28, lines.length * 28) + 14;
        } else if (block.type === "h3") {
          y += 24;
          ctx.font = canvasFont(17, 700);
          const lines = wrapCanvasText(ctx, block.text, contentW);
          if (draw) {
            ctx.fillStyle = "#172033";
            drawWrappedText(ctx, lines, contentX, y + 20, 25);
          }
          y += lines.length * 25 + 10;
        } else if (block.type === "li") {
          ctx.font = canvasFont(17, 400);
          const lines = wrapCanvasText(ctx, block.text, contentW - 54);
          const boxH = lines.length * 30 + 26;
          if (draw) {
            ctx.fillStyle = "#f5f7fb";
            roundedRect(ctx, contentX, y, contentW, boxH, 8);
            ctx.fill();
            ctx.strokeStyle = "#e7ebf2";
            ctx.stroke();
            ctx.fillStyle = "#18a0fb";
            ctx.beginPath();
            ctx.arc(contentX + 20, y + 25, 3.5, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = "#253047";
            drawWrappedText(ctx, lines, contentX + 38, y + 34, 30);
          }
          y += boxH + 10;
        } else {
          ctx.font = canvasFont(17, 400);
          const lines = wrapCanvasText(ctx, block.text, contentW);
          if (draw) {
            ctx.fillStyle = "#253047";
            drawWrappedText(ctx, lines, contentX, y + 22, 30);
          }
          y += lines.length * 30 + 13;
        }
      }
      return Math.max(y + 64, 420 + 48);
    }

    async function downloadPng() {
      if (!currentMarkdown.trim()) return;
      const width = 1088;
      const measure = document.createElement("canvas").getContext("2d");
      const height = Math.ceil(layoutReport(measure, currentMarkdown, false));
      const scale = 2;
      const canvas = document.createElement("canvas");
      canvas.width = width * scale;
      canvas.height = height * scale;
      const ctx = canvas.getContext("2d");
      ctx.scale(scale, scale);
      ctx.fillStyle = "#ffffff";
      roundedRect(ctx, 0, 0, width, height, 8);
      ctx.fill();
      ctx.strokeStyle = "#d9dee8";
      ctx.stroke();
      layoutReport(ctx, currentMarkdown, true);

      const a = document.createElement("a");
      a.download = `${currentFileBase()}.png`;
      a.href = canvas.toDataURL("image/png");
      a.click();
    }

    generateBtn.addEventListener("click", generate);
    downloadBtn.addEventListener("click", downloadPng);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
