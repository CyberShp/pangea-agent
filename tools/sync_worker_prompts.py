"""把 scene_flow.md 中的 flow 契约块同步到各客户端的 analysis-worker 提示词。

唯一来源：src/pangea_agent/rubrics/builtin/scene_flow.md 中
``<!-- FLOW-CONTRACT:BEGIN -->`` 与 ``<!-- FLOW-CONTRACT:END -->`` 之间的内容。

用法：
    python tools/sync_worker_prompts.py          # 写入/更新三个副本
    python tools/sync_worker_prompts.py --check  # 只检查，存在漂移时退出码为 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "pangea_agent" / "rubrics" / "builtin" / "scene_flow.md"
TARGETS = (
    ROOT / ".claude" / "agents" / "analysis-worker.md",
    ROOT / ".opencode" / "agents" / "analysis-worker.md",
    ROOT / ".agents" / "pangea" / "analysis-worker.md",
)
BEGIN = "<!-- FLOW-CONTRACT:BEGIN -->"
END = "<!-- FLOW-CONTRACT:END -->"
ANCHOR = "## behavior-test-v2 场景任务"


def _read(path: Path) -> tuple[str, str]:
    text = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline


def extract_block(text: str) -> str:
    """返回包含首尾标记在内的契约块（LF 换行）。"""
    start = text.find(BEGIN)
    end = text.find(END)
    if start < 0 or end < start:
        raise ValueError(f"{SOURCE} 中缺少 {BEGIN} / {END} 标记")
    return text[start:end + len(END)]


def apply_block(text: str, block: str) -> str:
    """替换已有契约块；没有时插入到 behavior-test-v2 段落之后。"""
    start = text.find(BEGIN)
    if start >= 0:
        end = text.find(END, start)
        if end < 0:
            raise ValueError("目标文件只有起始标记，缺少结束标记")
        return text[:start] + block + text[end + len(END):]
    anchor = text.find(ANCHOR)
    if anchor < 0:
        raise ValueError(f"目标文件缺少 {ANCHOR} 段落")
    # 段落 = 标题行、空行、正文行；在正文行之后插入。
    heading_end = text.index("\n", anchor)
    paragraph_start = heading_end + 1
    while text.startswith("\n", paragraph_start):
        paragraph_start += 1
    paragraph_end = text.find("\n", paragraph_start)
    if paragraph_end < 0:
        paragraph_end = len(text)
    return text[:paragraph_end] + "\n\n" + block + text[paragraph_end:]


def sync(check: bool) -> list[Path]:
    block = extract_block(_read(SOURCE)[0])
    drifted: list[Path] = []
    for target in TARGETS:
        text, newline = _read(target)
        updated = apply_block(text, block)
        if updated == text:
            continue
        drifted.append(target)
        if not check:
            target.write_bytes(updated.replace("\n", newline).encode("utf-8"))
    return drifted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只检查，不写入")
    args = parser.parse_args(argv)
    drifted = sync(args.check)
    for path in drifted:
        action = "未同步" if args.check else "已更新"
        print(f"{action}：{path.relative_to(ROOT).as_posix()}")
    return 1 if args.check and drifted else 0


if __name__ == "__main__":
    sys.exit(main())
