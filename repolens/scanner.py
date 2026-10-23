"""Safe repository scanner. Never executes or imports scanned code."""
import os, fnmatch

LANGS = {'.py': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
         '.cjs': 'javascript', '.ts': 'typescript', '.tsx': 'typescript', '.mts': 'typescript', '.cts': 'typescript'}
EXCLUDE_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env', 'dist', 'build', '.tox',
                '.mypy_cache', '.next', 'coverage', '.idea', '.pytest_cache', '.svn', '.hg'}


class ScanError(Exception):
    """Invalid scan request (bad path, not a directory)."""


def _load_gitignore(root):
    pats = []
    try:
        with open(os.path.join(root, '.gitignore'), encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith(('#', '!')):
                    pats.append(line)
    except OSError:
        pass
    return pats


def _ignored(rel, is_dir, pats):
    name = rel.rsplit('/', 1)[-1]
    for p in pats:
        if p.endswith('/') and not is_dir:
            continue
        q = p.rstrip('/')
        if q.startswith('/'):
            if fnmatch.fnmatch(rel, q[1:]):
                return True
        elif '/' in q:
            if fnmatch.fnmatch(rel, q):
                return True
        elif fnmatch.fnmatch(name, q):
            return True
    return False


def scan(root, max_files=5000, max_bytes=1_000_000):
    if not isinstance(root, str) or not root.strip():
        raise ScanError('A repository path is required.')
    if '\0' in root:
        raise ScanError('Invalid path.')
    real = os.path.realpath(os.path.expanduser(root.strip()))
    if not os.path.exists(real):
        raise FileNotFoundError('Path does not exist.')
    if not os.path.isdir(real):
        raise ScanError('Path is not a directory.')
    if not os.access(real, os.R_OK | os.X_OK):
        raise PermissionError('Directory is not readable.')
    pats = _load_gitignore(real)
    files, skipped, warnings = [], [], []
    counts = {'unsupported': 0, 'gitignored': 0, 'excluded_dirs': 0}
    truncated = False
    for dp, dns, fns in os.walk(real, followlinks=False):
        rd = os.path.relpath(dp, real)
        rd = '' if rd == '.' else rd.replace(os.sep, '/')
        keep = []
        for d in sorted(dns):
            rel = f'{rd}/{d}' if rd else d
            if os.path.islink(os.path.join(dp, d)):
                skipped.append({'path': rel, 'reason': 'symlink (not followed)'})
            elif d in EXCLUDE_DIRS:
                counts['excluded_dirs'] += 1
            elif _ignored(rel, True, pats):
                counts['gitignored'] += 1
            else:
                keep.append(d)
        dns[:] = keep
        for fn in sorted(fns):
            rel = f'{rd}/{fn}' if rd else fn
            full = os.path.join(dp, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext not in LANGS:
                counts['unsupported'] += 1
                continue
            if _ignored(rel, False, pats):
                counts['gitignored'] += 1
                continue
            if os.path.islink(full):
                skipped.append({'path': rel, 'reason': 'symlink (not followed)'})
                continue
            rp = os.path.realpath(full)
            if rp != real and not rp.startswith(real + os.sep):
                skipped.append({'path': rel, 'reason': 'outside repository boundary'})
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                skipped.append({'path': rel, 'reason': 'unreadable'})
                continue
            if size > max_bytes:
                skipped.append({'path': rel, 'reason': f'larger than {max_bytes} bytes'})
                continue
            if len(files) >= max_files:
                truncated = True
                break
            files.append({'path': rel, 'abs': full, 'lang': LANGS[ext], 'size': size})
        if truncated:
            break
    if truncated:
        warnings.append(f'File limit of {max_files} reached; remaining files were not scanned.')
    return {'root': real, 'files': files, 'skipped': skipped, 'counts': counts, 'warnings': warnings}
