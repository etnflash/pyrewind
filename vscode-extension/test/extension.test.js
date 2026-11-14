// VS Code 확장 테스트 - 가짜 vscode API 로 extension.js 를 실제 파이썬 트레이서와 함께 실행한다.
//   node vscode-extension/test/extension.test.js
// 환경 변수 PYREWIND_PYTHON 으로 사용할 python 을 지정할 수 있다 (기본: python / python3).
"use strict";
const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");

const EXT_DIR = path.resolve(__dirname, "..");
const REPO = path.resolve(EXT_DIR, "..");
const EXAMPLES = path.join(REPO, "examples");
const PYTHON = process.env.PYREWIND_PYTHON || (process.platform === "win32" ? "python" : "python3");

/* ------------------------------------------------------------ 가짜 vscode */
class Uri {
  constructor(p, scheme = "file") { this.fsPath = p; this.scheme = scheme; }
  toString() { return `${this.scheme}:${this.fsPath}`; }
  static file(p) { return new Uri(p); }
}
class Range { constructor(a, b, c, d) { this.start = { line: a, character: b }; this.end = { line: c, character: d }; this.line = a; } }

const state = {};
// 테스트마다 초기화. 단, 확장이 한 번 만든 웹뷰 패널과 빠른 수정 제공자는 계속 재사용되므로 유지한다.
function reset() {
  Object.assign(state, {
    panel: state.panel || null, onMessage: state.onMessage || null, provider: state.provider || null,
    diags: new Map(),
    applied: [], terminals: [], errors: [], posts: [], editors: [], docs: [], notebooks: [], choice: null,
  });
}
reset();

function makeDoc(uri, text) {
  const lines = text.split(/\r?\n/);
  return {
    uri, languageId: "python", lineCount: lines.length, isDirty: false,
    getText: () => text, save: async () => true,
    lineAt: i => ({ text: lines[i], range: new Range(i, 0, i, lines[i].length) }),
  };
}
function selection(a, ac, b, bc) {
  return { start: { line: a, character: ac }, end: { line: b, character: bc }, active: { line: b, character: bc },
    isEmpty: a === b && ac === bc };
}
function makeEditor(doc) {
  return {
    document: doc, decos: new Map(), revealed: null, selection: selection(0, 0, 0, 0),
    setDecorations(type, ranges) { this.decos.set(type, ranges); },
    revealRange(r) { this.revealed = r.line; },
  };
}

