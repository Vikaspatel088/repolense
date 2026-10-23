import json, os, sys, tempfile, threading, unittest, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from repolens import engine, scanner
import server


def mk(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self._t.name)

    def tearDown(self):
        self._t.cleanup()


class TestScanner(Base):
    def test_gitignore_and_excludes(self):
        mk(self.d, {'.gitignore': 'secret.py\ngen/\n', 'a.py': '', 'secret.py': '', 'gen/x.py': '', 'node_modules/n.js': '', 'README.md': ''})
        r = scanner.scan(self.d)
        self.assertEqual([f['path'] for f in r['files']], ['a.py'])
        self.assertEqual(r['counts']['unsupported'], 2)  # .gitignore and README.md

    def test_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as out:
            mk(out, {'evil.py': 'x=1'})
            mk(self.d, {'ok.py': ''})
            try:
                os.symlink(os.path.join(out, 'evil.py'), os.path.join(self.d, 'link.py'))
                os.symlink(out, os.path.join(self.d, 'linkdir'))
            except OSError as e:
                # Windows requires SeCreateSymbolicLinkPrivilege (developer mode or elevated process).
                self.skipTest(f'Cannot create symlinks on this system: {e}')
            r = scanner.scan(self.d)
            self.assertEqual([f['path'] for f in r['files']], ['ok.py'])
            self.assertEqual(len(r['skipped']), 2)

    def test_limits(self):
        mk(self.d, {'a.py': '', 'b.py': '', 'big.py': 'x' * 100})
        r = scanner.scan(self.d, max_files=1)
        self.assertEqual(len(r['files']), 1)
        self.assertTrue(r['warnings'])
        r = scanner.scan(self.d, max_bytes=50)
        self.assertEqual([f['path'] for f in r['files']], ['a.py', 'b.py'])
        self.assertTrue(any('larger than' in s['reason'] for s in r['skipped']))

    def test_bad_paths(self):
        with self.assertRaises(FileNotFoundError):
            scanner.scan(os.path.join(self.d, 'nope'))
        mk(self.d, {'f.py': ''})
        with self.assertRaises(scanner.ScanError):
            scanner.scan(os.path.join(self.d, 'f.py'))
        with self.assertRaises(scanner.ScanError):
            scanner.scan('')


class TestPython(Base):
    def repo(self):
        mk(self.d, {'app/__init__.py': '', 'app/main.py': 'from . import util\nfrom .models import User\nimport requests\nimport app.core as c\nfrom app.missing import z\n\nif __name__ == "__main__":\n    pass\n',
                    'app/util.py': 'def helper(): pass\nclass K:\n    def m(self): pass\n', 'app/models.py': 'from app import util\n', 'app/core.py': '', 'bad.py': 'def (:\n'})
        return engine.analyze_repo(self.d)

    def test_edges_direction_and_resolution(self):
        s = self.repo()
        pairs = {(e['source'], e['target']) for e in s['edges']}
        self.assertEqual(pairs, {('app/main.py', 'app/util.py'), ('app/main.py', 'app/models.py'), ('app/main.py', 'app/core.py'), ('app/models.py', 'app/util.py')})
        self.assertEqual(s['_index']['app/util.py']['dependents'], ['app/main.py', 'app/models.py'])

    def test_unresolved_and_errors(self):
        s = self.repo()
        un = {(u['spec'], u['reason']) for u in s['unresolved']}
        self.assertIn(('requests', 'external'), un)
        self.assertIn(('app.missing', 'local-not-found'), un)
        self.assertEqual([e['file'] for e in s['parse_errors']], ['bad.py'])
        self.assertIn('app/main.py', {n['path'] for n in s['files']})  # other files survive

    def test_defs_and_entry(self):
        s = self.repo()
        defs = {(d['name'], d['kind'], d['parent']) for d in s['_index']['app/util.py']['defs']}
        self.assertEqual(defs, {('helper', 'function', None), ('K', 'class', None), ('m', 'method', 'K')})
        self.assertEqual(s['_index']['app/main.py']['entry']['confidence'], 'high')

    def test_similar_names_not_linked(self):
        mk(self.d, {'a/util.py': '', 'b/util2.py': 'import util\n'})
        s = engine.analyze_repo(self.d)
        self.assertEqual(s['edges'], [])

    def test_nested_packages_and_relative_imports(self):
        mk(self.d, {
            'pkg/__init__.py': '',
            'pkg/sub/__init__.py': '',
            'pkg/sub/mod.py': 'from .. import top\nfrom ...nope import x\n',
            'pkg/top.py': 'from .sub.mod import something\n',
        })
        s = engine.analyze_repo(self.d)
        pairs = {(e['source'], e['target']) for e in s['edges']}
        self.assertIn(('pkg/sub/mod.py', 'pkg/top.py'), pairs)
        self.assertIn(('pkg/top.py', 'pkg/sub/mod.py'), pairs)
        self.assertTrue(any('nope' in u['spec'] for u in s['unresolved']))

    def test_self_import_no_loop(self):
        mk(self.d, {'pkg/__init__.py': '', 'pkg/a.py': 'from . import a\nimport pkg.a\n'})
        s = engine.analyze_repo(self.d)
        self_edges = [e for e in s['edges'] if e['source'] == e['target']]
        self.assertEqual(len(self_edges), 0)

    def test_malformed_utf8_recovery(self):
        p = os.path.join(self.d, 'bad_utf8.py')
        with open(p, 'wb') as f:
            f.write(b'# \xff\xfe invalid utf8\nx = 1\n')
        s = engine.analyze_repo(self.d)
        self.assertIn('bad_utf8.py', {f['path'] for f in s['files']})


