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
    "dup dup2 pipe mmap opendir tmpfile mkstemp pthread_create timer_create eventfd "
    "kmalloc kzalloc vmalloc kmem_cache_alloc asprintf vasprintf realpath getline getdelim "
    "aligned_alloc posix_memalign memalign valloc pvalloc shm_open "
    "cJSON_Parse cJSON_ParseWithLength cJSON_ParseWithOpts cJSON_Print "
    "cJSON_PrintUnformatted cJSON_PrintBuffered cJSON_Duplicate "
    "cJSON_CreateObject cJSON_CreateArray cJSON_CreateString cJSON_CreateRaw".split()
)
_RELEASE_EXACT = frozenset(
    "free fclose close munmap closedir pthread_join timer_delete kfree vfree "
    "kmem_cache_free shmdt cJSON_Delete".split()
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
_HEAP_ALLOC_VERBS = frozenset(
    "alloc malloc calloc zalloc zmalloc realloc strdup strndup memdup asprintf vasprintf clone dup".split()
)
_BUFFER_RESOURCE_NOUNS = frozenset(
    "buf buffer mem memory page pages blk block slot pkt skb mbuf sgl iobuf".split()
)
_GETTER_META_WORDS = frozenset(
    "id idx index size len length cnt count num max min cap info stat stats status state flag flags type name addr offset ver version".split()
)
_ACQUIRE_VERBS = _HEAP_ALLOC_VERBS | frozenset(
    "create open new acquire ref hold register attach map pin subscribe enqueue reserve".split()
)
_RELEASE_VERBS = frozenset(
    "free zfree kfree vfree destroy close delete put release unref drop unregister detach unmap unpin deinit uninit fini stop unsubscribe dequeue".split()
)
_CALLBACK_VERBS = frozenset("register subscribe set_cb set_callback on_event timer add_timer schedule_work queue_work".split())
_EARLY_EXIT_MACRO_RE = re.compile(r"^[A-Z][A-Z0-9_]*(?:RET|RETURN|GOTO|CHECK|ENSURE)[A-Z0-9_]*$")
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
_C_IGNORE_PERSISTENT = frozenset(
    "NULL nullptr true false TRUE FALSE errno this stdin stdout stderr".split()
)
_SCALAR_TYPE_RE = re.compile(
    r"\b(?:int|long|short|bool|_Bool|ssize_t|size_t|errno_t|u?int(?:8|16|32|64)_t|"
    r"[us](?:8|16|32|64)|ULONG|LONG|UINT|INT|DWORD|HRESULT)\b"
)
_STATUS_VAR_NAMES = frozenset("ret rc err rv res result status errcode retval ret_val".split())
_POST_ALLOC_EXIT_LIMIT = 64


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


def _call_addr_targets(call_node, data: bytes) -> list[str]:
    """Extract `&target` out-parameter expressions (unwrapping C-style casts/parens)."""
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return []
    targets: list[str] = []
    for arg in args.named_children:
        raw = _node_text(arg, data).strip()
        unwrapped = re.sub(r"^(?:\([^()]*\)\s*)+", "", raw).strip()
        if unwrapped.startswith("&") and not unwrapped.startswith("&&"):
            target = unwrapped[1:].strip().strip("()").strip()
            if target and target not in targets:
                targets.append(target)
    return targets


def _extract_decl_ident(decl_node, data: bytes) -> str:
    cur = decl_node
    for _ in range(12):
        if cur is None:
            return ""
        if cur.type in {"identifier", "field_identifier"}:
            return _node_text(cur, data).strip()
        nxt = cur.child_by_field_name("declarator")
        if nxt is not None:
            cur = nxt
            continue
        named = [
            c for c in cur.named_children
            if c.type not in {"parameter_list", "type_qualifier", "attribute_specifier", "ms_declspec_modifier"}
        ]
        cur = named[0] if named else None
    return ""


def _strip_casts_and_parens(expr: str) -> str:
    cleaned = expr.strip()
    for _ in range(6):
        nxt = re.sub(r"^\([^()]*\)\s*", "", cleaned).strip()
        if nxt.startswith("(") and nxt.endswith(")"):
            inner = nxt[1:-1].strip()
            if inner.count("(") == inner.count(")"):
                nxt = inner
        if nxt == cleaned:
            break
        cleaned = nxt
    return cleaned


def _root_ident(expr: str) -> str:
    cleaned = _strip_casts_and_parens(expr)
    cleaned = re.sub(r"^[!*&\s]+", "", cleaned)
    m = re.match(r"^([A-Za-z_]\w*)", cleaned)
    return m.group(1) if m else ""


def _cond_mentions_target(cond_texts: list[str] | tuple[str, ...], target_expr: str) -> bool:
    target = target_expr.strip()
    if not target or not cond_texts:
        return False
    root = _root_ident(target)
    leaf = target.split("->")[-1].split(".")[-1].split("[")[0].strip()
    for cond in cond_texts:
        if target in cond:
            return True
        if leaf and re.search(rf"\b{re.escape(leaf)}\b", cond) and (
            not root or root == leaf or re.search(rf"\b{re.escape(root)}\b", cond)
        ):
            return True
    return False


def _c_function_name(fn_node, data: bytes) -> str:
    decl = fn_node.child_by_field_name("declarator")
    if decl is None:
        return "<unknown>"
    text = _node_text(decl, data)
    match = re.search(r"\b([A-Za-z_]\w*)\s*\(", text)
    return match.group(1) if match else "<unknown>"


def _enclosing_assign_info(node, data: bytes) -> tuple[str, bool]:
    """Return `(lhs_target, is_non_pointer_init)` for an expression."""
    cur = node.parent
    depth = 0
    while cur is not None and depth < 4:
        if cur.type == "assignment_expression":
            left = cur.child_by_field_name("left")
            return (_node_text(left, data).strip() if left is not None else ""), False
        if cur.type == "init_declarator":
            decl = cur.child_by_field_name("declarator")
            if decl is not None:
                raw = _node_text(decl, data).strip()
                ident = _extract_decl_ident(decl, data)
                if not ident:
                    m = re.search(r"([A-Za-z_]\w*)\s*$", raw)
                    ident = m.group(1) if m else raw.lstrip("*& \t")
                has_ptr = "*" in raw or "[" in raw
                parent_decl = cur.parent
                type_text = ""
                if parent_decl is not None and parent_decl.type == "declaration":
                    t_node = parent_decl.child_by_field_name("type")
                    type_text = _node_text(t_node, data) if t_node is not None else ""
                is_scalar_init = (not has_ptr) and bool(_SCALAR_TYPE_RE.search(type_text) or ident in _STATUS_VAR_NAMES)
                return ident, is_scalar_init
            return "", False
        if cur.type in {"cast_expression", "parenthesized_expression"}:
            cur = cur.parent
            depth += 1
            continue
        break
    return "", False


def _enclosing_assign_target(node, data: bytes) -> str:
    target, _ = _enclosing_assign_info(node, data)
    return target


_INIT_PATH_RE = re.compile(
    r"(?:^|/)(?:init|boot|startup|mod_init)(?:/|_|\.)|_(?:init|boot)\.[ch](?:pp|xx|c)?$",
    re.IGNORECASE,
)


def is_init_like_path(path: str) -> bool:
    """Deterministic path-shape hint for ordering steady-state runtime files ahead of bootstrap files."""
    return bool(_INIT_PATH_RE.search(path.replace("\\", "/")))


def _is_null_alloc_guard(
    cond_texts: list[str] | tuple[str, ...],
    snippet: str,
    exit_kind: str,
    target_expr: str,
    status_lhs: str | None,
    status_reassigned: bool,
    alloc_callee: str | None = None,
) -> bool:
    texts = list(cond_texts)
    if exit_kind == "macro_exit_candidate" and snippet:
        texts.append(snippet)
    if target_expr:
        esc = re.escape(target_expr.strip())
        for cond in texts:
            if (
                re.search(rf"!\s*\(*\s*{esc}\b", cond)
                or re.search(rf"\b{esc}\s*==\s*(?:NULL|nullptr|0)\b", cond)
                or re.search(rf"\b(?:NULL|nullptr|0)\s*==\s*{esc}\b", cond)
            ):
                return True
    if alloc_callee:
        c_esc = re.escape(alloc_callee.strip())
        for cond in texts:
            if re.search(rf"\b{c_esc}\s*\(", cond) and not re.search(
                r"==\s*(?:0|VOS_OK|RET_OK|OK|SUCCESS|TRUE|true)\b", cond
            ):
                return True
    if status_lhs and not status_reassigned:
        s_esc = re.escape(status_lhs.strip())
        for cond in texts:
            if re.search(rf"\b{s_esc}\b", cond):
                return True
    return False


def _classify_c_call(callee: str, extra_heap_callees: frozenset[str] | set[str] = frozenset()) -> list[str]:
    tags: list[str] = []
    words = _words(callee)
    is_release = callee in _RELEASE_EXACT or (bool(words & _RELEASE_VERBS) and callee not in _ACQUIRE_EXACT)
    is_buf_acquire = (
        bool(words & {"get", "acquire", "create", "reserve", "borrow"})
        and bool(words & _BUFFER_RESOURCE_NOUNS)
        and not bool(words & _GETTER_META_WORDS)
        and not is_release
    )
    is_heap_alloc = (
        callee in _ACQUIRE_EXACT
        or callee in extra_heap_callees
        or bool(words & _HEAP_ALLOC_VERBS)
        or is_buf_acquire
    ) and not is_release
    if is_heap_alloc:
        tags.append("heap_alloc")
        tags.append("acquire")
    elif not is_release and bool(words & _ACQUIRE_VERBS):
        tags.append("acquire")
    if is_release:
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
    if _EARLY_EXIT_MACRO_RE.match(callee):
        tags.append("macro_exit_candidate")
    return tags


def _scan_c_cpp_ast(
    repo_id: str,
    path: str,
    data: bytes,
    lines: list[str],
    root: Any,
    parser_mode: str,
    extra_heap_callees: set[str],
) -> dict[str, Any]:
    error_slices: list[dict[str, Any]] = []
    concurrency_anchors: list[dict[str, Any]] = []
    resource_anchors: list[dict[str, Any]] = []
    alloc_flow_slices: list[dict[str, Any]] = []
    memory_anchors: list[dict[str, Any]] = []
    field_reads: dict[str, set[str]] = defaultdict(set)
    field_writes: dict[str, set[str]] = defaultdict(set)
    persistent_reads: dict[str, set[str]] = defaultdict(set)
    persistent_writes: dict[str, set[str]] = defaultdict(set)
    released_fields: dict[str, set[str]] = defaultdict(set)
    global_vars: set[str] = set()

    # First pass: top-level declarations (file-scope and extern shared state candidates).
    for child in root.named_children:
        if child.type == "declaration":
            decl_text = _node_text(child, data)
            if not any(c.type == "function_declarator" for c in child.named_children):
                is_extern = bool(re.search(r"\bextern\b", decl_text))
                for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?:=|;|\[)", decl_text):
                    name = m.group(1)
                    if name not in {"static", "const", "volatile", "struct", "enum", "union", "typedef", "extern"}:
                        global_vars.add(name)
                        if not is_extern:
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
        fn_start_line = fn_node.start_point.row + 1
        fn_end_line = fn_node.end_point.row + 1

        # Pre-pass: distinguish parameters & automatic locals from function-static / global / extern symbols.
        param_vars: set[str] = set()
        fn_local_vars: set[str] = set()
        fn_static_vars: set[str] = set()
        scalar_locals: set[str] = set()

        fn_decl = fn_node.child_by_field_name("declarator")
        if fn_decl is not None:
            p_stack = [fn_decl]
            while p_stack:
                p_cur = p_stack.pop()
                if p_cur.type == "parameter_declaration":
                    p_ident = _extract_decl_ident(p_cur.child_by_field_name("declarator"), data)
                    if p_ident:
                        param_vars.add(p_ident)
                    continue
                for c in p_cur.named_children:
                    if c.type != "ERROR":
                        p_stack.append(c)

        d_stack = [body]
        while d_stack:
            d_cur = d_stack.pop()
            if d_cur.type == "declaration":
                storage_tokens = {
                    _node_text(c, data).strip()
                    for c in d_cur.children
                    if c.type == "storage_class_specifier"
                }
                t_node = d_cur.child_by_field_name("type")
                type_text = _node_text(t_node, data) if t_node is not None else ""
                is_static_or_extern = bool(storage_tokens & {"static", "extern"})
                for sub in d_cur.named_children:
                    if sub.type in {"type_qualifier", "storage_class_specifier", "primitive_type", "sized_type_specifier", "type_identifier", "struct_specifier", "enum_specifier", "union_specifier"}:
                        continue
                    decl_part = sub.child_by_field_name("declarator") if sub.type == "init_declarator" else sub
                    if decl_part is None:
                        continue
                    raw_decl = _node_text(decl_part, data)
                    ident = _extract_decl_ident(decl_part, data)
                    if not ident:
                        continue
                    if is_static_or_extern:
                        fn_static_vars.add(ident)
                        if "static" in storage_tokens:
                            d_line = d_cur.start_point.row + 1
                            concurrency_anchors.append({
                                "anchor_id": _anchor_id("conc", repo_id, path, d_line, f"fn_static:{fn_name}:{ident}"),
                                "kind": "shared_decl",
                                "line": d_line,
                                "function": fn_name,
                                "symbol": ident,
                                "snippet": lines[d_line - 1].strip()[:160] if 1 <= d_line <= len(lines) else "",
                            })
                    else:
                        fn_local_vars.add(ident)
                        if "*" not in raw_decl and "[" not in raw_decl and (
                            _SCALAR_TYPE_RE.search(type_text) or ident in _STATUS_VAR_NAMES
                        ):
                            scalar_locals.add(ident)
                continue
            if d_cur.type == "for_range_loop":
                r_ident = _extract_decl_ident(d_cur.child_by_field_name("declarator"), data)
                if r_ident:
                    fn_local_vars.add(r_ident)
            for c in d_cur.named_children:
                if c.type != "ERROR":
                    d_stack.append(c)

        def is_persistent_expr(expr: str) -> bool:
            root_id = _root_ident(expr)
            if not root_id or root_id in _C_IGNORE_PERSISTENT or root_id.isupper():
                return False
            if root_id in param_vars or root_id in fn_local_vars:
                return False
            return True

        label_cleanups: dict[str, list[str]] = defaultdict(list)
        label_lines: dict[str, int] = {}
        side_effects: list[dict[str, Any]] = []
        exits: list[dict[str, Any]] = []
        fn_macro_exits: list[dict[str, Any]] = []
        fn_allocs: list[dict[str, Any]] = []
        fn_assigns: list[tuple[int, str]] = []
        fn_releases: list[tuple[int, str, str, str, int]] = []
        fn_handoffs: list[tuple[int, str, int]] = []
        fn_nullifies: list[tuple[int, str]] = []
        fn_derefs: list[tuple[int, str, str]] = []
        fn_if_conds: list[tuple[int, str]] = []
        local_alloc_origins: dict[str, int] = {}
        local_call_origins: dict[str, tuple[int, str]] = {}
        emitted_global_allocs: set[tuple[int, str]] = set()
        lock_depth = 0
        held_locks: list[str] = []
        next_block_id = 0

        def emit_global_alloc_anchor(
            assign_line: int,
            alloc_line: int,
            symbol: str,
            callee: str,
            null_checked: bool,
            locks_snapshot: list[str],
            snip: str,
        ) -> None:
            key = (assign_line, symbol)
            if key in emitted_global_allocs:
                return
            emitted_global_allocs.add(key)
            concurrency_anchors.append({
                "anchor_id": _anchor_id("conc", repo_id, path, assign_line, f"glazy:{fn_name}:{symbol}:{callee}"),
                "kind": "global_lazy_init_or_alloc",
                "line": assign_line,
                "alloc_line": alloc_line,
                "function": fn_name,
                "symbol": symbol,
                "callee": callee,
                "null_checked": null_checked,
                "held_locks": locks_snapshot,
                "snippet": snip,
            })
            resource_anchors.append({
                "anchor_id": _anchor_id("res", repo_id, path, assign_line, f"galloc:{fn_name}:{symbol}:{callee}"),
                "kind": "global_alloc_assign",
                "line": assign_line,
                "alloc_line": alloc_line,
                "function": fn_name,
                "symbol": symbol,
                "callee": callee,
                "null_checked": null_checked,
                "held_locks": locks_snapshot,
                "snippet": snip,
            })

        def walk_fn(
            node,
            current_label: str | None = None,
            active_conds: tuple[str, ...] = (),
            block_ancestors: tuple[int, ...] = (),
        ) -> None:
            nonlocal lock_depth, next_block_id
            if node.type == "ERROR":
                return
            line_no = node.start_point.row + 1
            snippet = lines[line_no - 1].strip()[:160] if 1 <= line_no <= len(lines) else ""

            if node.type == "compound_statement":
                next_block_id += 1
                block_ancestors = (*block_ancestors, next_block_id)

            cur_block = block_ancestors[-1] if block_ancestors else 0

            if node.type == "labeled_statement":
                lbl = node.child_by_field_name("label")
                if lbl is not None:
                    lbl_text = _node_text(lbl, data).strip()
                    if lbl_text:
                        current_label = lbl_text
                        label_lines.setdefault(lbl_text, line_no)

            if node.type == "if_statement":
                cond_node = node.child_by_field_name("condition")
                cond_text = _node_text(cond_node, data).strip() if cond_node is not None else ""
                if cond_text:
                    fn_if_conds.append((line_no, cond_text))
                if cond_node is not None:
                    walk_fn(cond_node, current_label, active_conds, block_ancestors)
                consequence = node.child_by_field_name("consequence")
                if consequence is not None:
                    next_conds = (*active_conds, cond_text) if cond_text else active_conds
                    walk_fn(consequence, current_label, next_conds, block_ancestors)
                alternative = node.child_by_field_name("alternative")
                if alternative is not None:
                    walk_fn(alternative, current_label, active_conds, block_ancestors)
                return

            if node.type == "new_expression":
                target_expr = _enclosing_assign_target(node, data)
                resource_anchors.append({
                    "anchor_id": _anchor_id("res", repo_id, path, line_no, "heap_alloc:new"),
                    "kind": "acquire",
                    "line": line_no,
                    "function": fn_name,
                    "symbol": "new",
                    "snippet": snippet,
                })
                is_pers = is_persistent_expr(target_expr)
                prior_conds = [ct for cl, ct in fn_if_conds if cl <= line_no]
                null_chk = _cond_mentions_target((*active_conds, *prior_conds), target_expr) if is_pers else False
                alloc_idx = len(fn_allocs)
                fn_allocs.append({
                    "alloc_line": line_no,
                    "callee": "new",
                    "target_expr": target_expr,
                    "status_lhs": None,
                    "snippet": snippet,
                    "ownership_out": target_expr.startswith("*"),
                    "global_target": target_expr if is_pers else None,
                    "global_assign_line": line_no if is_pers else None,
                    "null_checked": null_chk if is_pers else None,
                    "held_locks": list(held_locks) if is_pers else None,
                })
                if target_expr and not is_pers:
                    local_alloc_origins[target_expr] = alloc_idx
                elif is_pers:
                    persistent_writes[_root_ident(target_expr)].add(fn_name)
                    emit_global_alloc_anchor(line_no, line_no, target_expr, "new", null_chk, list(held_locks), snippet)
                side_effects.append({"line": line_no, "kind": "acquire", "symbol": "new"})

            elif node.type == "delete_expression":
                expr_text = _node_text(node, data).strip()
                del_arg = re.sub(r"^\s*delete(?:\s*\[\s*\])?\s+", "", expr_text).strip()
                if current_label:
                    label_cleanups[current_label].append("delete")
                resource_anchors.append({
                    "anchor_id": _anchor_id("res", repo_id, path, line_no, "release:delete"),
                    "kind": "release",
                    "line": line_no,
                    "function": fn_name,
                    "symbol": "delete",
                    "snippet": snippet,
                })
                fn_releases.append((line_no, "delete", del_arg, snippet, cur_block))
                for m in _FIELD_RE.finditer(del_arg):
                    released_fields[m.group(1)].add(fn_name)

            elif node.type == "call_expression":
                callee = _callee_name(node, data)
                tags = _classify_c_call(callee, extra_heap_callees)
                first_arg = _first_call_arg_text(node, data)
                lhs_target, lhs_is_scalar_init = _enclosing_assign_info(node, data)
                addr_targets = _call_addr_targets(node, data)
                if lhs_target and lhs_target in fn_local_vars and lhs_target not in scalar_locals and not lhs_is_scalar_init:
                    local_call_origins[lhs_target] = (line_no, callee)
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
                if "heap_alloc" in tags:
                    target_expr = lhs_target
                    status_lhs = None
                    if addr_targets and (
                        not target_expr
                        or lhs_is_scalar_init
                        or target_expr in scalar_locals
                        or target_expr in _STATUS_VAR_NAMES
                    ):
                        status_lhs = lhs_target or None
                        target_expr = addr_targets[0]
                    is_pers = is_persistent_expr(target_expr)
                    prior_conds = [ct for cl, ct in fn_if_conds if cl <= line_no]
                    null_chk = _cond_mentions_target((*active_conds, *prior_conds), target_expr) if is_pers else False
                    alloc_idx = len(fn_allocs)
                    fn_allocs.append({
                        "alloc_line": line_no,
                        "callee": callee,
                        "target_expr": target_expr,
                        "status_lhs": status_lhs,
                        "snippet": snippet,
                        "ownership_out": target_expr.startswith("*"),
                        "global_target": target_expr if is_pers else None,
                        "global_assign_line": line_no if is_pers else None,
                        "null_checked": null_chk if is_pers else None,
                        "held_locks": list(held_locks) if is_pers else None,
                    })
                    if target_expr and not is_pers:
                        local_alloc_origins[target_expr] = alloc_idx
                    elif is_pers:
                        persistent_writes[_root_ident(target_expr)].add(fn_name)
                        emit_global_alloc_anchor(line_no, line_no, target_expr, callee, null_chk, list(held_locks), snippet)
                elif "acquire" in tags and addr_targets:
                    for out_t in addr_targets:
                        if is_persistent_expr(out_t):
                            prior_conds = [ct for cl, ct in fn_if_conds if cl <= line_no]
                            null_chk = _cond_mentions_target((*active_conds, *prior_conds), out_t)
                            persistent_writes[_root_ident(out_t)].add(fn_name)
                            emit_global_alloc_anchor(line_no, line_no, out_t, callee, null_chk, list(held_locks), snippet)
                if "macro_exit_candidate" in tags:
                    fn_macro_exits.append({
                        "line": line_no,
                        "kind": "macro_exit_candidate",
                        "target_label": None,
                        "return_expr": "",
                        "snippet": snippet,
                        "block_ancestors": block_ancestors,
                        "active_conds": active_conds,
                    })
                if "release" in tags:
                    fn_releases.append((line_no, callee, first_arg, snippet, cur_block))
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
                    side_effects.append({"line": line_no, "kind": "acquire" if "acquire" in tags else tags[0], "symbol": callee})
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
                    left_clean = left_text.strip()
                    right_text = _node_text(right, data).strip() if right is not None else ""
                    rhs_stripped = _strip_casts_and_parens(right_text) if right_text else ""
                    if right_text in {"NULL", "0", "nullptr", "(void *)0", "(void*)0"}:
                        fn_nullifies.append((line_no, left_clean))
                    else:
                        fn_derefs.append((line_no, "assign", left_clean))
                        fn_assigns.append((line_no, left_clean))
                        if rhs_stripped in local_alloc_origins:
                            if left_clean.startswith("*"):
                                fn_allocs[local_alloc_origins[rhs_stripped]]["ownership_out"] = True
                                fn_handoffs.append((line_no, rhs_stripped, cur_block))
                            elif "->" in left_clean or "." in left_clean or is_persistent_expr(left_clean):
                                fn_handoffs.append((line_no, rhs_stripped, cur_block))
                    for m in _FIELD_RE.finditer(left_text):
                        field_writes[m.group(1)].add(fn_name)
                        side_effects.append({"line": line_no, "kind": "state_write", "symbol": m.group(1)})
                    if is_persistent_expr(left_clean):
                        root_sym = _root_ident(left_clean)
                        persistent_writes[root_sym].add(fn_name)
                        side_effects.append({"line": line_no, "kind": "global_write", "symbol": left_clean})
                        if right_text not in {"NULL", "0", "nullptr", "(void *)0", "(void*)0"}:
                            prior_conds = [ct for cl, ct in fn_if_conds if cl <= line_no]
                            null_chk = _cond_mentions_target((*active_conds, *prior_conds), left_clean)
                            if rhs_stripped in local_alloc_origins:
                                origin = fn_allocs[local_alloc_origins[rhs_stripped]]
                                origin["global_target"] = left_clean
                                origin["global_assign_line"] = line_no
                                origin["null_checked"] = null_chk
                                origin["held_locks"] = list(held_locks)
                                emit_global_alloc_anchor(
                                    line_no,
                                    origin["alloc_line"],
                                    left_clean,
                                    origin["callee"],
                                    null_chk,
                                    list(held_locks),
                                    snippet,
                                )
                            elif _cond_mentions_target(active_conds, left_clean):
                                custom_callee = None
                                custom_alloc_line = line_no
                                if rhs_stripped in local_call_origins:
                                    custom_alloc_line, custom_callee = local_call_origins[rhs_stripped]
                                elif right is not None:
                                    r_stack = [right]
                                    while r_stack:
                                        rc = r_stack.pop()
                                        if rc.type == "call_expression":
                                            custom_callee = _callee_name(rc, data)
                                            break
                                        r_stack.extend(reversed(rc.named_children))
                                if custom_callee and "release" not in _classify_c_call(custom_callee, extra_heap_callees):
                                    fn_allocs.append({
                                        "alloc_line": custom_alloc_line,
                                        "callee": custom_callee,
                                        "target_expr": left_clean,
                                        "status_lhs": None,
                                        "snippet": snippet,
                                        "ownership_out": False,
                                        "global_target": left_clean,
                                        "global_assign_line": line_no,
                                        "null_checked": True,
                                        "held_locks": list(held_locks),
                                    })
                                    emit_global_alloc_anchor(
                                        line_no,
                                        custom_alloc_line,
                                        left_clean,
                                        custom_callee,
                                        True,
                                        list(held_locks),
                                        snippet,
                                    )

            elif node.type == "field_expression":
                expr_full = _node_text(node, data).strip()
                fn_derefs.append((line_no, "field", expr_full))
                field_node = node.child_by_field_name("field")
                if field_node is not None:
                    fname = _node_text(field_node, data).strip()
                    if fname:
                        field_reads[fname].add(fn_name)
                if is_persistent_expr(expr_full):
                    parent = node.parent
                    if not (parent is not None and parent.type == "assignment_expression" and parent.child_by_field_name("left") == node):
                        persistent_reads[_root_ident(expr_full)].add(fn_name)

            elif node.type == "identifier":
                ident_name = _node_text(node, data).strip()
                if ident_name in global_vars or ident_name in fn_static_vars:
                    parent = node.parent
                    is_callee = parent is not None and parent.type == "call_expression" and parent.child_by_field_name("function") == node
                    is_lhs = parent is not None and parent.type == "assignment_expression" and parent.child_by_field_name("left") == node
                    is_decl = parent is not None and parent.type in {"init_declarator", "pointer_declarator", "array_declarator", "declaration"}
                    if not is_callee and not is_lhs and not is_decl:
                        persistent_reads[ident_name].add(fn_name)

            elif node.type in {"return_statement", "goto_statement"}:
                target_label = None
                return_expr = ""
                if node.type == "goto_statement":
                    m = re.search(r"\bgoto\s+([A-Za-z_]\w*)", _node_text(node, data))
                    target_label = m.group(1) if m else None
                else:
                    ret_raw = _node_text(node, data).strip()
                    m = re.match(r"return\s+(.+?)\s*;?$", ret_raw, re.DOTALL)
                    return_expr = m.group(1).strip() if m else ""
                exits.append({
                    "line": line_no,
                    "kind": "goto" if node.type == "goto_statement" else "return",
                    "target_label": target_label,
                    "return_expr": return_expr,
                    "snippet": snippet,
                    "block_ancestors": block_ancestors,
                    "active_conds": active_conds,
                })

            if node.type == "update_expression":
                expr_text = _node_text(node, data)
                if is_persistent_expr(expr_text):
                    persistent_writes[_root_ident(expr_text)].add(fn_name)
                    side_effects.append({"line": line_no, "kind": "global_write", "symbol": _root_ident(expr_text)})
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
                walk_fn(c, current_label, active_conds, block_ancestors)

        walk_fn(body)

        for alloc_info in fn_allocs:
            alloc_line = alloc_info["alloc_line"]
            alloc_callee = alloc_info["callee"]
            target_expr = alloc_info["target_expr"]
            status_lhs = alloc_info.get("status_lhs")
            alloc_snip = alloc_info["snippet"]
            global_target = alloc_info.get("global_target")
            matching_releases = [
                (rl, rb)
                for rl, _rc, r_arg, _rs, rb in fn_releases
                if rl > alloc_line and (
                    not target_expr
                    or r_arg == target_expr
                    or r_arg.startswith(f"{target_expr}->")
                    or r_arg.startswith(f"{target_expr}.")
                    or (global_target and (r_arg == global_target or r_arg.startswith(f"{global_target}->")))
                )
            ]
            same_fn_releases = [rl for rl, _rb in matching_releases]
            matching_handoffs = [
                (hl, hb)
                for hl, h_sym, hb in fn_handoffs
                if hl > alloc_line and target_expr and h_sym == target_expr
            ]
            sorted_exits = [
                ex for ex in sorted([*exits, *fn_macro_exits], key=lambda item: item["line"])
                if ex["line"] > alloc_line
            ]
            all_post_exits = [
                {
                    "line": ex["line"],
                    "kind": ex["kind"],
                    "target_label": ex["target_label"],
                    "label_cleanups": label_cleanups.get(ex["target_label"], []) if ex["target_label"] else [],
                    "snippet": ex["snippet"],
                }
                for ex in sorted_exits
            ]
            uncleaned_exit_lines: list[int] = []
            if target_expr and not is_persistent_expr(target_expr) and not target_expr.startswith("*"):
                for ex in sorted_exits:
                    ex_line = ex["line"]
                    ex_ret = _strip_casts_and_parens(ex.get("return_expr") or "")
                    if ex_ret == target_expr:
                        continue
                    status_reassigned = bool(
                        status_lhs and any(alloc_line < al < ex_line and lhs == status_lhs for al, lhs in fn_assigns)
                    )
                    if _is_null_alloc_guard(
                        ex.get("active_conds", ()),
                        ex["snippet"],
                        ex["kind"],
                        target_expr,
                        status_lhs,
                        status_reassigned,
                        alloc_callee,
                    ):
                        continue
                    t_lbl = ex.get("target_label")
                    if t_lbl and (
                        label_cleanups.get(t_lbl)
                        or (t_lbl in label_lines and any(rl >= label_lines[t_lbl] for rl in same_fn_releases))
                    ):
                        continue
                    anc_set = set(ex.get("block_ancestors") or ())
                    if any(rl < ex_line and rb in anc_set for rl, rb in matching_releases):
                        continue
                    if any(hl < ex_line and hb in anc_set for hl, hb in matching_handoffs):
                        continue
                    uncleaned_exit_lines.append(ex_line)

            post_exits = all_post_exits[:_POST_ALLOC_EXIT_LIMIT]
            overwrite_lines = [
                al for al, lhs in fn_assigns
                if al > alloc_line and target_expr and lhs == target_expr
            ][:8]
            ownership_out = bool(
                alloc_info.get("ownership_out")
                or target_expr.startswith("*")
                or (
                    target_expr
                    and any(
                        _strip_casts_and_parens(ex.get("return_expr") or "") == target_expr
                        for ex in exits
                        if ex["line"] > alloc_line
                    )
                )
            )
            slice_item: dict[str, Any] = {
                "anchor_id": _anchor_id("alloc", repo_id, path, alloc_line, f"{fn_name}:{alloc_callee}:{target_expr}"),
                "function": fn_name,
                "fn_start_line": fn_start_line,
                "fn_end_line": fn_end_line,
                "alloc_line": alloc_line,
                "callee": alloc_callee,
                "target": target_expr or None,
                "same_fn_release_lines": same_fn_releases[:12],
                "uncleaned_exit_lines": uncleaned_exit_lines[:24],
                "post_alloc_exits": post_exits,
                "overwrite_lines": overwrite_lines,
                "ownership_out_candidate": ownership_out,
                "snippet": alloc_snip,
            }
            if len(all_post_exits) > _POST_ALLOC_EXIT_LIMIT:
                slice_item["post_alloc_exits_total"] = len(all_post_exits)
                slice_item["post_alloc_exits_truncated"] = True
            if global_target:
                slice_item["global_or_static_target"] = global_target
                slice_item["global_assign_line"] = alloc_info.get("global_assign_line")
                slice_item["null_checked_before_assign"] = bool(alloc_info.get("null_checked"))
                slice_item["held_locks_at_assign"] = list(alloc_info.get("held_locks") or [])
            alloc_flow_slices.append(slice_item)

        for rel_line, rel_callee, rel_arg, rel_snip, _rel_blk in fn_releases:
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

    shared_by_sym: dict[str, dict[str, Any]] = {}
    for sym, writers in sorted(persistent_writes.items()):
        shared_by_sym[sym] = {
            "symbol": sym,
            "scope": "global_or_static",
            "writers": sorted(writers)[:8],
            "readers": sorted(persistent_reads.get(sym, set()))[:8],
        }
    for sym, writers in sorted(field_writes.items()):
        if sym in shared_by_sym:
            shared_by_sym[sym]["writers"] = sorted(set(shared_by_sym[sym]["writers"]) | writers)[:8]
            shared_by_sym[sym]["readers"] = sorted(set(shared_by_sym[sym]["readers"]) | field_reads.get(sym, set()))[:8]
        elif len(writers | field_reads.get(sym, set())) >= 2:
            shared_by_sym[sym] = {
                "symbol": sym,
                "writers": sorted(writers)[:8],
                "readers": sorted(field_reads.get(sym, set()))[:8],
            }
    shared_access_matrix = list(shared_by_sym.values())[:48]

    truncated = {
        "error_slices": len(error_slices) > 280,
        "concurrency_anchors": len(concurrency_anchors) > 200,
        "resource_anchors": len(resource_anchors) > 360,
        "alloc_flow_slices": len(alloc_flow_slices) > 300,
        "memory_anchors": len(memory_anchors) > 200,
    }
    return {
        "repo_id": repo_id,
        "path": path,
        "language": "c_cpp",
        "parser": parser_mode,
        "error_slices": error_slices[:280],
        "concurrency_anchors": concurrency_anchors[:200],
        "shared_access_matrix": shared_access_matrix,
        "resource_anchors": resource_anchors[:360],
        "alloc_flow_slices": alloc_flow_slices[:300],
        "memory_anchors": memory_anchors[:200],
        "truncated": {k: v for k, v in truncated.items() if v},
    }


def _scan_c_cpp(repo_id: str, path: str, text: str) -> dict[str, Any]:
    """Fault-tolerant two-pass tree-sitter C/C++ scanner (propagates intra-file ownership-out wrapper allocators)."""
    from pangea_agent.inventory.cpp_symbols import _load_parser

    data = text.encode("utf-8")
    lines = text.splitlines()
    parser_mode = "tree-sitter-tolerant"

    try:
        parser, _ = _load_parser(Path(path))
        root = parser.parse(data).root_node
        if root.has_error:
            parser_mode = "tree-sitter-partial"
    except Exception:
        return _scan_c_cpp_fallback(repo_id, path, lines)

    first = _scan_c_cpp_ast(repo_id, path, data, lines, root, parser_mode, set())
    local_helpers = {
        str(s["function"])
        for s in first["alloc_flow_slices"]
        if s.get("ownership_out_candidate")
        and s.get("function")
        and s["function"] != "<unknown>"
        and "heap_alloc" not in _classify_c_call(str(s["function"]))
    }
    if not local_helpers:
        return first
    return _scan_c_cpp_ast(repo_id, path, data, lines, root, parser_mode, local_helpers)


def _scan_c_cpp_fallback(repo_id: str, path: str, lines: list[str]) -> dict[str, Any]:
    error_slices, concurrency_anchors, resource_anchors, alloc_flow_slices, memory_anchors = [], [], [], [], []
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
            if "heap_alloc" in tags:
                alloc_flow_slices.append({
                    "anchor_id": _anchor_id("alloc", repo_id, path, i, m.group(1)),
                    "function": None, "alloc_line": i, "callee": m.group(1), "target": None,
                    "same_fn_release_lines": [], "post_alloc_exits": [], "overwrite_lines": [],
                    "ownership_out_candidate": False, "snippet": stripped,
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
        "error_slices": error_slices[:200], "concurrency_anchors": concurrency_anchors[:160],
        "shared_access_matrix": [], "resource_anchors": resource_anchors[:320],
        "alloc_flow_slices": alloc_flow_slices[:240],
        "memory_anchors": memory_anchors[:160],
        "truncated": {},
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
        "alloc_flow_slices": [],
        "memory_anchors": memory_anchors[:160],
        "truncated": {},
    }


def scan_file_topology(repo_id: str, path: str, text: str, language: str) -> dict[str, Any]:
    phase_hint = "init_or_boot" if is_init_like_path(path) else "runtime"
    if language == "c_cpp":
        return {"phase_hint": phase_hint, **_scan_c_cpp(repo_id, path, text)}
    if language == "lua":
        return {"phase_hint": phase_hint, **_scan_lua(repo_id, path, text)}
    return {
        "repo_id": repo_id,
        "path": path,
        "phase_hint": phase_hint,
        "language": language,
        "parser": "raw_source_required",
        "error_slices": [],
        "concurrency_anchors": [],
        "shared_access_matrix": [],
        "resource_anchors": [],
        "alloc_flow_slices": [],
        "memory_anchors": [],
        "truncated": {},
    }


def _compact_alloc_slice(item: dict[str, Any]) -> dict[str, Any]:
    """Preserve all uncleaned post-alloc exits while condensing repetitive cleaned exits in large files."""
    exits = item.get("post_alloc_exits") or []
    if len(exits) <= 10:
        return item
    uncleaned_set = set(item.get("uncleaned_exit_lines") or [])
    must_keep = [ex for ex in exits if ex.get("line") in uncleaned_set]
    others = [ex for ex in exits if ex.get("line") not in uncleaned_set][:6]
    kept = sorted([*must_keep, *others], key=lambda ex: ex.get("line", 0))
    omitted = len(exits) - len(kept)
    if omitted <= 0:
        return item
    return {
        **item,
        "post_alloc_exits": kept,
        "cleaned_exits_omitted": omitted,
    }


def generate_topology_summary(run_dir: Path, task: dict[str, Any]) -> dict[str, Any]:
    """Generate frozen global four-track topology index for Stage 1 Blueprint Agent."""
    from pangea_agent.evidence_quotes import FrozenSourceResolver
    from pangea_agent.inventory.languages import file_language

    resolver = FrozenSourceResolver(run_dir.parents[1], run_dir.name, task)
    files, skipped, used_bytes = [], [], 0
    ordered_scope = sorted(
        task.get("owned_scope_paths", []),
        key=lambda x: (1 if is_init_like_path(str(x.get("path", ""))) else 0, x["repo_id"], x["path"]),
    )
    for item in ordered_scope:
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
        alloc_slices = scanned.get("alloc_flow_slices", [])
        uncleaned_slices = [s for s in alloc_slices if s.get("uncleaned_exit_lines")]
        global_allocs = [
            {
                "function": a.get("function"),
                "line": a.get("line"),
                "alloc_line": a.get("alloc_line"),
                "symbol": a.get("symbol"),
                "callee": a.get("callee"),
                "null_checked": a.get("null_checked"),
                "held_locks": a.get("held_locks", []),
            }
            for a in scanned["concurrency_anchors"]
            if a.get("kind") == "global_lazy_init_or_alloc"
        ]
        file_entry: dict[str, Any] = {
            "repo_id": repo,
            "path": path,
            "phase_hint": "init_or_boot" if is_init_like_path(path) else "runtime",
            "language": detected,
            "parser": scanned["parser"],
            "counts": {
                "error_rollback": len(scanned["error_slices"]),
                "concurrency_timing": len(scanned["concurrency_anchors"]),
                "resource_lifecycle": len(scanned["resource_anchors"]),
                "alloc_flow_slices": len(alloc_slices),
                "uncleaned_exit_slices": len(uncleaned_slices),
                "global_lazy_or_alloc_writes": len(global_allocs),
                "memory_corruption": len(scanned["memory_anchors"]),
                "shared_symbols": len(scanned["shared_access_matrix"]),
            },
            "shared_access_matrix": scanned["shared_access_matrix"][:12],
        }
        if global_allocs:
            file_entry["global_alloc_candidates"] = global_allocs[:12]
        if uncleaned_slices:
            file_entry["uncleaned_exit_candidates"] = [
                f"{s['function']}@L{s['alloc_line']}(exits:{s['uncleaned_exit_lines'][:6]})"
                for s in uncleaned_slices[:12]
            ]
        files.append(file_entry)

    files.sort(
        key=lambda f: (
            1 if f.get("phase_hint") == "init_or_boot" else 0,
            -(f["counts"].get("uncleaned_exit_slices", 0) + f["counts"].get("global_lazy_or_alloc_writes", 0)),
            -sum(f["counts"].values()),
            f["repo_id"],
            f["path"],
        )
    )
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
    owned_paths = sorted(
        {
            (r["repo_id"], r["path"])
            for r in task.get("owned_regions", [])
            if isinstance(r, dict) and r.get("repo_id") and r.get("path")
        },
        key=lambda pair: (1 if is_init_like_path(pair[1]) else 0, pair[0], pair[1]),
    )
    context_only_paths = sorted(
        {
            (r["repo_id"], r["path"])
            for group in (task.get("context_regions", []), task.get("context_files", []))
            for r in group
            if isinstance(r, dict) and r.get("repo_id") and r.get("path")
        } - set(owned_paths)
    )
    paths = owned_paths or context_only_paths
    paired_entries: list[tuple[tuple[int, int, int, str, str], dict[str, Any], dict[str, Any]]] = []
    for repo, path in paths:
        source = resolver.repositories[repo][0] / path
        detected = file_language(source)
        phase_hint = "init_or_boot" if is_init_like_path(path) else "runtime"
        if detected not in {"c_cpp", "lua"}:
            raw_item = {"repo_id": repo, "path": path, "phase_hint": phase_hint, "language": detected, "parser": "raw_source_required"}
            paired_entries.append(((1 if phase_hint == "init_or_boot" else 0, 0, 0, repo, path), dict(raw_item), dict(raw_item)))
            continue
        scanned = scan_file_topology(repo, path, "\n".join(resolver.lines(repo, path)), detected)
        alloc_slices = scanned.get("alloc_flow_slices", [])
        uncleaned_cnt = sum(1 for s in alloc_slices if s.get("uncleaned_exit_lines"))
        uncleaned_fns = [
            f"{s['function']}@L{s['alloc_line']}(exits:{s['uncleaned_exit_lines'][:6]})"
            for s in alloc_slices
            if s.get("uncleaned_exit_lines")
        ][:12]
        global_lazy_cnt = sum(
            1 for a in scanned["concurrency_anchors"] if a.get("kind") == "global_lazy_init_or_alloc"
        )
        dir_item: dict[str, Any] = {
            "path": path,
            "phase_hint": phase_hint,
        }
        if alloc_slices:
            dir_item["alloc_slices"] = len(alloc_slices)
        if global_lazy_cnt:
            dir_item["global_lazy_or_alloc_writes"] = global_lazy_cnt
        if uncleaned_fns:
            dir_item["uncleaned_exit_candidates"] = uncleaned_fns

        entry: dict[str, Any] = {
            "repo_id": repo,
            "path": path,
            "phase_hint": phase_hint,
            "language": detected,
            "parser": scanned["parser"],
        }
        if scanned.get("truncated"):
            entry["truncated"] = scanned["truncated"]
        if track == "error_rollback":
            entry["error_slices"] = scanned["error_slices"]
            track_weight = len(scanned["error_slices"])
        elif track == "concurrency_timing":
            entry["concurrency_anchors"] = scanned["concurrency_anchors"]
            entry["shared_access_matrix"] = scanned["shared_access_matrix"]
            track_weight = len(scanned["concurrency_anchors"]) + global_lazy_cnt * 4
        elif track == "resource_lifecycle":
            alloc_fns = {item.get("function") for item in alloc_slices if item.get("function")}
            uncleaned_lines = {
                ln for item in alloc_slices for ln in (item.get("uncleaned_exit_lines") or [])
            }
            entry["alloc_flow_slices"] = [_compact_alloc_slice(s) for s in alloc_slices]
            prioritized_err = sorted(
                [item for item in scanned["error_slices"] if not alloc_fns or item.get("function") in alloc_fns],
                key=lambda item: (0 if item.get("line") in uncleaned_lines else 1, item.get("line", 0)),
            )
            entry["error_slices"] = prioritized_err[:32]
            alloc_lines = {item.get("alloc_line") for item in alloc_slices}
            entry["resource_anchors"] = [
                a for a in scanned["resource_anchors"]
                if a.get("kind") in {"global_alloc_assign", "refcount_op"}
                or (a.get("kind") == "acquire" and a.get("line") not in alloc_lines)
                or a.get("kind") == "release"
            ][:64]
            track_weight = len(alloc_slices) + len(entry["resource_anchors"])
        elif track == "memory_corruption":
            entry["memory_anchors"] = scanned["memory_anchors"]
            entry["shared_access_matrix"] = scanned["shared_access_matrix"]
            track_weight = len(scanned["memory_anchors"])
        else:
            entry.update({
                "error_slices": scanned["error_slices"],
                "concurrency_anchors": scanned["concurrency_anchors"],
                "shared_access_matrix": scanned["shared_access_matrix"],
                "resource_anchors": scanned["resource_anchors"],
                "alloc_flow_slices": [_compact_alloc_slice(s) for s in alloc_slices],
                "memory_anchors": scanned["memory_anchors"],
            })
            track_weight = len(alloc_slices) + len(scanned["error_slices"])
        sort_key = (
            1 if phase_hint == "init_or_boot" else 0,
            -(uncleaned_cnt + global_lazy_cnt),
            -track_weight,
            repo,
            path,
        )
        paired_entries.append((sort_key, dir_item, entry))

    paired_entries.sort(key=lambda t: t[0])
    file_directory = [t[1] for t in paired_entries]
    files = [t[2] for t in paired_entries]

    target = run_dir / "inputs" / "risk-topology" / f"{key}.json"
    payload: dict[str, Any] = {
        "format_version": "risk-track-topology-v1",
        "track": track,
        "unit_id": task.get("unit_id"),
        "file_directory": file_directory,
        "files": files,
        "note": NOTE,
    }
    if context_only_paths and owned_paths:
        payload["context_reference_paths"] = [f"{r}:{p}" for r, p in context_only_paths[:64]]
    write_json(target, payload)
    return target
