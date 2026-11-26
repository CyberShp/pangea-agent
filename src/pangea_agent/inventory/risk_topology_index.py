"""Fault-tolerant C/C++ and Lua three-track AST topology indexer for risk-method-v2.

Produces deterministic structural facts only (error-rollback slices, concurrency
anchors & shared-access matrix, resource lifecycle anchors). Never classifies
defects or replaces semantic analysis by workers.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from pangea_agent.agent_io import write_json
from pangea_agent.fs_paths import long_path

NOTE = (
    "确定性四轨结构索引（仅结构事实与检索锚点，不构成缺陷结论；"
    "宏展开、函数指针间接调用与条件编译仍须由轨道 Worker 核对源码）。"
)

_ACQUIRE_EXACT = frozenset(
    "malloc calloc realloc strdup strndup fopen fdopen open openat socket accept "
    "dup pipe mmap opendir tmpfile mkstemp pthread_create timer_create eventfd "
    "kmalloc kzalloc vmalloc kmem_cache_alloc".split()
)
_RELEASE_EXACT = frozenset(
    "free fclose close munmap closedir pthread_join timer_delete kfree vfree "
    "kmem_cache_free shmdt".split()
)
_RAW_MEM_WRITE_EXACT = frozenset(
    "memcpy memmove memset bcopy bzero strcpy strncpy strcat strncat "
    "sprintf vsprintf snprintf vsnprintf".split()
)
_SHARED_MEM_OR_SLOT_VERBS = frozenset(
    "shmget shmat shmdt mmap munmap mremap pool slab slot ring".split()
)
_LOCK_EXACT = frozenset(
    "pthread_mutex_lock pthread_mutex_trylock pthread_rwlock_rdlock "
    "pthread_rwlock_wrlock pthread_spin_lock sem_wait mutex_lock spin_lock "
    "spin_lock_irqsave down_read down_write".split()
)
_UNLOCK_EXACT = frozenset(
    "pthread_mutex_unlock pthread_rwlock_unlock pthread_spin_unlock sem_post "
    "mutex_unlock spin_unlock spin_unlock_irqrestore up_read up_write".split()
)
_BLOCKING_EXACT = frozenset(
    "read write recv recvfrom recvmsg send sendto sendmsg connect accept poll "
    "select epoll_wait sleep usleep nanosleep pthread_cond_wait "
    "pthread_cond_timedwait msleep wait_event".split()
)
_ACQUIRE_VERBS = frozenset("alloc create open new get acquire ref hold register attach map pin init start subscribe enqueue reserve".split())
_RELEASE_VERBS = frozenset("free destroy close delete put release unref drop unregister detach unmap unpin deinit uninit fini stop unsubscribe dequeue".split())
_CALLBACK_VERBS = frozenset("register subscribe set_cb set_callback on_event timer add_timer schedule_work queue_work".split())
_LUA_YIELD_RE = re.compile(r"\b(?:coroutine\.yield|skynet\.(?:call|sleep|wait|timeout|yield))\b")
_LUA_RESOURCE_RE = re.compile(r"\b(?:io\.open|socket\.\w+|skynet\.timeout|skynet\.fork)\b|:close\s*\(")
_LUA_ERROR_RE = re.compile(r"\b(?:error|assert|pcall|xpcall)\s*\(")
_LUA_MEM_RE = re.compile(r"\b(?:ffi\.(?:copy|fill|cast|new|gc)|string\.unpack)\b")
_REFCOUNT_RE = re.compile(
    r"\b(ref|refs|refcnt|refcount|cnt|count|users)\s*(?:\+\+|--|\+=|-=)|"
    r"(?:\+\+|--)\s*(ref|refs|refcnt|refcount|cnt|count|users)\b"
)
_IDENT_WORDS_RE = re.compile(r"([a-z0-9])([A-Z])")
_FIELD_RE = re.compile(r"(?:->|\.)\s*([A-Za-z_]\w*)")


def _words(token: str) -> set[str]:
    return set(_IDENT_WORDS_RE.sub(r"\1_\2", token).lower().split("_"))


def _anchor_id(prefix: str, repo_id: str, path: str, line: int, tag: str) -> str:
    digest = hashlib.sha1(f"{repo_id}:{path}:{line}:{tag}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _node_text(node, data: bytes) -> str:
    return data[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _callee_name(call_node, data: bytes) -> str:
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return ""
    raw = _node_text(fn, data).strip()
    return raw.split("->")[-1].split(".")[-1].split("::")[-1].strip()


def _first_call_arg_text(call_node, data: bytes) -> str:
    args = call_node.child_by_field_name("arguments")
    if args is None or not args.named_children:
        return ""
    raw = _node_text(args.named_children[0], data).strip()
    return re.sub(r"^\s*&\s*", "", raw)


def _c_function_name(fn_node, data: bytes) -> str:
    decl = fn_node.child_by_field_name("declarator")
    if decl is None:
        return "<unknown>"
    text = _node_text(decl, data)
    match = re.search(r"\b([A-Za-z_]\w*)\s*\(", text)
    return match.group(1) if match else "<unknown>"


def _classify_c_call(callee: str) -> list[str]:
    tags: list[str] = []
    words = _words(callee)
    if callee in _ACQUIRE_EXACT or (words & _ACQUIRE_VERBS and callee not in _RELEASE_EXACT):
        tags.append("acquire")
    if callee in _RELEASE_EXACT or (words & _RELEASE_VERBS and callee not in _ACQUIRE_EXACT):
        tags.append("release")
    if callee in _LOCK_EXACT or ("lock" in words and "unlock" not in words):
        tags.append("lock")
    if callee in _UNLOCK_EXACT or "unlock" in words:
        tags.append("unlock")
    if callee in _BLOCKING_EXACT:
        tags.append("blocking")
    if words & _CALLBACK_VERBS:
        tags.append("callback_reg")
    if callee in _RAW_MEM_WRITE_EXACT:
        tags.append("raw_mem_write")
    if callee in _SHARED_MEM_OR_SLOT_VERBS or (words & _SHARED_MEM_OR_SLOT_VERBS):
        tags.append("shared_mem_or_slot")
    return tags


def _scan_c_cpp(repo_id: str, path: str, text: str) -> dict[str, Any]:
    """Fault-tolerant tree-sitter C/C++ scanner: never discards valid functions on local parse errors."""
    from pangea_agent.inventory.cpp_symbols import _load_parser

    data = text.encode("utf-8")
    lines = text.splitlines()
    error_slices: list[dict[str, Any]] = []
    concurrency_anchors: list[dict[str, Any]] = []
    resource_anchors: list[dict[str, Any]] = []
    memory_anchors: list[dict[str, Any]] = []
    field_reads: dict[str, set[str]] = defaultdict(set)
    field_writes: dict[str, set[str]] = defaultdict(set)
    released_fields: dict[str, set[str]] = defaultdict(set)
    global_vars: set[str] = set()
    parser_mode = "tree-sitter-tolerant"

    try:
        parser, _ = _load_parser(Path(path))
        root = parser.parse(data).root_node
        if root.has_error:
            parser_mode = "tree-sitter-partial"
    except Exception:
        return _scan_c_cpp_fallback(repo_id, path, lines)

    # First pass: top-level non-extern declarations (file-scope shared state candidates).
    for child in root.named_children:
        if child.type == "declaration":
            decl_text = _node_text(child, data)
            if not re.search(r"\bextern\b", decl_text) and not any(
                c.type == "function_declarator" for c in child.named_children
            ):
                for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?:=|;|\[)", decl_text):
                    name = m.group(1)
                    if name not in {"static", "const", "volatile", "struct", "enum", "union", "typedef"}:
                        global_vars.add(name)
                        concurrency_anchors.append({
                            "anchor_id": _anchor_id("conc", repo_id, path, child.start_point.row + 1, f"global:{name}"),
                            "kind": "shared_decl",
                            "line": child.start_point.row + 1,
                            "function": None,
                            "symbol": name,
                            "snippet": lines[child.start_point.row][:160] if child.start_point.row < len(lines) else "",
                        })

    def collect_functions(node) -> list:
        found = []
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.type == "function_definition":
                found.append(cur)
                continue
            for c in reversed(cur.named_children):
                if c.type != "ERROR":
                    stack.append(c)
        return found

    for fn_node in collect_functions(root):
        fn_name = _c_function_name(fn_node, data)
        body = fn_node.child_by_field_name("body")
        if body is None:
            continue

        # Collect goto label cleanup summaries in this function.
        label_cleanups: dict[str, list[str]] = defaultdict(list)
        side_effects: list[dict[str, Any]] = []
        exits: list[dict[str, Any]] = []
        fn_releases: list[tuple[int, str, str, str]] = []
        fn_nullifies: list[tuple[int, str]] = []
        fn_derefs: list[tuple[int, str, str]] = []
        lock_depth = 0
        held_locks: list[str] = []

        def walk_fn(node, current_label: str | None = None) -> None:
            nonlocal lock_depth
            if node.type == "ERROR":
                return
            line_no = node.start_point.row + 1
            snippet = lines[line_no - 1].strip()[:160] if 1 <= line_no <= len(lines) else ""

            if node.type == "labeled_statement":
                lbl = node.child_by_field_name("label")
                current_label = _node_text(lbl, data).strip() if lbl else current_label

            if node.type == "call_expression":
                callee = _callee_name(node, data)
                tags = _classify_c_call(callee)
                first_arg = _first_call_arg_text(node, data)
                if current_label and ("release" in tags or "unlock" in tags):
                    label_cleanups[current_label].append(callee)
                if "acquire" in tags or "release" in tags:
                    kind = "acquire" if "acquire" in tags else "release"
                    resource_anchors.append({
                        "anchor_id": _anchor_id("res", repo_id, path, line_no, f"{kind}:{callee}"),
                        "kind": kind,
                        "line": line_no,
                        "function": fn_name,
                        "symbol": callee,
                        "snippet": snippet,
                    })
                if "release" in tags:
                    fn_releases.append((line_no, callee, first_arg, snippet))
                    for m in _FIELD_RE.finditer(first_arg):
                        released_fields[m.group(1)].add(fn_name)
                elif first_arg:
                    fn_derefs.append((line_no, callee, first_arg))
                if "raw_mem_write" in tags:
                    memory_anchors.append({
                        "anchor_id": _anchor_id("mem", repo_id, path, line_no, f"raw:{callee}"),
                        "kind": "raw_memory_op",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": callee,
                        "snippet": snippet,
                    })
                if "shared_mem_or_slot" in tags:
                    memory_anchors.append({
                        "anchor_id": _anchor_id("mem", repo_id, path, line_no, f"slot:{callee}"),
                        "kind": "shared_mem_or_slot_op",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": callee,
                        "snippet": snippet,
                    })
                if "acquire" in tags or "callback_reg" in tags:
                    side_effects.append({"line": line_no, "kind": tags[0], "symbol": callee})
                if "lock" in tags:
                    lock_depth += 1
                    held_locks.append(callee)
                    side_effects.append({"line": line_no, "kind": "lock", "symbol": callee})
                    concurrency_anchors.append({
                        "anchor_id": _anchor_id("conc", repo_id, path, line_no, f"lock:{callee}"),
                        "kind": "lock_acquire",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": callee,
                        "snippet": snippet,
                    })
                elif "unlock" in tags:
                    lock_depth = max(0, lock_depth - 1)
                    if held_locks:
                        held_locks.pop()
                    concurrency_anchors.append({
                        "anchor_id": _anchor_id("conc", repo_id, path, line_no, f"unlock:{callee}"),
                        "kind": "lock_release",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": callee,
                        "snippet": snippet,
                    })
                elif lock_depth > 0 and ("blocking" in tags or "callback_reg" in tags):
                    concurrency_anchors.append({
                        "anchor_id": _anchor_id("conc", repo_id, path, line_no, f"held_call:{callee}"),
                        "kind": "lock_held_call",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": callee,
                        "held_locks": list(held_locks),
                        "snippet": snippet,
                    })
                if "callback_reg" in tags:
                    concurrency_anchors.append({
                        "anchor_id": _anchor_id("conc", repo_id, path, line_no, f"cb:{callee}"),
                        "kind": "async_registration",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": callee,
                        "snippet": snippet,
                    })

            elif node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left is not None:
                    left_text = _node_text(left, data)
                    right_text = _node_text(right, data).strip() if right is not None else ""
                    if right_text in {"NULL", "0", "nullptr", "(void *)0", "(void*)0"}:
                        fn_nullifies.append((line_no, left_text.strip()))
                    else:
                        fn_derefs.append((line_no, "assign", left_text.strip()))
                    for m in _FIELD_RE.finditer(left_text):
                        field_writes[m.group(1)].add(fn_name)
                        side_effects.append({"line": line_no, "kind": "state_write", "symbol": m.group(1)})
                    ident = left_text.strip()
                    if ident in global_vars:
                        field_writes[ident].add(fn_name)
                        side_effects.append({"line": line_no, "kind": "global_write", "symbol": ident})

            elif node.type == "field_expression":
                expr_full = _node_text(node, data).strip()
                fn_derefs.append((line_no, "field", expr_full))
                field_node = node.child_by_field_name("field")
                if field_node is not None:
                    fname = _node_text(field_node, data).strip()
                    if fname:
                        field_reads[fname].add(fn_name)

            elif node.type in {"update_expression", "assignment_expression"}:
                pass

            elif node.type in {"return_statement", "goto_statement"}:
                target_label = None
                if node.type == "goto_statement":
                    m = re.search(r"\bgoto\s+([A-Za-z_]\w*)", _node_text(node, data))
                    target_label = m.group(1) if m else None
                exits.append({
                    "line": line_no,
                    "kind": "goto" if node.type == "goto_statement" else "return",
                    "target_label": target_label,
                    "snippet": snippet,
                })

            if node.type == "update_expression":
                expr_text = _node_text(node, data)
                m = _REFCOUNT_RE.search(expr_text)
                if m:
                    sym = m.group(1) or m.group(2)
                    resource_anchors.append({
                        "anchor_id": _anchor_id("res", repo_id, path, line_no, f"ref:{sym}"),
                        "kind": "refcount_op",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": sym,
                        "snippet": snippet,
                    })

            for c in node.named_children:
                walk_fn(c, current_label)

        walk_fn(body)

        for rel_line, rel_callee, rel_arg, rel_snip in fn_releases:
            if not rel_arg:
                continue
            nullified_after = any(nl > rel_line and lhs == rel_arg for nl, lhs in fn_nullifies)
            later_uses = [
                dl for dl, _dk, d_expr in fn_derefs
                if dl > rel_line and (d_expr == rel_arg or d_expr.startswith(f"{rel_arg}->") or d_expr.startswith(f"{rel_arg}."))
                and not any(rel_line < nl < dl and lhs == rel_arg for nl, lhs in fn_nullifies)
            ]
            if later_uses:
                memory_anchors.append({
                    "anchor_id": _anchor_id("mem", repo_id, path, rel_line, f"uaf_fn:{rel_arg}"),
                    "kind": "post_release_use_in_fn",
                    "line": rel_line,
                    "function": fn_name,
                    "symbol": rel_arg,
                    "post_release_lines": later_uses[:4],
                    "snippet": rel_snip,
                })
            elif ("->" in rel_arg or "." in rel_arg) and not nullified_after:
                memory_anchors.append({
                    "anchor_id": _anchor_id("mem", repo_id, path, rel_line, f"unnull:{rel_arg}"),
                    "kind": "unnullified_member_after_release",
                    "line": rel_line,
                    "function": fn_name,
                    "symbol": rel_arg,
                    "callee": rel_callee,
                    "snippet": rel_snip,
                })

        # Keep only exits that occur AFTER at least one side effect (eliminates pure guard early-returns).
        if side_effects:
            first_effect_line = min(se["line"] for se in side_effects)
            for ex in exits:
                if ex["line"] <= first_effect_line:
                    continue
                prior_effects = [se["symbol"] for se in side_effects if se["line"] < ex["line"]][:6]
                if not prior_effects:
                    continue
                error_slices.append({
                    "anchor_id": _anchor_id("err", repo_id, path, ex["line"], f"{fn_name}:{ex['kind']}"),
                    "function": fn_name,
                    "line": ex["line"],
                    "exit_kind": ex["kind"],
                    "target_label": ex["target_label"],
                    "label_cleanups": label_cleanups.get(ex["target_label"], []) if ex["target_label"] else [],
                    "prior_side_effects": prior_effects,
                    "snippet": ex["snippet"],
                })

    for sym, rel_fns in sorted(released_fields.items()):
        other_fns = sorted((field_reads.get(sym, set()) | field_writes.get(sym, set())) - rel_fns)
        if other_fns:
            memory_anchors.append({
                "anchor_id": _anchor_id("mem", repo_id, path, 1, f"xfn_uaf:{sym}"),
                "kind": "cross_fn_dangling_candidate",
                "line": 1,
                "function": ",".join(sorted(rel_fns)[:4]),
                "symbol": sym,
                "release_functions": sorted(rel_fns)[:6],
                "access_functions": other_fns[:8],
                "snippet": f"成员 {sym} 在 {sorted(rel_fns)[:4]} 中被释放，同时在 {other_fns[:6]} 中被读写（核查跨线程/进程/回调释放后踩内存）",
            })

    shared_access_matrix = [
        {
            "symbol": sym,
            "writers": sorted(writers)[:8],
            "readers": sorted(field_reads.get(sym, set()))[:8],
        }
        for sym, writers in sorted(field_writes.items())
        if len(writers | field_reads.get(sym, set())) >= 2
    ][:40]

    return {
        "repo_id": repo_id,
        "path": path,
        "language": "c_cpp",
        "parser": parser_mode,
        "error_slices": error_slices[:160],
        "concurrency_anchors": concurrency_anchors[:160],
        "shared_access_matrix": shared_access_matrix,
        "resource_anchors": resource_anchors[:160],
        "memory_anchors": memory_anchors[:160],
    }


def _scan_c_cpp_fallback(repo_id: str, path: str, lines: list[str]) -> dict[str, Any]:
    error_slices, concurrency_anchors, resource_anchors, memory_anchors = [], [], [], []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()[:160]
        if _REFCOUNT_RE.search(line):
            resource_anchors.append({
                "anchor_id": _anchor_id("res", repo_id, path, i, "refcount"),
                "kind": "refcount_op", "line": i, "function": None, "symbol": "refcount", "snippet": stripped,
            })
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", line):
            tags = _classify_c_call(m.group(1))
            if "acquire" in tags or "release" in tags:
                resource_anchors.append({
                    "anchor_id": _anchor_id("res", repo_id, path, i, m.group(1)),
                    "kind": "acquire" if "acquire" in tags else "release",
                    "line": i, "function": None, "symbol": m.group(1), "snippet": stripped,
                })
            if "lock" in tags or "unlock" in tags:
                concurrency_anchors.append({
                    "anchor_id": _anchor_id("conc", repo_id, path, i, m.group(1)),
                    "kind": "lock_acquire" if "lock" in tags else "lock_release",
                    "line": i, "function": None, "symbol": m.group(1), "snippet": stripped,
                })
            if "raw_mem_write" in tags or "shared_mem_or_slot" in tags:
                memory_anchors.append({
                    "anchor_id": _anchor_id("mem", repo_id, path, i, m.group(1)),
                    "kind": "raw_memory_op" if "raw_mem_write" in tags else "shared_mem_or_slot_op",
                    "line": i, "function": None, "symbol": m.group(1), "snippet": stripped,
                })
    return {
        "repo_id": repo_id, "path": path, "language": "c_cpp", "parser": "regex_fallback",
        "error_slices": error_slices[:160], "concurrency_anchors": concurrency_anchors[:160],
        "shared_access_matrix": [], "resource_anchors": resource_anchors[:160],
        "memory_anchors": memory_anchors[:160],
    }


def _scan_lua(repo_id: str, path: str, text: str) -> dict[str, Any]:
    """Fault-tolerant tree-sitter-lua scanner focusing on yield spans, pcall/error rollback, resource handles, and stale/FFI memory ops."""
    from pangea_agent.inventory.lua_symbols import _load_parser

    data = text.encode("utf-8")
    lines = text.splitlines()
    error_slices: list[dict[str, Any]] = []
    concurrency_anchors: list[dict[str, Any]] = []
    resource_anchors: list[dict[str, Any]] = []
    memory_anchors: list[dict[str, Any]] = []
    field_reads: dict[str, set[str]] = defaultdict(set)
    field_writes: dict[str, set[str]] = defaultdict(set)
    parser_mode = "tree-sitter-lua"

    try:
        parser, _ = _load_parser()
        root = parser.parse(data).root_node
        if root.has_error:
            parser_mode = "tree-sitter-lua-partial"
    except Exception:
        parser_mode = "regex_fallback"
        root = None

    if root is not None:
        stack = [(root, "<chunk>")]
        fn_yields: dict[str, list[int]] = defaultdict(list)
        fn_state_lines: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        while stack:
            node, fn_name = stack.pop()
            if node.type == "ERROR":
                continue
            line_no = node.start_point.row + 1
            snippet = lines[line_no - 1].strip()[:160] if 1 <= line_no <= len(lines) else ""
            if node.type in {"function_declaration", "function_definition"}:
                name_node = node.child_by_field_name("name")
                fn_name = _node_text(name_node, data).strip() if name_node else f"anon@{line_no}"
            elif node.type == "function_call":
                call_text = _node_text(node, data)
                if _LUA_YIELD_RE.search(call_text):
                    fn_yields[fn_name].append(line_no)
                    concurrency_anchors.append({
                        "anchor_id": _anchor_id("conc", repo_id, path, line_no, f"yield:{fn_name}"),
                        "kind": "lua_yield_point",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": _LUA_YIELD_RE.search(call_text).group(0),
                        "snippet": snippet,
                    })
                if _LUA_RESOURCE_RE.search(call_text):
                    is_close = ":close" in call_text
                    resource_anchors.append({
                        "anchor_id": _anchor_id("res", repo_id, path, line_no, f"lua_res:{is_close}"),
                        "kind": "release" if is_close else "acquire",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": _LUA_RESOURCE_RE.search(call_text).group(0),
                        "snippet": snippet,
                    })
                if _LUA_ERROR_RE.search(call_text):
                    error_slices.append({
                        "anchor_id": _anchor_id("err", repo_id, path, line_no, f"lua_err:{fn_name}"),
                        "function": fn_name,
                        "line": line_no,
                        "exit_kind": "lua_error_or_pcall",
                        "target_label": None,
                        "label_cleanups": [],
                        "prior_side_effects": [],
                        "snippet": snippet,
                    })
                if _LUA_MEM_RE.search(call_text):
                    memory_anchors.append({
                        "anchor_id": _anchor_id("mem", repo_id, path, line_no, f"lua_mem:{fn_name}"),
                        "kind": "lua_ffi_or_unpack_op",
                        "line": line_no,
                        "function": fn_name,
                        "symbol": _LUA_MEM_RE.search(call_text).group(0),
                        "snippet": snippet,
                    })
            elif node.type == "assignment_statement":
                stmt = _node_text(node, data)
                parts = stmt.split("=", 1)
                lhs = parts[0]
                rhs = parts[1].strip() if len(parts) > 1 else ""
                for m in re.finditer(r"\bself\.([A-Za-z_]\w*)", lhs):
                    sym = f"self.{m.group(1)}"
                    field_writes[sym].add(fn_name)
                    fn_state_lines[fn_name].append((line_no, "nil_clear" if rhs == "nil" else "write", sym))
            elif node.type == "dot_index_expression":
                expr = _node_text(node, data)
                if expr.startswith("self."):
                    field_reads[expr].add(fn_name)
                    fn_state_lines[fn_name].append((line_no, "read", expr))
            for c in reversed(node.named_children):
                stack.append((c, fn_name))

        # Detect state fields accessed both before and after a yield point inside the same Lua function.
        for fn_name, yield_lines in fn_yields.items():
            if not yield_lines:
                continue
            first_y, last_y = min(yield_lines), max(yield_lines)
            before = {sym for ln, _, sym in fn_state_lines[fn_name] if ln < first_y}
            after = {sym for ln, _, sym in fn_state_lines[fn_name] if ln > last_y}
            for sym in sorted(before & after):
                concurrency_anchors.append({
                    "anchor_id": _anchor_id("conc", repo_id, path, first_y, f"cross_yield:{fn_name}:{sym}"),
                    "kind": "lua_cross_yield_state",
                    "line": first_y,
                    "function": fn_name,
                    "symbol": sym,
                    "snippet": f"{fn_name} 在 yield (L{first_y}) 前后均访问状态 {sym}",
                })

        cleared_fields: dict[str, set[str]] = defaultdict(set)
        for fn_name, ops in fn_state_lines.items():
            for _ln, op, sym in ops:
                if op == "nil_clear":
                    cleared_fields[sym].add(fn_name)
        for sym, clear_fns in sorted(cleared_fields.items()):
            other_fns = sorted((field_reads.get(sym, set()) | field_writes.get(sym, set())) - clear_fns)
            if other_fns:
                memory_anchors.append({
                    "anchor_id": _anchor_id("mem", repo_id, path, 1, f"lua_stale:{sym}"),
                    "kind": "lua_cross_handler_cleared_state",
                    "line": 1,
                    "function": ",".join(sorted(clear_fns)[:4]),
                    "symbol": sym,
                    "snippet": f"{sym} 在 {sorted(clear_fns)[:4]} 中置 nil 释放，同时在 {other_fns[:6]} 中被跨协程/回调访问",
                })

    shared_access_matrix = [
        {
            "symbol": sym,
            "writers": sorted(writers)[:8],
            "readers": sorted(field_reads.get(sym, set()))[:8],
        }
        for sym, writers in sorted(field_writes.items())
        if len(writers | field_reads.get(sym, set())) >= 2
    ][:40]

    return {
        "repo_id": repo_id,
        "path": path,
        "language": "lua",
        "parser": parser_mode,
        "error_slices": error_slices[:160],
        "concurrency_anchors": concurrency_anchors[:160],
        "shared_access_matrix": shared_access_matrix,
        "resource_anchors": resource_anchors[:160],
        "memory_anchors": memory_anchors[:160],
    }


def scan_file_topology(repo_id: str, path: str, text: str, language: str) -> dict[str, Any]:
    if language == "c_cpp":
        return _scan_c_cpp(repo_id, path, text)
    if language == "lua":
        return _scan_lua(repo_id, path, text)
    return {
        "repo_id": repo_id,
        "path": path,
        "language": language,
        "parser": "raw_source_required",
        "error_slices": [],
        "concurrency_anchors": [],
        "shared_access_matrix": [],
        "resource_anchors": [],
        "memory_anchors": [],
    }


def generate_topology_summary(run_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    """Generate frozen global four-track topology index for Stage 1 Blueprint Agent."""
    from pangea_agent.evidence_quotes import FrozenSourceResolver
    from pangea_agent.inventory.languages import file_language

    resolver = FrozenSourceResolver(run_dir.parents[1], run_dir.name, task)
    files, skipped, used_bytes = [], [], 0
    for item in sorted(task.get("owned_scope_paths", []), key=lambda x: (x["repo_id"], x["path"])):
        repo, path = item["repo_id"], item["path"]
        source = resolver.repositories[repo][0] / path
        size = long_path(source).stat().st_size
        if used_bytes + size > 40 * 1024 * 1024:
            skipped.append({**item, "reason": "exceeded_40mb_index_budget"})
            continue
        used_bytes += size
        detected = file_language(source)
        if detected not in {"c_cpp", "lua"}:
            skipped.append({**item, "reason": f"language={detected}: raw source reading required"})
            continue
        text = "\n".join(resolver.lines(repo, path))
        scanned = scan_file_topology(repo, path, text, detected)
        files.append({
            "repo_id": repo,
            "path": path,
            "language": detected,
            "parser": scanned["parser"],
            "counts": {
                "error_rollback": len(scanned["error_slices"]),
                "concurrency_timing": len(scanned["concurrency_anchors"]),
                "resource_lifecycle": len(scanned["resource_anchors"]),
                "memory_corruption": len(scanned["memory_anchors"]),
                "shared_symbols": len(scanned["shared_access_matrix"]),
            },
            "shared_access_matrix": scanned["shared_access_matrix"][:12],
        })

    payload = {
        "format_version": "risk-topology-summary-v1",
        "files": files,
        "not_indexed": skipped,
        "note": NOTE,
    }
    write_json(run_dir / "inputs" / "risk-topology-summary.json", payload)
    return payload


def generate_track_topology(run_dir: Path, task: dict[str, Any], track: str, key: str) -> Path:
    """Generate per-track detailed AST anchors for a Stage 2 track worker."""
    from pangea_agent.evidence_quotes import FrozenSourceResolver
    from pangea_agent.inventory.languages import file_language

    resolver = FrozenSourceResolver(run_dir.parents[1], run_dir.name, task)
    paths = sorted({
        (r["repo_id"], r["path"])
        for group in (task.get("owned_regions", []), task.get("context_regions", []), task.get("context_files", []))
        for r in group
        if isinstance(r, dict) and r.get("repo_id") and r.get("path")
    })
    files = []
    for repo, path in paths:
        source = resolver.repositories[repo][0] / path
        detected = file_language(source)
        if detected not in {"c_cpp", "lua"}:
            files.append({"repo_id": repo, "path": path, "language": detected, "parser": "raw_source_required"})
            continue
        scanned = scan_file_topology(repo, path, "\n".join(resolver.lines(repo, path)), detected)
        entry: dict[str, Any] = {
            "repo_id": repo,
            "path": path,
            "language": detected,
            "parser": scanned["parser"],
        }
        if track == "error_rollback":
            entry["error_slices"] = scanned["error_slices"]
        elif track == "concurrency_timing":
            entry["concurrency_anchors"] = scanned["concurrency_anchors"]
            entry["shared_access_matrix"] = scanned["shared_access_matrix"]
        elif track == "resource_lifecycle":
            entry["resource_anchors"] = scanned["resource_anchors"]
        elif track == "memory_corruption":
            entry["memory_anchors"] = scanned["memory_anchors"]
            entry["shared_access_matrix"] = scanned["shared_access_matrix"]
        else:
            entry.update({
                "error_slices": scanned["error_slices"],
                "concurrency_anchors": scanned["concurrency_anchors"],
                "shared_access_matrix": scanned["shared_access_matrix"],
                "resource_anchors": scanned["resource_anchors"],
                "memory_anchors": scanned["memory_anchors"],
            })
        files.append(entry)

    target = run_dir / "inputs" / "risk-topology" / f"{key}.json"
    write_json(target, {
        "format_version": "risk-track-topology-v1",
        "track": track,
        "unit_id": task.get("unit_id"),
        "files": files,
        "note": NOTE,
    })
    return target