class TestGraph(Base):
    def test_cycle_and_impact(self):
        mk(self.d, {'a.py': 'import b\n', 'b.py': 'import c\n', 'c.py': 'import a\nimport d\n', 'd.py': '', 'top.py': 'import a\n'})
        s = engine.analyze_repo(self.d)
        self.assertEqual(len(s['cycles']), 1)
        self.assertEqual(s['cycles'][0]['members'], ['a.py', 'b.py', 'c.py'])
        self.assertEqual(s['cycles'][0]['example'], ['a.py', 'b.py', 'c.py', 'a.py'])
        r = engine.impact(s, 'd.py')
        self.assertEqual({x['file'] for x in r['dependents']}, {'a.py', 'b.py', 'c.py', 'top.py'})
        self.assertEqual(r['direct'], ['c.py'])
        top = next(x for x in r['dependents'] if x['file'] == 'top.py')
        self.assertEqual(top['path'], ['top.py', 'a.py', 'b.py', 'c.py', 'd.py'])
        self.assertEqual(engine.impact(s, 'top.py')['count'], 0)

    def test_empty_repo(self):
        s = engine.analyze_repo(self.d)
        self.assertEqual(s['stats']['files'], 0)
        self.assertTrue(s['warnings'])
        self.assertIn('Architecture report', engine.report_markdown(s))

    def test_search(self):
        mk(self.d, {'x.py': 'def find_me(): pass\n'})
        s = engine.analyze_repo(self.d)
        self.assertEqual(engine.search(s, 'find_me')[0]['line'], 1)
        self.assertEqual(engine.search(s, 'zzz'), [])