const commands = {};
const decoTypes = [];
const vscode = {
  Uri, Range,
  Diagnostic: class { constructor(r, m, s) { this.range = r; this.message = m; this.severity = s; } },
  DiagnosticSeverity: { Error: 0 },
  CodeActionKind: { QuickFix: "quickfix" },
  CodeAction: class { constructor(t, k) { this.title = t; this.kind = k; } },
  WorkspaceEdit: class { constructor() { this.ops = []; } replace(u, r, t) { this.ops.push({ uri: u.toString(), range: r, text: t }); } },
  ThemeColor: class {}, ThemeIcon: class {},
  OverviewRulerLane: { Full: 7 }, StatusBarAlignment: { Left: 1 }, ViewColumn: { Beside: -2 },
  ProgressLocation: { Notification: 15, Window: 10 }, TextEditorRevealType: { InCenterIfOutsideViewport: 2 },
  NotebookCellKind: { Markup: 1, Code: 2 },
  languages: {
    createDiagnosticCollection: () => ({
      set: (u, d) => state.diags.set(u.toString(), d), delete: u => state.diags.delete(u.toString()),
      clear: () => state.diags.clear(), dispose() {},
    }),
    registerCodeActionsProvider: (_sel, p) => { state.provider = p; return { dispose() {} }; },
  },
  window: {
    createOutputChannel: () => ({ append() {}, appendLine() {}, show() {}, dispose() {} }),
    createTextEditorDecorationType: o => { const t = { o, dispose() {} }; decoTypes.push(t); return t; },
    createStatusBarItem: () => ({ show() {}, hide() {}, dispose() {} }),
    onDidChangeVisibleTextEditors: () => ({ dispose() {} }),
    onDidChangeActiveTextEditor: () => ({ dispose() {} }),
    onDidChangeActiveNotebookEditor: () => ({ dispose() {} }),
    get visibleTextEditors() { return state.editors; },
    activeTextEditor: null, activeNotebookEditor: null,
    withProgress: (_o, fn) => fn({}, { onCancellationRequested() {} }),
    showErrorMessage: (m, ...buttons) => { state.errors.push({ m, buttons }); return Promise.resolve(state.choice); },
    showWarningMessage: () => Promise.resolve(),
    showInformationMessage: () => Promise.resolve(),
    setStatusBarMessage: () => {},
    showInputBox: async () => "",
    createTerminal: name => { const t = { name, sent: [], show() {}, sendText(s) { this.sent.push(s); } }; state.terminals.push(t); return t; },
    createWebviewPanel: () => (state.panel = {
      webview: { html: "", onDidReceiveMessage: h => { state.onMessage = h; }, postMessage: m => state.posts.push(m) },
      onDidDispose() {}, reveal() {}, title: "",
    }),
  },
  workspace: {
    onDidSaveTextDocument: () => ({ dispose() {} }),
    onDidSaveNotebookDocument: () => ({ dispose() {} }),
    onDidChangeTextDocument: () => ({ dispose() {} }),
    getConfiguration: () => ({ get: k => ({ pythonPath: PYTHON, maxSteps: 3000, inlineValues: true, timeoutSeconds: 120 }[k]) }),
    getWorkspaceFolder: () => ({ uri: { fsPath: REPO } }),
    get notebookDocuments() { return state.notebooks; },
    get textDocuments() { return state.docs; },
    applyEdit: async e => { state.applied.push(...e.ops); return true; },
  },
  extensions: { getExtension: () => undefined },
  commands: {
    registerCommand: (id, fn) => { commands[id] = fn; return { dispose() {} }; },
    executeCommand: async (id, ...a) => (commands[id] ? commands[id](...a) : undefined),
  },
};
const originalLoad = Module._load;
Module._load = function (request, ...rest) {
  return request === "vscode" ? vscode : originalLoad.call(this, request, ...rest);
};

/* ------------------------------------------------------------ 도우미 */
const ext = require(path.join(EXT_DIR, "extension.js"));
ext.activate({ extensionPath: EXT_DIR, subscriptions: [], workspaceState: { get: (_k, d) => d, update: async () => {} } });
const [DECO_NEXT, DECO_PREV, DECO_ERR, DECO_NOTE] = decoTypes;
const tick = () => new Promise(r => setTimeout(r, 30));

