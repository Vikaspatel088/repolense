# RepoLens

RepoLens is a lightweight, local static analysis explorer for Python and JavaScript/TypeScript repositories. It scans your codebase, extracts module definitions and dependency relationships without executing any user code, builds a directed dependency graph, identifies circular dependencies (Tarjan's strongly connected components), computes change impact paths (cycle-safe BFS), and generates downloadable architecture reports.

---

## Capabilities

- **Language Support**: Python (`.py`), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`), and TypeScript (`.ts`, `.tsx`, `.mts`, `.cts`).
- **Static Dependency Extraction**:
  - Python: Parses AST (`ast.parse`) to detect absolute imports, relative package imports (`.`, `..`), `from ... import`, and `if __name__ == '__main__':` entry points.
  - JavaScript/TypeScript: Uses the official TypeScript compiler AST API (`jsparse.js`) for ES module imports/exports, CommonJS `require()`, re-exports, dynamic `import()` with literal specifiers, classes, interfaces, and functions.
- **Graph & Impact Analysis**:
  - Directed import graph (`A -> B` means `A` imports `B`).
  - Circular dependency detection via Tarjan's Strongly Connected Components (SCC) algorithm.
  - Cycle-safe BFS for direct and transitive dependents (impact assessment).
  - Fan-in (most depended-upon) and fan-out (most dependencies) ranking.
- **Interactive UI**:
  - Overview dashboard with language breakdown, entry points, and issue summaries.
  - Interactive SVG force-directed dependency graph with zooming, panning, cycle highlighting, and node selection.
  - File browser with search, line count, in/out degrees, and symbol counts.
  - Symbol search command palette (`Ctrl+K`).
  - Source excerpt viewer with line highlighting.
- **Architecture Report**: One-click export of a structured Markdown summary of repository metrics, cycles, unresolved imports, and top impacted files.

---

## Architecture & Analysis Workflow

1. **Scanner (`repolens/scanner.py`)**:
   - Safely walks the target directory (`os.walk(followlinks=False)`).
   - Enforces repository boundary validation (`os.path.realpath`).
   - Ignores directories like `.git`, `node_modules`, `__pycache__`, virtualenvs, and build outputs.
   - Respects `.gitignore` rules (literal paths, directory prefixes, wildcards).
   - Enforces configurable limits: maximum 5,000 files, 1 MB per file, 60 seconds scan timeout.
   - Does not execute or import scanned files.
2. **Analysis Engine (`repolens/engine.py`)**:
   - Python: Extracts AST nodes for function/class definitions, imports, and `__main__` entry-point guards.
   - JavaScript/TypeScript: Invokes Node.js sub-process with `jsparse.js` via stdin/stdout JSON protocol using the TypeScript compiler API.
   - Resolves module specifiers against discovered repository files.
   - Computes graph adjacencies, reverse dependencies, cycles, and metrics.
3. **HTTP Server (`server.py`)**:
   - Python standard-library `ThreadingHTTPServer` binding to `127.0.0.1` (localhost only).
   - In-memory snapshot storage (no external database required).
   - Endpoints:
     - `POST /api/analyze` — Initiates scan and analysis for a given path.
     - `GET /api/snapshot` — Returns sanitized public repository snapshot.
     - `GET /api/impact?path=<file>` — Computes transitive dependents for `<file>`.
     - `GET /api/search?q=<term>` — Searches files and symbols.
     - `GET /api/source?path=<file>&line=<n>` — Retrieves source lines with context.
     - `GET /api/report` — Downloads Markdown architecture report.
     - `GET /api/health` — Health check endpoint.
4. **Frontend (`frontend/index.html`)**:
   - Single-file zero-dependency interface (vanilla HTML5, CSS `@layer`, standard modern JavaScript).
   - Served directly by the local HTTP server.

---

## Prerequisites & Installation

### Requirements

- **Python**: 3.10+ (tested on Python 3.13)
- **Node.js**: 16+ (tested on Node.js v26)
- **TypeScript**: `^5.0.0` (required for JS/TS analysis; tested on TypeScript 5.9.3)

> [!NOTE]
> `typescript@7` is an experimental stub release that removed the classic compiler API (`ts.createSourceFile`, `ts.ScriptKind`). RepoLens requires TypeScript 5.x.

### Installation Steps

```sh
# Clone the repository
git clone <repository-url>
cd repolens

# Install TypeScript locally for JS/TS analysis
npm install

# Alternatively, install TypeScript globally:
# npm install -g typescript@5
```

---

## Running RepoLens

### Start the Server

```sh
python server.py 8765
```

Once running, navigate to:
```
http://127.0.0.1:8765
```

Enter the absolute path to any local repository (e.g. `C:\Projects\my-app` or `/home/user/projects/my-app`) and click **Analyze**.

### Stopping the Server

Press `Ctrl+C` in the terminal where `server.py` is running to cleanly stop the server process.

### Restricting Permitted Paths (Optional)

To restrict analysis to a specific directory tree:
```sh
# Linux/macOS
export REPOLENS_ALLOWED_ROOT=/path/to/permitted/projects
python server.py 8765

# Windows PowerShell
$env:REPOLENS_ALLOWED_ROOT="C:\Projects"
python server.py 8765
```

---

## Running Tests

RepoLens includes a complete unit and integration test suite:

```sh
# Run all standard tests
python -m unittest discover -s tests -v
```

### Observed Test Results

```
test_flow_and_errors (test_repolens.TestAPI.test_flow_and_errors) ... ok
test_security_boundaries_and_allowed_root (test_repolens.TestAPI.test_security_boundaries_and_allowed_root) ... ok
test_cycle_and_impact (test_repolens.TestGraph.test_cycle_and_impact) ... ok
test_empty_repo (test_repolens.TestGraph.test_empty_repo) ... ok
test_search (test_repolens.TestGraph.test_search) ... ok
test_imports_exports (test_repolens.TestJS.test_imports_exports) ... ok
test_js_dynamic_and_boundary_imports (test_repolens.TestJS.test_js_dynamic_and_boundary_imports) ... ok
test_js_extension_and_dir_resolution (test_repolens.TestJS.test_js_extension_and_dir_resolution) ... ok
test_defs_and_entry (test_repolens.TestPython.test_defs_and_entry) ... ok
test_edges_direction_and_resolution (test_repolens.TestPython.test_edges_direction_and_resolution) ... ok
test_malformed_utf8_recovery (test_repolens.TestPython.test_malformed_utf8_recovery) ... ok
test_nested_packages_and_relative_imports (test_repolens.TestPython.test_nested_packages_and_relative_imports) ... ok
test_self_import_no_loop (test_repolens.TestPython.test_self_import_no_loop) ... ok
test_similar_names_not_linked (test_repolens.TestPython.test_similar_names_not_linked) ... ok
test_unresolved_and_errors (test_repolens.TestPython.test_unresolved_and_errors) ... ok
test_bad_paths (test_repolens.TestScanner.test_bad_paths) ... ok
test_gitignore_and_excludes (test_repolens.TestScanner.test_gitignore_and_excludes) ... ok
test_limits (test_repolens.TestScanner.test_limits) ... ok
test_symlink_not_followed (test_repolens.TestScanner.test_symlink_not_followed) ... skipped (requires Windows SeCreateSymbolicLinkPrivilege)

Ran 19 tests in ~2.1s: 18 passed, 1 skipped (OS permission requirement), 0 failed.
```

---

## Security Model & Privacy Boundaries

- **Local-Only Deployment**: RepoLens is strictly designed for local operation on `127.0.0.1` (loopback). It does not listen on public network interfaces and is not an internet-hosted web service or static site. Static hosting solutions (like GitHub Pages) cannot host RepoLens because it requires the active local Python backend (`server.py`) and Node.js parser (`jsparse.js`) to perform repository scanning and AST extraction.
- **Zero Execution of User Code**: Source code is read and parsed via standard AST analyzers (`ast.parse` and TypeScript compiler API). Scanned files are never executed, imported into the runtime, or evaluated (`eval`/`exec`).
- **No Data Exfiltration**: Snapshots live exclusively in server memory. No analytics, tracking, or network requests are dispatched outside localhost.
- **Path Traversal Defenses**:
  - Repository roots are validated with `os.path.realpath`.
  - Scanned file paths must strictly reside within the root (`rp.startswith(real + os.sep)`).
  - API endpoints (`/api/source`, `/api/impact`) reject relative traversal (`..`) and non-repository paths with HTTP 404.
  - JS module resolution flags `outside-repository` when specifiers navigate above the repo root.
- **Sanitized Public Responses**: Internal memory structures (e.g., `_text` full source buffers, `_abs` filesystem paths, `_index`, and `_rev` reverse maps) are stripped from public API JSON snapshots.
- **Cross-Origin Protections**: `POST /api/analyze` enforces origin verification against the `Host` header to reject cross-origin requests.
- **Payload Limits**: Request bodies are capped at 64 KB (`MAX_BODY`) to prevent denial-of-service.

---

## Parser Availability & Fallback Behavior

- **TypeScript API Available**: When TypeScript `^5.0.0` is installed (either locally in `./node_modules` or globally via `npm root -g`), JavaScript and TypeScript files are analyzed with full AST support.
- **TypeScript API Unavailable**: If Node.js or a supported TypeScript compiler is missing:
  - Python analysis continues to work completely without interruption.
  - JS/TS files are safely inventoried in the file list.
  - Files are marked with `parser unavailable` status rather than failing the scan or misrepresenting parse syntax errors.
  - A descriptive warning is surfaced in the UI and exported report detailing how to install TypeScript.

---

## Measured Performance

Benchmarked on local fixtures (Python 3.13, Windows 11):

| Fixture | Discovered Files | Graph Edges | Detected Cycles | Duration |
|---|---|---|---|---|
| **Small Python** | 10 files (18 LOC) | 8 | 0 | **4.1 ms** |
| **Medium Python** | 100 files (300 LOC) | 197 | 1 | **29.0 ms** |
| **Large Python** | 306 files (600 LOC) | 300 | 6 | **87.9 ms** |
| **Mixed Py + JS/TS** | 100 files (50 Py, 25 JS, 25 TS) | 100 | 3 | **382.5 ms** |
| **Syntax Error Stress** | 150 files (15 with SyntaxErrors) | — | — | **34.6 ms** |

---

## Known Limitations

1. **Static Resolution Only**: Dynamic import expressions with computed variable specifiers (e.g., `import(variableName)` or `importlib.import_module(name)`) are not resolved to dependency edges.
2. **TypeScript Path Aliases**: Custom `paths` configured in `tsconfig.json` are reported as `external-or-alias`.
3. **Python `sys.path` Detection**: Module resolution assumes the scanned root is the import root (custom `src/` layouts or package installations are treated relative to the scanned directory).
4. **Negated `.gitignore` Patterns**: Rules prefixed with `!` are not currently un-ignored.
5. **Windows Symlink Creation**: On Windows, creating symlinks for testing requires elevated administrative privileges or Developer Mode enabled (`SeCreateSymbolicLinkPrivilege`). The scanner safely ignores existing symlinks on all platforms.