@unittest.skipUnless(engine.run_js_parser([])[1] is None, 'TypeScript compiler not available')
class TestJS(Base):
    def test_imports_exports(self):
        mk(self.d, {'package.json': '{"main":"src/index.ts"}',
                    'src/index.ts': "import { a } from './lib/a';\nimport x from './b.js';\nimport 'react';\nconst c = require('./c');\nexport default function main() {}\nexport const f = () => 1;\nimport('./nope');\n",
                    'src/lib/a.ts': "export class A { run() {} }\nexport interface I {}\n", 'src/b.ts': "export * from './lib';\n", 'src/lib/index.ts': '',
                    'src/c.jsx': 'export const C = () => <div/>;\n', 'src/broken.js': 'function ( {\n'})
        s = engine.analyze_repo(self.d)
        pairs = {(e['source'], e['target']) for e in s['edges']}
        self.assertEqual(pairs, {('src/index.ts', 'src/lib/a.ts'), ('src/index.ts', 'src/b.ts'), ('src/index.ts', 'src/c.jsx'), ('src/b.ts', 'src/lib/index.ts')})
        un = {(u['spec'], u['reason']) for u in s['unresolved']}
        self.assertEqual(un, {('react', 'external-or-alias'), ('./nope', 'local-not-found')})
        idx = s['_index']
        self.assertEqual({d['name'] for d in idx['src/lib/a.ts']['defs']}, {'A', 'run', 'I'})
        self.assertEqual({e['name'] for e in idx['src/index.ts']['exports']}, {'default', 'f'})
        self.assertEqual(idx['src/index.ts']['entry']['confidence'], 'medium')
        self.assertTrue(idx['src/broken.js']['error'])

    def test_js_extension_and_dir_resolution(self):
        mk(self.d, {
            'index.ts': "import './a.js';\nimport './b';\nimport './pkg';\n",
            'a.ts': '',
            'b.tsx': '',
            'pkg/index.ts': '',
        })
        s = engine.analyze_repo(self.d)
        pairs = {(e['source'], e['target']) for e in s['edges']}
        self.assertEqual(pairs, {('index.ts', 'a.ts'), ('index.ts', 'b.tsx'), ('index.ts', 'pkg/index.ts')})

    def test_js_dynamic_and_boundary_imports(self):
        mk(self.d, {
            'a.js': "import(computedVar);\nimport('./b');\nrequire('./c');\n",
            'b.js': '',
            'c.js': '',
        })
        s = engine.analyze_repo(self.d)
        pairs = {(e['source'], e['target']) for e in s['edges']}
        self.assertEqual(pairs, {('a.js', 'b.js'), ('a.js', 'c.js')})
        self.assertEqual(engine.resolve_js('a.js', '../../secret', {'a.js'}), (None, 'outside-repository'))


class TestAPI(Base):
    def setUp(self):
        super().setUp()
        self.srv = server.make_server(port=0)
        self.base = f'http://127.0.0.1:{self.srv.server_address[1]}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        super().tearDown()

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode()), headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers

    def test_flow_and_errors(self):
        self.assertEqual(self.call('/api/snapshot')[0], 409)
        self.assertEqual(self.call('/api/analyze', b'not json')[0], 400)
        self.assertEqual(self.call('/api/analyze', {'x': 1})[0], 400)
        self.assertEqual(self.call('/api/analyze', {'path': os.path.join(self.d, 'nope')})[0], 404)
        self.assertEqual(self.call('/api/analyze', {'path': self.d}, {'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.call('/api/analyze', b'x' * 70000)[0], 413)
        mk(self.d, {'a.py': 'import b\n', 'b.py': ''})
        code, body, _ = self.call('/api/analyze', {'path': self.d})
        self.assertEqual(code, 200)
        snap = json.loads(body)
        self.assertEqual(snap['stats']['edges'], 1)
        self.assertNotIn('_text', body); self.assertNotIn('_abs', body)
        code, body, _ = self.call('/api/impact?path=b.py')
        self.assertEqual(json.loads(body)['direct'], ['a.py'])
        self.assertEqual(self.call('/api/impact?path=../../etc/passwd')[0], 404)
        self.assertEqual(self.call('/api/source?path=/etc/passwd&line=1')[0], 404)
        self.assertEqual(json.loads(self.call('/api/source?path=a.py&line=1')[1])['lines'], ['import b', ''])
        self.assertEqual(json.loads(self.call('/api/search?q=b.py')[1])['results'][0]['file'], 'b.py')
        code, body, h = self.call('/api/report')
        self.assertIn('attachment', h['Content-Disposition'])
        self.assertIn('`a.py`', body)
        self.assertEqual(self.call('/')[0], 200)

    def test_security_boundaries_and_allowed_root(self):
        mk(self.d, {'a.py': 'x = 1\n'})
        self.assertEqual(self.call('/api/analyze', {'path': self.d})[0], 200)
        self.assertEqual(self.call('/api/source?path=a.py&line=abc')[0], 400)
        with tempfile.TemporaryDirectory() as allowed_dir:
            os.environ['REPOLENS_ALLOWED_ROOT'] = os.path.realpath(allowed_dir)
            try:
                self.assertEqual(self.call('/api/analyze', {'path': self.d})[0], 403)
            finally:
                del os.environ['REPOLENS_ALLOWED_ROOT']


if __name__ == '__main__':
    unittest.main(verbosity=2)
