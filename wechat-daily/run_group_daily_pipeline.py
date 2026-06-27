"""Export a configured WeChat group chat and generate the daily Markdown report."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from summarize_export_chat import (
    load_config,
    resolve_config_path,
    resolve_target_date,
    safe_name,
)


def find_venv_python(project_dir: Path) -> Path:
    candidates = [
        project_dir / ".venv" / "bin" / "python",
        project_dir / ".venv" / "bin" / "python3",
        project_dir / ".venv" / "Scripts" / "python.exe",
        project_dir / ".venv" / "Scripts" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit(f"missing python in virtualenv: {project_dir / '.venv'}")


def run_export(decrypt_repo: Path, chat_name: str, export_json: Path) -> None:
    decrypt_python = find_venv_python(decrypt_repo)
    export_script = decrypt_repo / "export_chat.py"

    if not export_script.exists():
        raise SystemExit(f"missing export script: {export_script}")

    export_json.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(decrypt_python), str(export_script), chat_name, str(export_json)],
        cwd=str(decrypt_repo),
        check=True,
    )


def run_summary(
    project_dir: Path,
    config_path: Path,
    chat_name: str,
    target_date: str,
    export_json: Path,
    mode: str | None = None,
    speaker: str | None = None,
    render_png: bool = False,
    start_time: str | None = None,
    end_time: str | None = None,
    output_dir: str | None = None,
) -> None:
    summarize_script = project_dir / "summarize_export_chat.py"
    command = [
        sys.executable,
        str(summarize_script),
        "--config",
        str(config_path),
        "--input",
        str(export_json),
        "--date",
        target_date,
        "--chat-name",
        chat_name,
    ]
    if mode:
        command.extend(["--mode", mode])
    if speaker:
        command.extend(["--speaker", speaker])
    if render_png:
        command.append("--png")
    if start_time:
        command.extend(["--start-time", start_time])
    if end_time:
        command.extend(["--end-time", end_time])
    if output_dir:
        command.extend(["--output-dir", output_dir])

    subprocess.run(command, cwd=str(project_dir), check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export WeChat chat and generate daily markdown.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--chat-name", help="Override group_daily.chat_name")
    parser.add_argument("--date", help='Override group_daily.date, format YYYY-MM-DD or "today"')
    parser.add_argument("--input", help="Override group_daily.input_json")
    parser.add_argument("--output-dir", help="Override chat_summary.output_dir")
    parser.add_argument("--decrypt-repo", help="Override group_daily.decrypt_repo")
    parser.add_argument("--mode", choices=["group", "private", "speaker"], help="Summary mode")
    parser.add_argument("--speaker", help="Only summarize this sender in a group chat")
    parser.add_argument("--start-time", help="Start time: HH:MM or YYYY-MM-DD HH:MM")
    parser.add_argument("--end-time", help="End time: HH:MM or YYYY-MM-DD HH:MM")
    parser.add_argument("--png", action="store_true", help="Also render generated Markdown to PNG")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    project_dir = config_path.parent
    config = load_config(str(config_path))
    group_daily_cfg = config.get("chat_summary", {}) or config.get("group_daily", {}) or {}

    chat_name = args.chat_name or group_daily_cfg.get("chat_name")
    if not chat_name:
        raise SystemExit("missing chat name: set group_daily.chat_name in config.yaml or pass --chat-name")

    target_date = resolve_target_date(args.date or group_daily_cfg.get("date"))
    input_value = args.input or group_daily_cfg.get("input_json") or f"markdown_exports/{safe_name(chat_name)}-export.json"
    output_dir = args.output_dir or group_daily_cfg.get("output_dir")
    decrypt_repo_value = args.decrypt_repo or group_daily_cfg.get("decrypt_repo") or "../wechat-decrypt"
    mode = args.mode or group_daily_cfg.get("mode", "group")
    speaker = args.speaker or group_daily_cfg.get("speaker")
    start_time = args.start_time or group_daily_cfg.get("start_time")
    end_time = args.end_time or group_daily_cfg.get("end_time")
    render_png = args.png or bool(group_daily_cfg.get("render_png", False))

    export_json = resolve_config_path(config_path, input_value)
    decrypt_repo = resolve_config_path(config_path, decrypt_repo_value)

    print(f"[group_daily] config={config_path}")
    print(f"[group_daily] chat_name={chat_name}")
    print(f"[group_daily] date={target_date}")
    print(f"[group_daily] mode={mode}")
    if speaker:
        print(f"[group_daily] speaker={speaker}")
    if start_time or end_time:
        print(f"[group_daily] time_range={start_time or ''}~{end_time or ''}")
    if render_png:
        print("[group_daily] png=true")

    run_export(decrypt_repo, chat_name, export_json)
    run_summary(
        project_dir,
        config_path,
        chat_name,
        target_date,
        export_json,
        mode,
        speaker,
        render_png,
        start_time,
        end_time,
        output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
