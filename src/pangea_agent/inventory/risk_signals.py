"""Bounded source clues. No defect classification and no eager structural index."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import re
from pathlib import Path

from pangea_agent.agent_io import write_json

NOTE = '信号只是检索线索，不代表缺陷，也不保证完整（宏、内联、函数指针、条件编译可能遗漏）。'
EXACT = {
    'acquire': 'malloc calloc realloc strdup strndup fopen fdopen open openat socket accept dup pipe mmap opendir tmpfile mkstemp pthread_create timer_create eventfd',
    'release': 'free fclose close munmap closedir pthread_join timer_delete',
    'cancel': 'pthread_setcancelstate pthread_setcanceltype pthread_cleanup_push pthread_cleanup_pop pthread_testcancel sigprocmask pthread_sigmask',
    'blocking': 'read write recv recvfrom recvmsg send sendto sendmsg connect accept poll select epoll_wait sleep usleep nanosleep pthread_cond_wait pthread_cond_timedwait fgets fread getc fgetc',
    'buffer_op': 'memcpy memmove memset strcpy strncpy strcat strncat sprintf snprintf vsprintf vsnprintf gets fgets sscanf strtok stpcpy',
    'input': 'read recv recvfrom recvmsg fread fgets getline getc fgetc scanf fscanf getenv',
    'exit': 'abort exit _exit assert longjmp siglongjmp',
}
EXACT = {key: set(value.split()) for key, value in EXACT.items()}
VERBS = {
    'acquire': set('alloc create open new get acquire ref hold register attach map pin init start subscribe enqueue reserve'.split()),
    'release': set('free destroy close delete put release unref drop unregister detach unmap unpin deinit uninit fini stop unsubscribe dequeue'.split()),
    'lock': {'lock', 'unlock', 'trylock'},
    'input': {'parse', 'decode', 'unpack', 'expand', 'deserialize'},
}
CALL = re.compile(r'\b([A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)?)\s*\(')
FIELD = re.compile(r'(?:->|\.)\s*([A-Za-z_]\w*)')


def words(token):
    return set(re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', token).lower().split('_'))


def _ast(path, text):
    from .cpp_symbols import _load_parser
    parser, _ = _load_parser(Path(path))
    data = text.encode('utf-8')
    root = parser.parse(data).root_node
    if root.has_error:
        raise ValueError('tree-sitter source contains parse errors')
    functions, calls, arrays, exits, subscripts, shared = [], [], [], [], [], []
    def literal(node):
        return data[node.start_byte:node.end_byte].decode('utf-8', errors='replace')
    def visit(node, function=None, file_scope=True):
        if node.type == 'function_definition':
            declaration = node.child_by_field_name('declarator')
            match = CALL.search(literal(declaration)) if declaration else None
            function = match[1] if match else '<unknown>'
            functions.append((node.start_point.row+1, node.end_point.row+1, function))
            file_scope = False
        if node.type == 'call_expression':
            callee = node.child_by_field_name('function')
            if callee:
                calls.append((node.start_point.row+1, literal(callee).split('->')[-1].split('.')[-1], function, literal(node)))
        if node.type == 'array_declarator':
            arrays.append((node.start_point.row+1, literal(node), function))
        if node.type in ('return_statement', 'goto_statement'):
            exits.append((node.start_point.row+1, node.type.split('_')[0], function))
        if node.type == 'assignment_expression':
            left = node.child_by_field_name('left')
            if left and left.type == 'subscript_expression':
                subscripts.append((node.start_point.row+1, literal(left), function))
        if node.type == 'declaration' and (file_scope or re.search(r'\b(static|volatile|_Atomic|_Thread_local)\b', literal(node))) and not re.search(r'\bextern\b', literal(node)):
            # Function prototypes have a function_declarator and no initializer.
            if not any(c.type == 'function_declarator' for c in node.named_children):
                shared.append((node.start_point.row+1, literal(node), function))
        for child in node.named_children:
            visit(child, function, file_scope)
    visit(root)
    return functions, calls, arrays, exits, subscripts, shared


def scan_text(repo_id, path, text, *, language='c_cpp', detailed=False, defined_functions=()):
    lines, signals, seen = text.splitlines(), [], set()
    functions, calls, arrays, exits, subscripts, shared = [], [], [], [], [], []
    mode = 'regex'
    if language != 'lua' and detailed:
        try:
            functions, calls, arrays, exits, subscripts, shared = _ast(path, text)
            mode = 'tree-sitter'
        except (ImportError, RuntimeError, ValueError, OSError):
            mode = 'regex_fallback'
    if not functions:
        starts = [(i, m[1]) for i, line in enumerate(lines, 1)
                  if (m := re.search(r'\b([A-Za-z_]\w*)\s*\([^;]*\)\s*\{', line)) and m[1] not in {'if', 'for', 'while', 'switch'}]
        functions = [(start, starts[j+1][0]-1 if j+1 < len(starts) else len(lines), name) for j, (start, name) in enumerate(starts)]
    def owner(line):
        return next((name for start, end, name in functions if start <= line <= end), None)
    def add(category, line, token, match='exact', function=None, **extra):
        identity = f'{repo_id}:{path}:{line}:{category}:{token}'
        sid = 'sig-' + hashlib.sha1(identity.encode('utf-8')).hexdigest()[:12]
        if sid not in seen:
            seen.add(sid)
            signals.append(dict(signal_id=sid, category=category, line=line, function=function or owner(line),
                                token=token, match=match, text=lines[line-1][:160], **extra))
    if language == 'lua':
        rules = {
            'error': r'\b(?:error|assert|pcall|xpcall)\s*\(',
            'coroutine': r'\b(?:coroutine\.\w+|skynet\.(?:fork|wait|wakeup|sleep|yield|call|timeout))',
            'resource': r'\b(?:io\.open|socket\.\w+|skynet\.timeout)|:close\s*\(',
            'metatable': r'\b(?:setmetatable|__gc|__close)\b',
            'boundary': r'\b(?:string\.(?:sub|byte)|tonumber|math\.tointeger)\b|#|//',
            'input': r'\b(?:json\.decode|cjson\.decode|string\.unpack)\b',
        }
        depth = 0
        for i, line in enumerate(lines, 1):
            line = line.split('--', 1)[0]
            for category, pattern in rules.items():
                for match in re.finditer(pattern, line):
                    add(category, i, match[0])
            if depth == 0 and re.match(r'^\s*(?!local\b)[\w.]+\s*=(?!=)', line):
                add('global_write', i, line.split('=')[0].strip())
            depth += len(re.findall(r'\b(function|then|do|repeat)\b', line)) - len(re.findall(r'\b(end|until)\b', line))
            depth = max(0, depth)
    else:
        if mode != 'tree-sitter':
            calls = [(i, m[1], owner(i), m[0]) for i, line in enumerate(lines, 1) for m in CALL.finditer(line)]
        defined = {f[2] for f in functions} | set(defined_functions)
        for start, _, name in functions:
            if words(name) & VERBS['input']:
                add('input', start, name, 'verb', name)
        for line, token, function, expression in calls:
            for category, names in EXACT.items():
                if token in names:
                    add(category, line, token, function=function)
            if token.startswith(('pthread_mutex_', 'pthread_rwlock_', 'pthread_spin_')) or token in {'sem_wait', 'sem_post'}:
                add('lock', line, token, function=function)
            for category, verbs in VERBS.items():
                if token not in EXACT.get(category, set()) and words(token) & verbs:
                    add(category, line, token, 'verb', function)
            for name in sorted(defined):
                if re.search(r'(?<=[,(])\s*' + re.escape(name) + r'\s*(?=[,)])', expression):
                    add('callback', line, name, function=function)
        for i, line in enumerate(lines, 1):
            if mode != 'tree-sitter':
                for match in re.finditer(r'\b\w+\s+(\w+\s*\[[^]\n]+\])', line):
                    arrays.append((i, match[1], owner(i)))
                for match in re.finditer(r'\b(return|goto)\b', line):
                    exits.append((i, match[1], owner(i)))
                if re.search(r'\w+\[[^]]+\]\s*[-+*/]?=(?!=)', line):
                    subscripts.append((i, line.strip(), owner(i)))
                if re.search(r'\b(static|volatile|_Atomic|_Thread_local)\b', line) or owner(i) is None and re.search(r'^\s*\w+[\w *]*\s+\w+\s*(?:=|;|\[)', line) and not re.search(r'\bextern\b', line):
                    shared.append((i, line.strip(), owner(i)))
            for match in re.finditer(r'\b(ref|refs|refcnt|refcount|cnt|count|users)\s*(?:\+\+|--|\+=|-=|=(?!=))|(?:\+\+|--)\s*(ref|refs|refcnt|refcount|cnt|count|users)\b', line):
                add('refcount', i, match[1] or match[2])
        for category, items in (('array_decl', arrays), ('exit', exits), ('subscript_write', subscripts), ('shared_state', shared)):
            for line, token, function in items:
                add(category, line, token, function=function)
    signals.sort(key=lambda s: (s['line'], s['category'], s['token']))
    for signal in signals:
        if signal['category'] == 'exit':
            signal['after_acquire'] = bool(signal['function']) and any(s['function'] == signal['function'] and s['line'] <= signal['line'] and s['category'] in {'acquire', 'lock', 'cancel'} for s in signals)
    return signals, mode


def generate_summary(run_dir, task, language):
    from pangea_agent.evidence_quotes import FrozenSourceResolver
    resolver = FrozenSourceResolver(run_dir.parents[1], run_dir.name, task)
    files, skipped, used = [], [], 0
    for item in sorted(task['owned_scope_paths'], key=lambda x: (x['repo_id'], x['path'])):
        repo, path = item['repo_id'], item['path']
        source = resolver.repositories[repo][0] / path
        size = source.stat().st_size
        if used + size > 40*1024*1024:
            skipped.append(item)
            continue
        used += size
        lines = resolver.lines(repo, path)
        signals, _ = scan_text(repo, path, '\n'.join(lines), language=language)
        counts = Counter(s['category'] for s in signals)
        tokens = {category: sorted({s['token'] for s in signals if s['category'] == category})[:12] for category in sorted(counts)}
        fields = sorted({m[1] for s in signals if s['category'] in {'acquire', 'release', 'lock', 'refcount'} for m in FIELD.finditer(lines[s['line']-1])})[:20]
        files.append({**item, 'counts': dict(sorted(counts.items())), 'tokens': tokens, 'fields': fields})
    payload = dict(format_version='risk-signal-summary-v1', files=files, not_scanned=skipped, note=NOTE)
    write_json(run_dir / 'inputs/risk-signal-summary.json', payload)
    return payload


def generate_unit(run_dir, task, key):
    from pangea_agent.evidence_quotes import FrozenSourceResolver
    resolver = FrozenSourceResolver(run_dir.parents[1], run_dir.name, task)
    selections = defaultdict(list)
    for role, group in (('owned', task.get('owned_regions', [])), ('context', task.get('context_regions', [])), ('context', task.get('context_files', []))):
        for r in group:
            selections[(r['repo_id'], r['path'])].append((role, r.get('line_start', 1), r.get('line_end')))
    files, truncated, total = [], [], 0
    texts = {(repo, path): '\n'.join(resolver.lines(repo, path)) for repo, path in sorted(selections)}
    defined_functions = {m[1] for text in texts.values() for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{', text) if m[1] not in {'if', 'for', 'while', 'switch'}}
    for (repo, path), ranges in sorted(selections.items()):
        signals, parser = scan_text(repo, path, texts[(repo, path)], language=task.get('analysis_language', 'c_cpp'), detailed=True, defined_functions=defined_functions)
        kept = []
        for s in signals:
            roles = [role for role, start, end in ranges if start <= s['line'] <= (end or 10**12)]
            if roles:
                kept.append({**s, 'role': 'owned' if 'owned' in roles else 'context'})
        limit = min(400, max(0, 4000-total))
        if len(kept) > limit:
            truncated.append(f'{repo}:{path}')
        kept = kept[:limit]
        total += len(kept)
        files.append(dict(repo_id=repo, path=path, parser=parser, signals=kept))
    target = run_dir / 'inputs/risk-signals' / f'{key}.json'
    write_json(target, dict(format_version='risk-signals-v1', unit_id=task['unit_id'], files=files, limits={'truncated': truncated}, note=NOTE))
    return target
