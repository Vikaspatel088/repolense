"""Analysis engine. Edge direction everywhere: edge(source -> target) means `source` imports/depends on `target`."""
import ast, os, json, time, subprocess, datetime, posixpath
from collections import deque, defaultdict
from .scanner import scan

HERE = os.path.dirname(os.path.abspath(__file__))
JS_EXT = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.mts', '.cts']
_node_path = None


def _node_env():
    """Build a NODE_PATH that includes the local node_modules (next to this package's parent)
    and the global npm root so that `require('typescript')` is found regardless of how Node
    resolves modules from the jsparse.js location.
    """
    global _node_path
    if _node_path is None:
        try:
            _node_path = subprocess.run(['npm', 'root', '-g'], capture_output=True, text=True, timeout=15, shell=(os.name == 'nt')).stdout.strip()
        except Exception:
            _node_path = ''
    env = dict(os.environ)
    # Prefer the local node_modules installed next to this project over the global one.
    local_nm = os.path.join(os.path.dirname(HERE), 'node_modules')
    parts = [p for p in [local_nm, _node_path, env.get('NODE_PATH', '')] if p]
    if parts:
        env['NODE_PATH'] = os.pathsep.join(parts)
    return env


# ---------- Python ----------
def analyze_python(src):
    r = {'imports': [], 'defs': [], 'main_guard': False, 'error': None}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        r['error'] = f'SyntaxError line {e.lineno}: {e.msg}'
        return r
    except (ValueError, RecursionError, MemoryError) as e:
        r['error'] = f'Could not parse: {type(e).__name__}'
        return r

    def walk(body, parent=None):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                r['defs'].append({'name': n.name, 'kind': 'method' if parent else 'function', 'line': n.lineno,
                                  'end': getattr(n, 'end_lineno', n.lineno), 'parent': parent})
            elif isinstance(n, ast.ClassDef):
                r['defs'].append({'name': n.name, 'kind': 'class', 'line': n.lineno,
                                  'end': getattr(n, 'end_lineno', n.lineno), 'parent': None})
                walk(n.body, n.name)
    walk(tree.body)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                r['imports'].append({'kind': 'import', 'module': a.name, 'level': 0, 'names': [], 'line': n.lineno})
        elif isinstance(n, ast.ImportFrom):
            r['imports'].append({'kind': 'from', 'module': n.module or '', 'level': n.level,
                                 'names': [a.name for a in n.names], 'line': n.lineno})
    for n in tree.body:
        t = getattr(n, 'test', None)
        if isinstance(n, ast.If) and isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == '__name__' \
                and any(isinstance(c, ast.Constant) and c.value == '__main__' for c in t.comparators):
            r['main_guard'] = True
    r['imports'].sort(key=lambda i: i['line'])
    return r


def py_modname(rel):
    parts = rel[:-3].split('/')
    if parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def resolve_python(rel, imp, mods):
    """Returns ([(target_path, evidence)], None) or ([], (reason, text))."""
    pkg = rel[:-3].split('/')[:-1]
    tops = {m.split('.')[0] for m in mods}
    if imp['kind'] == 'import':
        m = imp['module']
        if m in mods:
            return [(mods[m], f'import {m}')], None
        return [], ('local-not-found' if m.split('.')[0] in tops else 'external', m)
    level, mod, names = imp['level'], imp['module'], imp['names']
    if level:
        if level - 1 > len(pkg):
            return [], ('invalid-relative', '.' * level + mod)
        base = pkg[:len(pkg) - (level - 1)]
        full = '.'.join(base + ([mod] if mod else []))
    else:
        full = mod
    label = f"from {'.' * level}{mod} import {', '.join(names)}"
    real_names = [n for n in names if n != '*']
    hits = [f'{full}.{n}' if full else n for n in real_names]
    hits = [h for h in hits if h in mods]
    out = [(mods[h], label) for h in hits]
    if full in mods and (not hits or len(hits) < len(names)):
        out.append((mods[full], label))
    if out:
        return out, None
    top = full.split('.')[0] if full else ''
    return [], ('local-not-found' if (level or top in tops) else 'external', '.' * level + mod)


# ---------- JS / TS ----------
def resolve_js(rel, spec, fileset):
    if not spec.startswith('.'):
        return None, 'external-or-alias'
    base = posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec))
    if base.startswith('..'):
        return None, 'outside-repository'
    stem, ext = posixpath.splitext(base)
    cands = [base]
    if ext in ('.js', '.jsx', '.mjs', '.cjs'):
        cands += [stem + e for e in ('.ts', '.tsx', '.mts', '.cts')]
    cands += [base + e for e in JS_EXT] + [base + '/index' + e for e in JS_EXT]
    for c in cands:
        if c in fileset:
            return c, None
    return None, 'local-not-found'


