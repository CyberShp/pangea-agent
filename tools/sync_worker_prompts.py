"""同步 analysis-worker 的冻结 flow 规则指针，不复制契约正文。

scene_flow.md 是 v2 flow 契约的源码；运行时只读取当前 task 冻结的版本。

用法：
    python tools/sync_worker_prompts.py          # 更新三个客户端的指针
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
BEGIN = "<!-- FLOW-REFERENCE:BEGIN -->"
END = "<!-- FLOW-REFERENCE:END -->"
CONTRACT_BEGIN = "<!-- FLOW-CONTRACT:BEGIN -->"
CONTRACT_END = "<!-- FLOW-CONTRACT:END -->"
ANCHOR = "## behavior-test-v2 场景任务"
REFERENCE = f"""{BEGIN}
flow 字段与样例读取当前 task.inputs 中 rubric_scene_flow 对应的冻结 scene_flow.md 的 FLOW-CONTRACT 段，并读完分页；不读取工作树最新版代替。behavior-test-v1 或更早任务未提供 scene_flow 时，按该任务已冻结的生成 rubric 与结果合同执行，不追加新场景规则。
{END}"""
RISK_TARGETS = tuple(ROOT / directory / f'{role}-worker.md'
                     for directory in ('.claude/agents', '.opencode/agents', '.agents/pangea')
                     for role in ('analysis', 'review'))
RISK_BEGIN, RISK_END = '<!-- RISK-REFERENCE:BEGIN -->', '<!-- RISK-REFERENCE:END -->'
RISK_REFERENCE = f'''{RISK_BEGIN}
风险场景合同以本 Run 冻结的 scene_risk / scene_risk_review 合同块为准，读取语言风险方法与风险信号；另交 kind=check 检查账本，证据附 evidence_quotes。G1–G4 当前只提示 warning，核对后由原 worker 修正；旧任务继续使用其冻结合同。
{RISK_END}'''


def apply_risk_reference(text: str) -> str:
    if RISK_BEGIN in text:
        start = text.index(RISK_BEGIN)
        stop = text.index(RISK_END, start) + len(RISK_END)
        return text[:start] + RISK_REFERENCE + text[stop:]
    return text.rstrip() + '\n\n' + RISK_REFERENCE + '\n'


def _read(path: Path) -> tuple[str, str]:
    text = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline


def apply_reference(text: str) -> str:
    """将旧内联契约或已有指针更新为冻结输入引用。"""
    if BEGIN in text and CONTRACT_BEGIN in text:
        raise ValueError("目标同时包含冻结指针与内联契约，请删除重复的内联契约")
    for begin, end in ((BEGIN, END), (CONTRACT_BEGIN, CONTRACT_END)):
        start = text.find(begin)
        if start < 0:
            continue
        stop = text.find(end, start)
        if stop < 0:
            raise ValueError(f"目标文件只有起始标记 {begin}，缺少 {end}")
        return text[:start] + REFERENCE + text[stop + len(end):]
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
    return text[:paragraph_end] + "\n\n" + REFERENCE + text[paragraph_end:]


def sync(check: bool) -> list[Path]:
    source, _ = _read(SOURCE)
    if CONTRACT_BEGIN not in source or CONTRACT_END not in source:
        raise ValueError(f"{SOURCE} 中缺少 FLOW-CONTRACT 标记")
    drifted: list[Path] = []
    for target in TARGETS:
        text, newline = _read(target)
        updated = apply_reference(text)
        if updated == text:
            continue
        drifted.append(target)
        if not check:
            target.write_bytes(updated.replace("\n", newline).encode("utf-8"))
    for target in RISK_TARGETS:
        text, newline = _read(target)
        updated = apply_risk_reference(text)
        if updated != text:
            if target not in drifted:
                drifted.append(target)
            if not check:
                target.write_bytes(updated.replace('\n', newline).encode('utf-8'))
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