function traceFromHtml() {
  const m = state.panel.webview.html.match(/const TRACE = (\{.*?\});\n\(function/s);
  assert.ok(m, "웹뷰 HTML 에 trace 데이터가 있어야 함");
  return JSON.parse(m[1].replace(/<\\\//g, "</"));
}
const lines = (ed, type) => (ed.decos.get(type) || []).map(r => (r.range ? r.range.line : r.line));

async function openPy(file) {
  const doc = makeDoc(Uri.file(file), fs.readFileSync(file, "utf8"));
  const ed = makeEditor(doc);
  state.docs.push(doc);
  state.editors.push(ed);
  vscode.window.activeTextEditor = ed;
  vscode.window.activeNotebookEditor = null;
  await commands["pyrewind.run"]();
  await tick();
  return { doc, ed };
}

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pyrewind-ext-test-"));
function tmpPy(name, code) { const p = path.join(tmpDir, name); fs.writeFileSync(p, code, "utf8"); return p; }

/* ------------------------------------------------------------ 테스트 */
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test(".py 실행 → 웹뷰에 trace 가 담긴 뷰어가 열린다", async () => {
  await openPy(path.join(EXAMPLES, "basics.py"));
  const t = traceFromHtml();
  assert.ok(t.steps.length > 10);
  assert.equal(t.error, null);
  assert.ok(state.panel.title.includes("basics.py"));
});

test("뷰어 HTML 의 스크립트에 문법 오류가 없다", async () => {
  await openPy(path.join(EXAMPLES, "basics.py"));
  const scripts = [...state.panel.webview.html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  assert.ok(scripts.length >= 1);
  for (const code of scripts) new (require("node:vm").Script)(code);  // 컴파일만 (실행 X)
});

test("웹뷰의 단계 메시지 → 에디터 줄 강조 + 줄 끝 변화 표시", async () => {
  const { ed } = await openPy(path.join(EXAMPLES, "basics.py"));
  state.onMessage({ type: "step", step: 5, total: 60, next: 8, prev: 7, err: null, notes: ["total = 0"] });
  assert.deepEqual(lines(ed, DECO_NEXT), [7]);   // 0부터 세는 줄 번호
  assert.deepEqual(lines(ed, DECO_PREV), [6]);
  assert.equal(ed.revealed, 7);
  const note = ed.decos.get(DECO_NOTE)[0];
  assert.equal(note.range.line, 6);
  assert.ok(note.renderOptions.after.contentText.includes("total = 0"));
});

test("실행 오류 → 정확한 줄에 진단(빨간 밑줄) + 한국어 알림", async () => {
  const { doc, ed } = await openPy(path.join(EXAMPLES, "bug_hunt.py"));
  const d = state.diags.get(doc.uri.toString());
  assert.equal(d.length, 1);
  assert.equal(d[0].range.start.line, 21);  // 22번 줄
  assert.ok(d[0].message.includes("IndexError"));
  assert.ok(state.errors.at(-1).m.includes("22번 줄"));
  state.onMessage({ type: "step", step: 61, total: 62, next: null, prev: 32, err: 22, notes: [] });
  assert.deepEqual(lines(ed, DECO_ERR), [21]);
});

test("에디터 커서 줄 → 그 줄이 실행되는 시점으로 이동 요청", async () => {
  const { ed } = await openPy(path.join(EXAMPLES, "bug_hunt.py"));
  ed.selection.active.line = 21;
  state.posts.length = 0;
  await commands["pyrewind.jumpToLine"]();
  assert.deepEqual(state.posts.at(-1), { type: "jumpToLine", line: 22 });
});

test("노트북: trace 줄 번호 ↔ 셀 안 줄 번호 변환", async () => {
  const nbPath = path.join(EXAMPLES, "notebook_demo.ipynb");
  const nbJson = JSON.parse(fs.readFileSync(nbPath, "utf8"));
  const cells = nbJson.cells.map((c, i) => ({
    kind: c.cell_type === "code" ? 2 : 1,
    document: makeDoc(new Uri(`${nbPath}#${i}`, "vscode-notebook-cell"), [].concat(c.source).join("")),
  }));
  const nb = { uri: Uri.file(nbPath), isDirty: false, getCells: () => cells, save: async () => true };
  state.notebooks.push(nb);
  const second = cells.filter(c => c.kind === 2)[1];   // 두 번째 코드 셀
  const ed = makeEditor(second.document);
  state.editors.push(ed);
  vscode.window.activeTextEditor = ed;
  vscode.window.activeNotebookEditor = { notebook: nb };
  await commands["pyrewind.run"]();
  await tick();
  const t = traceFromHtml();
  const meta = t.cells[1];
  state.onMessage({ type: "step", step: 3, total: t.steps.length, next: meta.start + 3, prev: meta.start + 2, err: null, notes: [] });
  assert.deepEqual(lines(ed, DECO_NEXT), [3]);
  assert.deepEqual(lines(ed, DECO_PREV), [2]);
  ed.selection.active.line = 1;
  await commands["pyrewind.jumpToLine"]();
  assert.deepEqual(state.posts.at(-1), { type: "jumpToLine", line: meta.start + 1 });
});

test("메서드 이름 오타 → 알림 버튼과 전구 빠른 수정으로 고치기", async () => {
  const file = tmpPy("attr_typo.py", "nums = [3, 1, 2]\nnums.apend(4)\n");
  state.choice = "'append'(으)로 고치기";
  const { doc } = await openPy(file);
  state.choice = null;
  assert.ok(state.errors.at(-1).buttons.includes("'append'(으)로 고치기"));
  assert.deepEqual(state.applied.map(o => [o.range.start.line, o.range.start.character, o.range.end.character, o.text]),
    [[1, 5, 10, "append"]]);
  const actions = state.provider.provideCodeActions(doc, null, { diagnostics: state.diags.get(doc.uri.toString()) });
  assert.equal(actions[0].title, "'apend' → 'append' 로 바꾸기");
  assert.equal(actions[0].isPreferred, true);
});

test("함수 인자 이름 오타 → 받을 수 있는 인자 안내 + 빠른 수정", async () => {
  const file = tmpPy("kwarg_typo.py", "def area(width, height=1):\n    return width * height\narea(3, heigth=2)\n");
  const { doc } = await openPy(file);
  const d = state.diags.get(doc.uri.toString())[0];
  assert.ok(d.message.includes("width, height"));
  const [action] = state.provider.provideCodeActions(doc, null, { diagnostics: [d] });
  const op = action.edit.ops[0];
  assert.deepEqual([op.range.start.line, op.range.start.character, op.text], [2, 8, "height"]);
});

test("설치되지 않은 패키지 → 지금 쓰는 python 으로 pip install", async () => {
  const file = tmpPy("missing_pkg.py", "import notarealpackage_xyz\n");
  state.choice = "pip install notarealpackage_xyz";
  await openPy(file);
  state.choice = null;
  await tick();
  const sent = state.terminals.at(-1).sent[0];
  assert.ok(sent.includes("-m pip install notarealpackage_xyz"), sent);
});

test("드래그한 줄만 시각화 → --lines 로 선택 영역 기록", async () => {
  const file = path.join(EXAMPLES, "basics.py");
  const doc = makeDoc(Uri.file(file), fs.readFileSync(file, "utf8"));
  const ed = makeEditor(doc);
  ed.selection = selection(16, 0, 20, 0);   // 17~20번 줄 드래그 (21번 줄 맨 앞에서 끝남)
  state.editors.push(ed); state.docs.push(doc);
  vscode.window.activeTextEditor = ed;
  await commands["pyrewind.runSelection"]();
  await tick();
  const t = traceFromHtml();
  assert.deepEqual(t.focus, { start: 17, end: 20 });
  assert.equal(t.steps.at(-1).event, "focus_end");
});

test("선택 없이 실행 → 커서가 있는 '# %%' 블록만 시각화", async () => {
  const file = tmpPy("percent.py", "x = 1\n# %%\ny = x + 1\nz = y * 2\n# %%\nw = 0\n");
  const doc = makeDoc(Uri.file(file), fs.readFileSync(file, "utf8"));
  const ed = makeEditor(doc);
  ed.selection = selection(3, 0, 3, 0);     // z = y * 2 줄에 커서
  state.editors.push(ed); state.docs.push(doc);
  vscode.window.activeTextEditor = ed;
  await commands["pyrewind.runSelection"]();
  await tick();
  const t = traceFromHtml();
  assert.deepEqual(t.focus, { start: 3, end: 4 });
  const globals = t.steps.at(-1).stack[0].vars.map(([n]) => n);
  assert.ok(globals.includes("z") && !globals.includes("w"));
});

test("노트북 셀 툴바 버튼 → 그 셀만 시각화", async () => {
  const nbPath = path.join(EXAMPLES, "notebook_demo.ipynb");
  const nbJson = JSON.parse(fs.readFileSync(nbPath, "utf8"));
  const nb = { uri: Uri.file(nbPath), isDirty: false, save: async () => true };
  const cells = nbJson.cells.map((c, i) => ({
    kind: c.cell_type === "code" ? 2 : 1, notebook: nb,
    document: makeDoc(new Uri(`${nbPath}#${i}`, "vscode-notebook-cell"), [].concat(c.source).join("")),
  }));
  nb.getCells = () => cells;
  state.notebooks.push(nb);
  const second = cells.filter(c => c.kind === 2)[1];
  await commands["pyrewind.runSelection"](second);
  await tick();
  const t = traceFromHtml();
  assert.deepEqual(t.focus, { start: t.cells[1].start, end: t.cells[1].end });
  assert.equal(t.error, null);
});

/* ------------------------------------------------------------ 실행 */
(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    reset();
    try { await fn(); console.log(`  ✓ ${name}`); }
    catch (e) { failed++; console.log(`  ✗ ${name}\n    ${e.stack.split("\n").slice(0, 4).join("\n    ")}`); }
  }
  fs.rmSync(tmpDir, { recursive: true, force: true });
  console.log(`\n${tests.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