def run_js_parser(files):
    try:
        p = subprocess.run(['node', os.path.join(HERE, 'jsparse.js')], input=json.dumps([{'abs': f.get('abs') or f['_abs']} for f in files]),
                           capture_output=True, text=True, timeout=120, env=_node_env())
        data = json.loads(p.stdout)
    except Exception as e:
        return None, f'JS/TS parser could not run ({type(e).__name__}).'
    if '_fatal' in data:
        return None, 'JS/TS parser unavailable: ' + data['_fatal']
    return data, None


# ---------- Graph algorithms ----------
def sccs(nodes, adj):
    idx, low, on, st, res, c = {}, {}, set(), [], [], 0
    for s in nodes:
        if s in idx:
            continue
        idx[s] = low[s] = c; c += 1; st.append(s); on.add(s)
        work = [(s, iter(adj.get(s, ())))]
        while work:
            v, it = work[-1]
            for w in it:
                if w not in idx:
                    idx[w] = low[w] = c; c += 1; st.append(w); on.add(w)
                    work.append((w, iter(adj.get(w, ()))))
                    break
                elif w in on:
                    low[v] = min(low[v], idx[w])
            else:
                work.pop()
                if work:
                    u = work[-1][0]
                    low[u] = min(low[u], low[v])
                if low[v] == idx[v]:
                    comp = []
                    while True:
                        w = st.pop(); on.discard(w); comp.append(w)
                        if w == v:
                            break
                    res.append(comp)
    return res


def find_cycles(nodes, adj):
    out = []
    for comp in sccs(nodes, adj):
        cs = set(comp)
        if len(comp) == 1 and comp[0] not in adj.get(comp[0], ()):
            continue
        start = sorted(comp)[0]
        prev, dq, last = {start: None}, deque([start]), None
        while dq and last is None:
            v = dq.popleft()
            for w in adj.get(v, ()):
                if w == start:
                    last = v
                    break
                if w in cs and w not in prev:
                    prev[w] = v
                    dq.append(w)
        path, cur = [], last
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        path.reverse()
        out.append({'members': sorted(comp), 'example': path + [start]})
    return sorted(out, key=lambda c: -len(c['members']))


def impact(snapshot, target):
    """Files that (transitively) import `target`. Paths read dependent -> ... -> target."""
    if target not in snapshot['_index']:
        raise KeyError(target)
    rev = snapshot['_rev']
    parent, dist, dq = {target: None}, {target: 0}, deque([target])
    while dq:
        v = dq.popleft()
        for w in rev.get(v, ()):
            if w not in parent:
                parent[w] = v; dist[w] = dist[v] + 1; dq.append(w)
    res = []
    for f, d in dist.items():
        if f == target:
            continue
        path, cur = [], f
        while cur is not None:
            path.append(cur); cur = parent[cur]
        res.append({'file': f, 'distance': d, 'path': path})
    res.sort(key=lambda x: (x['distance'], x['file']))
    return {'target': target, 'granularity': 'file', 'direct': [r['file'] for r in res if r['distance'] == 1],
            'dependents': res, 'count': len(res),
            'note': 'Static import analysis estimates which files could be affected by a change. It does not prove runtime '
                    'behavior, and it is file-level only: symbol-level impact is not established.'}


