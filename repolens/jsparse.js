// Syntax-aware JS/TS extraction using the TypeScript compiler API. Reads JSON [{abs}] on stdin.
const fs = require('fs');
let ts;
try {
  ts = require('typescript');
} catch (e) {
  console.log(JSON.stringify({ _fatal: 'typescript compiler not found' }));
  process.exit(0);
}
// Guard against stub/incompatible TypeScript packages (e.g. typescript@7 which only exposes `version`).
if (!ts || typeof ts.createSourceFile !== 'function' || !ts.ScriptKind) {
  console.log(JSON.stringify({ _fatal: 'typescript compiler API unavailable (version ' + (ts && ts.version || '?') + ' is not supported; install typescript@5)' }));
  process.exit(0);
}
const files = JSON.parse(fs.readFileSync(0, 'utf8'));
const KIND = { '.tsx': ts.ScriptKind.TSX, '.jsx': ts.ScriptKind.JSX, '.ts': ts.ScriptKind.TS, '.mts': ts.ScriptKind.TS, '.cts': ts.ScriptKind.TS };
const out = {};
for (const f of files) {
  const r = { imports: [], exports: [], defs: [], error: null };
  out[f.abs] = r;
  try {
    const text = fs.readFileSync(f.abs, 'utf8');
    const ext = f.abs.slice(f.abs.lastIndexOf('.'));
    const sf = ts.createSourceFile(f.abs, text, ts.ScriptTarget.Latest, true, KIND[ext] || ts.ScriptKind.JS);
    const d0 = (sf.parseDiagnostics || [])[0];
    if (d0) r.error = 'SyntaxError line ' + (sf.getLineAndCharacterOfPosition(d0.start).line + 1) + ': ' + ts.flattenDiagnosticMessageText(d0.messageText, ' ');
    const line = n => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;
    const end = n => sf.getLineAndCharacterOfPosition(n.getEnd()).line + 1;
    const str = n => n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n));
    const mod = (n, k) => (ts.canHaveModifiers && ts.canHaveModifiers(n) ? ts.getModifiers(n) || [] : n.modifiers || []).some(m => m.kind === k);
    const def = (name, kind, n) => {
      r.defs.push({ name, kind, line: line(n), end: end(n), parent: null });
      if (mod(n, ts.SyntaxKind.ExportKeyword)) r.exports.push({ name: mod(n, ts.SyntaxKind.DefaultKeyword) ? 'default' : name, kind, line: line(n) });
    };
    const imp = (spec, kind, n) => r.imports.push({ spec, kind, line: line(n) });
    (function visit(n) {
      if (ts.isImportDeclaration(n) && str(n.moduleSpecifier)) imp(n.moduleSpecifier.text, 'import', n);
      else if (ts.isExportDeclaration(n)) {
        if (n.moduleSpecifier && str(n.moduleSpecifier)) imp(n.moduleSpecifier.text, 're-export', n);
        else if (n.exportClause && n.exportClause.elements) n.exportClause.elements.forEach(e => r.exports.push({ name: e.name.text, kind: 'named', line: line(n) }));
      } else if (ts.isExportAssignment(n)) r.exports.push({ name: 'default', kind: 'default', line: line(n) });
      else if (ts.isImportEqualsDeclaration(n) && ts.isExternalModuleReference(n.moduleReference) && str(n.moduleReference.expression)) imp(n.moduleReference.expression.text, 'import', n);
      else if (ts.isCallExpression(n) && n.arguments.length && str(n.arguments[0])) {
        if (ts.isIdentifier(n.expression) && n.expression.text === 'require') imp(n.arguments[0].text, 'require', n);
        else if (n.expression.kind === ts.SyntaxKind.ImportKeyword) imp(n.arguments[0].text, 'dynamic-import', n);
      }
      if (ts.isFunctionDeclaration(n)) def(n.name ? n.name.text : 'default', 'function', n);
      else if (ts.isClassDeclaration(n)) def(n.name ? n.name.text : 'default', 'class', n);
      else if (ts.isInterfaceDeclaration(n)) def(n.name.text, 'interface', n);
      else if (ts.isTypeAliasDeclaration(n)) def(n.name.text, 'type', n);
      else if (ts.isMethodDeclaration(n) && n.name && n.parent && n.parent.name) r.defs.push({ name: n.name.getText(sf), kind: 'method', line: line(n), end: end(n), parent: n.parent.name.text });
      else if (ts.isVariableStatement(n)) {
        const ex = mod(n, ts.SyntaxKind.ExportKeyword);
        n.declarationList.declarations.forEach(d => {
          if (!ts.isIdentifier(d.name)) return;
          const fn = d.initializer && (ts.isArrowFunction(d.initializer) || ts.isFunctionExpression(d.initializer));
          if (fn) r.defs.push({ name: d.name.text, kind: 'function', line: line(d), end: end(d), parent: null });
          if (ex) r.exports.push({ name: d.name.text, kind: fn ? 'function' : 'variable', line: line(n) });
        });
      }
      ts.forEachChild(n, visit);
    })(sf);
  } catch (e) { r.error = 'Analysis failed: ' + e.message; }
}
console.log(JSON.stringify(out));