# ---------- Orchestration ----------
def analyze_repo(path, max_files=5000, max_bytes=1_000_000, max_seconds=60):
    t0 = time.time()
    sc = scan(path, max_files, max_bytes)
    warnings, skipped = list(sc['warnings']), list(sc['skipped'])
    files, order = {}, []
    for f in sc['files']:
        if time.time() - t0 > max_seconds:
            warnings.append(f'Time limit of {max_seconds}s reached; remaining files were not analyzed.')
            break
        try:
            with open(f['abs'], 'rb') as fh:
                text = fh.read().decode('utf-8', errors='replace')
        except OSError:
            skipped.append({'path': f['path'], 'reason': 'unreadable'})
            continue
        files[f['path']] = {'path': f['path'], 'lang': f['lang'], 'size': f['size'], 'loc': text.count('\n') + (1 if text and not text.endswith('\n') else 0),
                            'defs': [], 'exports': [], 'imports': [], 'error': None, 'entry': None, '_abs': f['abs'], '_text': text}
        order.append(f['path'])
    fileset = set(files)
    mods = {py_modname(p): p for p in order if files[p]['lang'] == 'python' and py_modname(p)}
    edges, unresolved = {}, []

    def add_edge(src, tgt, kind, line, ev):
        if src == tgt:
            return
        e = edges.setdefault((src, tgt), {'source': src, 'target': tgt, 'type': 'import', 'status': 'confirmed', 'evidence': []})
        e['evidence'].append({'line': line, 'kind': kind, 'text': ev})

    main_guard = {}
    for p in order:
        fi = files[p]
        if fi['lang'] != 'python':
            continue
        a = analyze_python(fi['_text'])
        fi['defs'], fi['error'] = a['defs'], a['error']
        main_guard[p] = a['main_guard']
        for imp in a['imports']:
            tg, un = resolve_python(p, imp, mods)
            for t, ev in tg:
                add_edge(p, t, imp['kind'], imp['line'], ev)
            if un:
                unresolved.append({'file': p, 'line': imp['line'], 'spec': un[1], 'reason': un[0]})
    jsf = [files[p] for p in order if files[p]['lang'] != 'python']
    if jsf:
        data, err = run_js_parser(jsf)
        if err:
            warnings.append(err + ' JS/TS files are listed but not analyzed.')
            for f in jsf:
                f['error'] = 'not analyzed (parser unavailable)'
        else:
            for f in jsf:
                r = data.get(f['_abs'], {})
                f['defs'], f['exports'], f['error'] = r.get('defs', []), r.get('exports', []), r.get('error')
                for imp in r.get('imports', []):
                    t, why = resolve_js(f['path'], imp['spec'], fileset)
                    if t:
                        add_edge(f['path'], t, imp['kind'], imp['line'], f"{imp['kind']} '{imp['spec']}'")
                    else:
                        unresolved.append({'file': f['path'], 'line': imp['line'], 'spec': imp['spec'], 'reason': why})
    # entry points (heuristic, labelled with basis)
    for p in order:
        fi, base = files[p], posixpath.basename(p)
        if main_guard.get(p):
            fi['entry'] = {'confidence': 'high', 'basis': "contains an `if __name__ == '__main__'` guard"}
        elif base in ('__main__.py', 'manage.py', 'main.py', 'app.py', 'server.py', 'cli.py', 'index.js', 'index.ts', 'main.js', 'main.ts', 'server.js', 'server.ts', 'app.js', 'app.ts'):
            fi['entry'] = {'confidence': 'low', 'basis': f'conventional file name `{base}` (heuristic)'}
    try:
        with open(os.path.join(sc['root'], 'package.json'), encoding='utf-8') as fh:
            pj = json.load(fh)
        decl = [pj.get('main')] + (list(pj['bin'].values()) if isinstance(pj.get('bin'), dict) else [pj.get('bin')])
        for d in decl:
            if isinstance(d, str):
                n = posixpath.normpath(d)
                if n in fileset:
                    files[n]['entry'] = {'confidence': 'medium', 'basis': 'declared in package.json (main/bin)'}
    except (OSError, ValueError, AttributeError):
        pass
    nodes = [files[p] for p in order]
    el = sorted(edges.values(), key=lambda e: (e['source'], e['target']))
    adj, rev = defaultdict(list), defaultdict(list)
    for e in el:
        adj[e['source']].append(e['target']); rev[e['target']].append(e['source'])
    for n in nodes:
        n['depends_on'] = adj.get(n['path'], [])
        n['dependents'] = rev.get(n['path'], [])
    cycles = find_cycles(order, adj)
    lang_counts = defaultdict(int)
    for n in nodes:
        lang_counts[n['lang']] += 1
    parse_errors = [{'file': n['path'], 'error': n['error']} for n in nodes if n['error']]
    if not nodes:
        warnings.append('No supported source files (Python, JavaScript, TypeScript) were found.')
    for n in nodes:
        n['fan_in'], n['fan_out'] = len(n['dependents']), len(n['depends_on'])
    stats = {'files': len(nodes), 'loc': sum(n['loc'] for n in nodes), 'languages': dict(lang_counts), 'edges': len(el),
             'unresolved': len(unresolved), 'unresolved_local': sum(1 for u in unresolved if u['reason'] != 'external' and u['reason'] != 'external-or-alias'),
             'cycles': len(cycles), 'parse_errors': len(parse_errors), 'skipped': len(skipped),
             'top_fan_in': [{'file': n['path'], 'count': n['fan_in']} for n in sorted(nodes, key=lambda n: -n['fan_in'])[:10] if n['fan_in']],
             'top_fan_out': [{'file': n['path'], 'count': n['fan_out']} for n in sorted(nodes, key=lambda n: -n['fan_out'])[:10] if n['fan_out']]}
    snap = {'root': sc['root'], 'analyzed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
            'duration_s': round(time.time() - t0, 3), 'stats': stats, 'files': nodes, 'edges': el, 'unresolved': unresolved,
            'cycles': cycles, 'parse_errors': parse_errors, 'skipped': skipped, 'ignored_counts': sc['counts'], 'warnings': warnings,
            'limits': {'max_files': max_files, 'max_bytes': max_bytes, 'max_seconds': max_seconds}}
    snap['_index'] = {n['path']: n for n in nodes}
    snap['_rev'] = rev
    return snap


def public(snap):
    """Snapshot without private fields (source text, absolute paths, indexes)."""
    out = {k: v for k, v in snap.items() if not k.startswith('_')}
    out['files'] = [{k: v for k, v in f.items() if not k.startswith('_')} for f in snap['files']]
    return out


def search(snap, q, limit=50):
    q = (q or '').strip().lower()
    if not q:
        return []
    res = []
    for f in snap['files']:
        if q in f['path'].lower():
            res.append({'type': 'file', 'name': f['path'], 'file': f['path'], 'line': 1})
        for d in f['defs']:
            if q in d['name'].lower():
                res.append({'type': d['kind'], 'name': (d['parent'] + '.' if d['parent'] else '') + d['name'], 'file': f['path'], 'line': d['line']})
    res.sort(key=lambda r: (not r['name'].lower().startswith(q), r['type'] != 'file', r['name']))
    return res[:limit]


def source_excerpt(snap, rel, line, context=8):
    f = snap['_index'].get(rel)
    if not f:
        raise KeyError(rel)
    # Normalize CRLF → LF before splitting so line content is clean on all platforms.
    lines = f['_text'].replace('\r\n', '\n').replace('\r', '\n').split('\n')
    a, b = max(1, line - context), min(len(lines), line + context)
    return {'file': rel, 'start': a, 'line': line, 'lines': lines[a - 1:b]}


def report_markdown(snap):
    s, L = snap['stats'], []
    L += [f"# Architecture report: `{os.path.basename(snap['root'])}`", '',
          f"Analyzed {snap['analyzed_at']} in {snap['duration_s']}s. All figures derive from static analysis of the scanned files.", '',
          '## Inventory', '', f"- Files analyzed: {s['files']} ({s['loc']} lines)"]
    L += [f'- {k}: {v} files' for k, v in sorted(s['languages'].items())]
    L += [f"- Dependency edges (confirmed local imports): {s['edges']}",
          f"- Unresolved imports: {s['unresolved']} ({s['unresolved_local']} point at local-looking paths that were not found)",
          f"- Circular dependency groups: {s['cycles']}", f"- Files with parse errors: {s['parse_errors']}", f"- Skipped files: {s['skipped']}", '',
          '## Entry points (heuristic; basis shown)', '']
    eps = [f for f in snap['files'] if f['entry']]
    L += [f"- `{f['path']}` ({f['entry']['confidence']}): {f['entry']['basis']}" for f in eps] or ['- None identified.']
    L += ['', '## Most depended-upon files (fan-in)', ''] + [f"- `{x['file']}`: imported by {x['count']} files" for x in s['top_fan_in']] or ['- None.']
    L += ['', '## Files with most dependencies (fan-out)', ''] + [f"- `{x['file']}`: imports {x['count']} local files" for x in s['top_fan_out']] or ['- None.']
    L += ['', '## Circular dependencies', '']
    L += [f"- {' -> '.join('`'+p+'`' for p in c['example'])} ({len(c['members'])} files in group)" for c in snap['cycles']] or ['- None detected.']
    L += ['', '## Unresolved imports', '']
    L += [f"- `{u['file']}:{u['line']}` `{u['spec']}` ({u['reason']})" for u in snap['unresolved'][:200]] or ['- None.']
    if len(snap['unresolved']) > 200:
        L.append(f"- ... and {len(snap['unresolved']) - 200} more")
    L += ['', '## Potential impact (most depended-upon files)', '']
    for x in s['top_fan_in'][:5]:
        r = impact(snap, x['file'])
        L.append(f"- Changing `{x['file']}` may affect {r['count']} files ({len(r['direct'])} directly).")
    if not s['top_fan_in']:
        L.append('- No dependents found.')
    L += ['', '## Parse errors', ''] + [f"- `{e['file']}`: {e['error']}" for e in snap['parse_errors']] or ['- None.']
    L += ['', '## Limitations and warnings', '']
    L += [f'- {w}' for w in snap['warnings']]
    L += ['- Only imports with literal module names are tracked; dynamic imports with computed names, `importlib`, and plugin loading are not.',
          '- JS/TS path aliases (tsconfig `paths`) and bare package imports are reported as unresolved/external, not guessed.',
          '- Python resolution assumes the scanned directory is the import root (no `src/` layout or sys.path detection).',
          '- Function-call relationships are not extracted; impact analysis is file-level.',
          '- Impact figures estimate potential effect from static imports and do not prove runtime behavior.']
    return '\n'.join(L) + '\n'
