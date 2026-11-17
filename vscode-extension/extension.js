// PyRewind - VS Code 확장
// 파이썬 트레이서(pyrewind)를 실행해 기록을 받고,
// 웹뷰 패널에 뷰어를 띄운 뒤 현재 단계를 에디터 줄 강조로 동기화한다.
"use strict";
const vscode = require("vscode");
const cp = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

let ctx;
let output;
let panel = null;
let statusItem;
let current = null;   // { uri, fsPath, isNotebook, cells, total }
let lastStep = null;  // 웹뷰가 보낸 현재 단계 정보
let running = false;
let diagnostics;      // 오류 줄 빨간 밑줄 + 문제(Problems) 창
let lastFix = null;   // 빠른 수정용 { uri, line, wrong, options, kind, pip }
let lastPython = null; // 마지막으로 실행에 쓴 python (pip install 에 사용)
const deco = {};

function activate(context) {
  ctx = context;
  output = vscode.window.createOutputChannel("PyRewind");

  const line = (bg, border) => vscode.window.createTextEditorDecorationType({
    isWholeLine: true,
    backgroundColor: bg,
    borderColor: border,
    borderStyle: "solid",
    borderWidth: "0 0 0 3px",
    overviewRulerColor: border,
    overviewRulerLane: vscode.OverviewRulerLane.Full,
  });
  deco.next = line("rgba(242,192,55,0.20)", "#e0a800");
  deco.prev = line("rgba(76,194,126,0.16)", "#3fae6c");
  deco.err = line("rgba(255,90,80,0.22)", "#e5483c");
  deco.note = vscode.window.createTextEditorDecorationType({
    after: { margin: "0 0 0 2.5em", color: new vscode.ThemeColor("editorCodeLens.foreground"), fontStyle: "italic" },
  });

  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusItem.command = "pyrewind.reveal";

  // 단축키가 막혀도 클릭으로 실행할 수 있는 상태 표시줄 버튼
  const runItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 51);
  runItem.text = "$(play-circle) 코드 시각화";
  runItem.tooltip = "PyRewind: 현재 파일을 한 줄씩 실행하며 시각화 (Ctrl+Alt+V)";
  runItem.command = "pyrewind.run";
  const updateRunItem = () => {
    const ed = vscode.window.activeTextEditor;
    const show = !!vscode.window.activeNotebookEditor || (ed && /\.(py|ipynb)$/i.test(ed.document.uri.fsPath)) ||
      (ed && ed.document.languageId === "python");
    if (show) runItem.show(); else runItem.hide();
  };
  updateRunItem();
  context.subscriptions.push(runItem,
    vscode.window.onDidChangeActiveTextEditor(updateRunItem),
    vscode.window.onDidChangeActiveNotebookEditor(updateRunItem));
  output.appendLine("[pyrewind] PyRewind 활성화됨");

  diagnostics = vscode.languages.createDiagnosticCollection("pyrewind");

  context.subscriptions.push(
    output, statusItem, diagnostics, ...Object.values(deco),
    // 코드를 고치기 시작하면 그 파일의 오류 표시는 지운다 (다시 실행하면 새로 표시)
    vscode.workspace.onDidChangeTextDocument(e => { if (e.contentChanges.length) diagnostics.delete(e.document.uri); }),
    vscode.commands.registerCommand("pyrewind.run", uri => run(uri)),
    vscode.commands.registerCommand("pyrewind.runSelection", runSelection),
    vscode.commands.registerCommand("pyrewind.jumpToLine", jumpToEditorLine),
    vscode.commands.registerCommand("pyrewind.next", () => post({ type: "key", key: "next" })),
    vscode.commands.registerCommand("pyrewind.prev", () => post({ type: "key", key: "prev" })),
    vscode.commands.registerCommand("pyrewind.openInBrowser", () => openInBrowser(lastStep ? lastStep.step : 0)),
    vscode.commands.registerCommand("pyrewind.clear", () => panel && panel.dispose()),
    vscode.commands.registerCommand("pyrewind.reveal", () => panel && panel.reveal(undefined, true)),
    vscode.commands.registerCommand("pyrewind.pipInstall", pipInstall),
    vscode.languages.registerCodeActionsProvider({ language: "python" }, { provideCodeActions },
      { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }),
    vscode.window.onDidChangeVisibleTextEditors(applyDecorations),
    vscode.workspace.onDidSaveTextDocument(doc => onSaved(doc.uri)),
    vscode.workspace.onDidSaveNotebookDocument(nb => onSaved(nb.uri)),
  );
}

function deactivate() {}

/* ------------------------------------------------------------------ 실행 */

function samePath(a, b) {
  const n = p => path.normalize(p).toLowerCase();
  return process.platform === "win32" ? n(a) === n(b) : path.normalize(a) === path.normalize(b);
}

function onSaved(uri) {
  const cfg = vscode.workspace.getConfiguration("pyrewind");
  if (cfg.get("runOnSave") && current && panel && samePath(uri.fsPath, current.fsPath)) run(uri, true, current.focus);
}

/* ------------------------------------------------------- 선택 영역 실행 */

// Spyder / VS Code 처럼 '# %%' 로 나눈 블록: 커서가 있는 블록의 줄 범위(0부터) 또는 null
function percentCellRange(doc, line) {
  const isMark = i => /^\s*#\s*%%/.test(doc.lineAt(i).text);
  let hasMark = false;
  for (let i = 0; i < doc.lineCount && !hasMark; i++) hasMark = isMark(i);
  if (!hasMark) return null;
  let a = line;
  while (a > 0 && !isMark(a)) a--;
  if (isMark(a)) a++;
  let b = line + 1;
  while (b < doc.lineCount && !isMark(b)) b++;
  return a <= b - 1 ? [a, b - 1] : null;
}

// 선택한 줄 / '# %%' 블록 / 노트북 셀만 시각화.
// 노트북 셀 툴바에서 부르면 arg 로 그 셀(NotebookCell)이 넘어온다.
async function runSelection(arg) {
  let ed = vscode.window.activeTextEditor;
  let cellDoc = null;
  if (arg && arg.document && arg.notebook) {           // 셀 툴바 버튼
    cellDoc = arg.document;
    ed = vscode.window.visibleTextEditors.find(e => e.document === cellDoc) || null;
  } else if (ed && ed.document.uri.scheme === "vscode-notebook-cell") {
    cellDoc = ed.document;
  }
  if (!ed && !cellDoc) { vscode.window.showWarningMessage("시각화할 코드를 선택하거나 셀을 골라 주세요."); return; }

  const doc = cellDoc || ed.document;
  let a, b;
  const sel = ed && ed.document === doc ? ed.selection : null;
  if (sel && !sel.isEmpty) {
    a = sel.start.line;
    b = sel.end.line;
    if (sel.end.character === 0 && b > a) b--;        // 다음 줄 맨 앞까지 드래그한 경우
  } else if (cellDoc) {
    a = 0; b = doc.lineCount - 1;                       // 노트북: 셀 전체
  } else {
    const line = sel ? sel.active.line : 0;
    [a, b] = percentCellRange(doc, line) || [line, line];
  }

  const focus = { start: a + 1, end: b + 1 };
  if (cellDoc) {
    const nb = vscode.workspace.notebookDocuments.find(n => n.getCells().some(c => c.document === cellDoc));
    if (!nb) { vscode.window.showWarningMessage("노트북을 찾을 수 없습니다."); return; }
    const codeCells = nb.getCells().filter(c => c.kind === vscode.NotebookCellKind.Code);
    focus.cell = codeCells.findIndex(c => c.document === cellDoc) + 1;
    await run(nb.uri, false, focus, nb);
  } else {
    await run(doc.uri, false, focus, null, doc);
  }
}

function resolveTarget(arg) {
  if (arg instanceof vscode.Uri) return { uri: arg, isNotebook: arg.fsPath.toLowerCase().endsWith(".ipynb") };
  const ed = vscode.window.activeTextEditor;
  const nbEd = vscode.window.activeNotebookEditor;
  if (nbEd && (!ed || ed.document.uri.scheme === "vscode-notebook-cell")) return { uri: nbEd.notebook.uri, isNotebook: true, nb: nbEd.notebook };
  if (ed) return { uri: ed.document.uri, isNotebook: false, doc: ed.document };
  return null;
}

function packageRoot() {
  const bundled = path.join(ctx.extensionPath, "python");
  if (fs.existsSync(path.join(bundled, "pyrewind", "tracer.py"))) return bundled;
  const dev = path.resolve(ctx.extensionPath, "..");  // 저장소 안에서 개발 모드(F5)로 실행할 때
  if (fs.existsSync(path.join(dev, "pyrewind", "tracer.py"))) return dev;
  return null;
}

function workspaceVenvPython(uri) {
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  const dirs = [];
  if (folder) dirs.push(folder.uri.fsPath);
  dirs.push(path.dirname(uri.fsPath), path.dirname(path.dirname(uri.fsPath)));
  for (const d of dirs) {
    for (const venv of [".venv", "venv", "env"]) {
      const exe = process.platform === "win32" ? path.join(d, venv, "Scripts", "python.exe") : path.join(d, venv, "bin", "python");
      if (fs.existsSync(exe)) return exe;
    }
  }
  return null;
}

async function pythonPath(uri) {
  let configured = vscode.workspace.getConfiguration("pyrewind").get("pythonPath");
  if (configured) {
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (folder) configured = configured.replace(/\$\{workspaceFolder\}/g, folder.uri.fsPath);
    return configured;
  }
  try {
    const ext = vscode.extensions.getExtension("ms-python.python");
    if (ext) {
      const api = ext.isActive ? ext.exports : await ext.activate();
      if (api && api.environments) {
        const envPath = api.environments.getActiveEnvironmentPath(uri);
        const env = await api.environments.resolveEnvironment(envPath);
        const exe = env && env.executable && env.executable.uri && env.executable.uri.fsPath;
        if (exe) return exe;
      }
    }
  } catch (e) {
    output.appendLine(`[pyrewind] Python 확장에서 인터프리터를 가져오지 못함: ${e}`);
  }
  const venv = workspaceVenvPython(uri);
  if (venv) return venv;
  return process.platform === "win32" ? "python" : "python3";
}

async function askInputs(fsPath, text) {
  if (!/\binput\s*\(/.test(text)) return null;
  const key = "inputs:" + fsPath;
  const prev = ctx.workspaceState.get(key, "");
  const val = await vscode.window.showInputBox({
    title: "PyRewind - input() 값",
    prompt: "코드에 input() 이 있어요. 차례로 넣을 값을 | 로 구분해 입력하세요 (예: 지수 | 20)",
    value: prev,
    ignoreFocusOut: true,
  });
  if (val === undefined) return undefined; // 취소
  await ctx.workspaceState.update(key, val);
  return val.split("|").map(s => s.trim());
}

async function run(arg, silent, focus, nbDoc, textDoc) {
  if (running) { vscode.window.showInformationMessage("PyRewind: 이미 실행 중입니다."); return; }
  const target = resolveTarget(arg);
  if (target && nbDoc) target.nb = nbDoc;
  if (target && textDoc) target.doc = textDoc;
  if (!target) { vscode.window.showWarningMessage("시각화할 .py 또는 .ipynb 파일을 먼저 열어 주세요."); return; }
  const fsPath = target.uri.fsPath;
  if (!/\.(py|ipynb)$/i.test(fsPath)) { vscode.window.showWarningMessage("PyRewind 는 .py / .ipynb 파일만 지원합니다."); return; }

  // 저장되지 않은 변경이 있으면 저장
  let text = "";
  try {
    if (target.doc) { if (target.doc.isDirty) await target.doc.save(); text = target.doc.getText(); }
    else if (target.nb) { if (target.nb.isDirty) await target.nb.save(); text = target.nb.getCells().map(c => c.document.getText()).join("\n"); }
    else text = fs.readFileSync(fsPath, "utf8");
  } catch (e) { text = ""; }

  const root = packageRoot();
  if (!root) { vscode.window.showErrorMessage("pyrewind 파이썬 패키지를 찾을 수 없습니다. 확장을 다시 설치해 주세요."); return; }

  const inputs = await askInputs(fsPath, text);
  if (inputs === undefined) return;

  const py = await pythonPath(target.uri);
  lastPython = py;
  const cfg = vscode.workspace.getConfiguration("pyrewind");
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pyrewind-"));
  const outJson = path.join(tmpDir, "trace.json");
  const args = ["-m", "pyrewind", fsPath, "--json", "--no-open", "-q",
    "-o", outJson, "--max-steps", String(cfg.get("maxSteps") || 3000)];
  if (cfg.get("loopLimit") > 0) args.push("--loop-limit", String(cfg.get("loopLimit")));
  if (focus) {
    args.push("--lines", `${focus.start}-${focus.end}`);
    if (focus.cell) args.push("--cell", String(focus.cell));
  }
  if (inputs) {
    const stdinFile = path.join(tmpDir, "stdin.txt");
    fs.writeFileSync(stdinFile, inputs.join("\n"), "utf8");
    args.push("--stdin", stdinFile);
  }
  const env = Object.assign({}, process.env, {
    PYTHONPATH: root + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : ""),
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  });

  running = true;
  output.appendLine(`\n[pyrewind] ${py} ${args.join(" ")}`);
  try {
    const result = await vscode.window.withProgress(
      { location: silent ? vscode.ProgressLocation.Window : vscode.ProgressLocation.Notification,
        title: `PyRewind: ${path.basename(fsPath)} 실행 중…`, cancellable: true },
      (_p, token) => spawnTracer(py, args, env, path.dirname(fsPath), token, (cfg.get("timeoutSeconds") || 60) * 1000));
    if (result.error) {
      if (result.error.code === "ENOENT") pythonMissing(py);
      else vscode.window.showErrorMessage(`PyRewind 실행 실패: ${result.error.message}`);
      return;
    }
    if (result.cancelled) { vscode.window.showWarningMessage("PyRewind: 실행을 중단했습니다."); return; }
    if (!fs.existsSync(outJson)) {
      // Windows 의 'python' 앱 실행 별칭(가짜 python)은 설치 안내만 출력하고 9009 로 끝난다
      if (result.code === 9009 || /Python was not found|Microsoft Store/i.test(result.stderr || "")) { pythonMissing(py); return; }
      if (/SyntaxError|requires Python|No module named ['"]?pyrewind/i.test(result.stderr || "")) {
        output.show(true);
        vscode.window.showErrorMessage(`PyRewind 는 Python 3.10 이상이 필요합니다 (${py}). 더 새 버전의 Python 을 선택해 주세요.`);
        return;
      }
      output.show(true);
      vscode.window.showErrorMessage("PyRewind: 기록을 만들지 못했습니다. 출력 창의 로그를 확인하세요.");
      return;
    }
    const trace = JSON.parse(fs.readFileSync(outJson, "utf8"));
    showTrace(trace, target, root);
    if (current) current.focus = focus || null;
  } finally {
    running = false;
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (e) { /* 무시 */ }
  }
}

function pythonMissing(py) {
  vscode.window.showErrorMessage(
    `Python 을 찾을 수 없습니다 (${py}). PyRewind 를 쓰려면 Python 3.10 이상이 설치되어 있어야 합니다.`,
    "Python 설치 페이지", "설정 열기")
    .then(c => {
      if (c === "Python 설치 페이지") vscode.env.openExternal(vscode.Uri.parse("https://www.python.org/downloads/"));
      else if (c === "설정 열기") vscode.commands.executeCommand("workbench.action.openSettings", "pyrewind.pythonPath");
    });
}

function spawnTracer(py, args, env, cwd, token, timeoutMs) {
  return new Promise(resolve => {
    let done = false;
    const finish = r => { if (!done) { done = true; clearTimeout(timer); resolve(r); } };
    let child;
    try {
      child = cp.spawn(py, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    } catch (e) { finish({ error: e }); return; }
    let stderr = "";
    child.stdout.on("data", d => output.append(d.toString()));
    child.stderr.on("data", d => { stderr += d.toString(); output.append(d.toString()); });
    child.on("error", e => finish({ error: e }));
    child.on("close", code => finish({ code, stderr }));
    const timer = setTimeout(() => {
      output.appendLine(`[pyrewind] 제한 시간(${timeoutMs / 1000}초) 초과로 중단`);
      child.kill(); finish({ cancelled: true });
    }, timeoutMs);
    token.onCancellationRequested(() => { child.kill(); finish({ cancelled: true }); });
  });
}

/* ------------------------------------------------------------------ 웹뷰 */

function showTrace(trace, target, root) {
  const viewer = fs.readFileSync(path.join(root, "pyrewind", "viewer.html"), "utf8");
  const data = JSON.stringify(trace).replace(/<\//g, "<\\/");
  const html = viewer.replace("/*__TRACE_DATA__*/null", () => data);

  const sameFile = current && samePath(current.fsPath, target.uri.fsPath);
  current = { uri: target.uri, fsPath: target.uri.fsPath, isNotebook: target.isNotebook, cells: trace.cells || [], total: trace.steps.length };
  if (!sameFile) lastStep = null;

  if (!panel) {
    panel = vscode.window.createWebviewPanel("pyrewind", "", { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true });
    panel.iconPath = new vscode.ThemeIcon("play-circle");
    panel.webview.onDidReceiveMessage(onMessage);
    panel.onDidDispose(() => {
      panel = null; current = null; lastStep = null;
      clearDecorations();
      diagnostics.clear();
      statusItem.hide();
      vscode.commands.executeCommand("setContext", "pyrewind.active", false);
    });
  } else {
    panel.reveal(undefined, true);
  }
  panel.title = `▶ ${path.basename(target.uri.fsPath)}`;
  panel.webview.html = html;
  current.html = html;
  vscode.commands.executeCommand("setContext", "pyrewind.active", true);

  diagnostics.clear();
  lastFix = null;
  if (trace.error) reportError(trace.error);
  if (trace.truncated) {
    vscode.window.showWarningMessage(`최대 ${trace.max_steps}단계까지만 기록했습니다. 무한 루프인지 확인해 보세요.`);
  }
}

function reportError(err) {
  const h = err.hint || { title: `${err.type} 오류`, why: "", fix: "" };
  const plain = s => (s || "").replace(/\*\*/g, "");
  const where = err.line ? ` (${err.line}번 줄)` : "";
  const sg = err.suggest;
  const best = sg && sg.options && sg.options.length ? sg.options[0] : null;
  const target = err.line ? docLineFor(err.line) : null;
  lastFix = null;
  if (target) {
    const range = new vscode.Range(target.line, 0, target.line, 10000);
    let text = `${h.title} — ${err.type}: ${err.msg}\n왜? ${plain(h.why)}\n해결: ${plain(h.fix)}`;
    if (best) text += `\n💡 추천: '${sg.wrong}' 대신 '${sg.options.join("' 또는 '")}' (전구 아이콘으로 바로 고치기)`;
    if (sg && sg.kind === "module" && !best) text += `\n📦 설치: pip install ${sg.pip}`;
    if (sg && sg.kind === "kwarg" && sg.params && sg.params.length) text += `\n${sg.owner}() 가 받는 인자: ${sg.params.join(", ")}`;
    const d = new vscode.Diagnostic(range, text, vscode.DiagnosticSeverity.Error);
    d.source = "PyRewind";
    diagnostics.set(target.uri, [d]);
    if (sg) lastFix = { uri: target.uri.toString(), line: target.line, wrong: sg.wrong, options: sg.options || [], kind: sg.kind, pip: sg.pip };
  }
  const buttons = ["오류 난 순간 보기"];
  if (best && target) buttons.push(`'${best}'(으)로 고치기`);
  if (sg && sg.kind === "module" && !best) buttons.push(`pip install ${sg.pip}`);
  buttons.push("해결 방법");
  const head = best ? ` 👉 혹시 '${best}'?` : "";
  vscode.window.showErrorMessage(`❌ ${h.title}${where} — ${err.type}: ${err.msg}${head}`, ...buttons)
    .then(choice => {
      if (!choice) return;
      if (choice === "오류 난 순간 보기") { post({ type: "goError" }); if (panel) panel.reveal(undefined, false); }
      else if (choice === "해결 방법") vscode.window.showInformationMessage(`왜? ${plain(h.why)}\n\n해결: ${plain(h.fix)}`, { modal: true });
      else if (choice.startsWith("pip install")) pipInstall(sg.pip);
      else if (best) applyRename(best);
    });
}

/* ---------------------------------------------------------- 빠른 수정 */

function isIdent(ch) { return !!ch && /[\w가-힣]/.test(ch); }

// 줄에서 틀린 이름이 '단어로' 나온 위치 찾기 (예: groupbyy 는 찾고 groupbyyy 안의 일부는 무시)
function findWord(text, word) {
  let i = text.indexOf(word);
  while (i >= 0) {
    if (!isIdent(text[i - 1]) && !isIdent(text[i + word.length])) return i;
    i = text.indexOf(word, i + 1);
  }
  return -1;
}

function renameEdit(document, replacement) {
  if (!lastFix || lastFix.line >= document.lineCount) return null;
  const text = document.lineAt(lastFix.line).text;
  const col = findWord(text, lastFix.wrong);
  if (col < 0) return null;
  const edit = new vscode.WorkspaceEdit();
  edit.replace(document.uri, new vscode.Range(lastFix.line, col, lastFix.line, col + lastFix.wrong.length), replacement);
  return edit;
}

async function applyRename(replacement) {
  if (!lastFix) return;
  const doc = vscode.workspace.textDocuments.find(d => d.uri.toString() === lastFix.uri);
  const edit = doc && renameEdit(doc, replacement);
  if (!edit) { vscode.window.showWarningMessage("고칠 위치를 찾지 못했어요. 코드가 바뀌었다면 다시 실행해 주세요."); return; }
  await vscode.workspace.applyEdit(edit);
  vscode.window.setStatusBarMessage(`$(check) '${lastFix.wrong}' → '${replacement}' 로 고쳤어요. 다시 실행해 보세요 (Ctrl+Alt+V)`, 6000);
}

function provideCodeActions(document, _range, context) {
  if (!lastFix || document.uri.toString() !== lastFix.uri) return [];
  const diag = context.diagnostics.find(d => d.source === "PyRewind");
  if (!diag) return [];
  const actions = [];
  lastFix.options.forEach((opt, i) => {
    const edit = renameEdit(document, opt);
    if (!edit) return;
    const a = new vscode.CodeAction(`'${lastFix.wrong}' → '${opt}' 로 바꾸기`, vscode.CodeActionKind.QuickFix);
    a.edit = edit;
    a.diagnostics = [diag];
    a.isPreferred = i === 0;
    actions.push(a);
  });
  if (lastFix.kind === "module" && !lastFix.options.length) {
    const a = new vscode.CodeAction(`터미널에서 pip install ${lastFix.pip} 실행`, vscode.CodeActionKind.QuickFix);
    a.command = { command: "pyrewind.pipInstall", title: "pip install", arguments: [lastFix.pip] };
    a.diagnostics = [diag];
    actions.push(a);
  }
  return actions;
}

function pipInstall(pkg) {
  if (!pkg) return;
  const py = lastPython || (process.platform === "win32" ? "python" : "python3");
  const term = vscode.window.createTerminal("PyRewind - pip");
  term.show();
  const quoted = /\s/.test(py) ? `& "${py}"` : py;
  term.sendText(`${quoted} -m pip install ${pkg}`);
}

function post(msg) {
  if (panel) panel.webview.postMessage(msg);
}

function onMessage(m) {
  if (m && m.type === "openBrowser") { openInBrowser(m.step); return; }
  if (!m || m.type !== "step") return;
  lastStep = m;
  statusItem.text = `$(debug-step-over) VCR ${m.step + 1}/${m.total}`;
  statusItem.tooltip = "PyRewind 패널 보기";
  statusItem.show();
  applyDecorations(true);
}

// 지금 보고 있는 기록을 HTML 파일로 저장해 기본 브라우저에서 연다 (같은 단계에서 시작)
async function openInBrowser(step) {
  if (!current || !current.html) { vscode.window.showWarningMessage("먼저 PyRewind 로 코드를 실행해 주세요."); return null; }
  const dir = path.join(os.tmpdir(), "pyrewind-browser");
  fs.mkdirSync(dir, { recursive: true });
  const name = path.basename(current.fsPath).replace(/[^\w.\-가-힣]/g, "_");
  const file = path.join(dir, `${name}.html`);
  const n = Number.isInteger(step) ? step : 0;
  // file:// 주소의 #step=N 은 OS 가 떼어 버릴 수 있어서, 시작 단계를 HTML 안에 직접 넣는다
  const html = current.html.replace("<head>", `<head><script>if(!location.hash)location.hash="step=${n}"</script>`);
  fs.writeFileSync(file, html, "utf8");
  openFile(file);
  return file;
}

// 파일을 기본 프로그램(브라우저)으로 연다.
// Windows 에서 vscode.env.openExternal(Uri.file(...)) 은 한글 경로를 %EB%A7%A4… 로 바꿔 버려
// "지정된 파일을 찾을 수 없습니다 (0x2)" 오류가 나므로 탐색기(explorer.exe)에 경로를 그대로 넘긴다.
function openFile(file) {
  if (process.platform === "win32" && !process.env.PYREWIND_TEST) {
    try {
      cp.spawn("explorer.exe", [file], { detached: true, stdio: "ignore", windowsHide: false }).unref();
      return;
    } catch (e) { /* 아래 방법으로 */ }
  }
  vscode.env.openExternal(vscode.Uri.file(file));
}

/* ------------------------------------------------------------- 에디터 동기화 */

// trace 의 줄 번호(1부터) → { 문서 uri, 0부터 시작하는 줄 }   (.ipynb 는 해당 셀 문서)
function docLineFor(traceLine) {
  if (!current || !traceLine) return null;
  if (!current.isNotebook) return { uri: current.uri, line: traceLine - 1 };
  const cell = current.cells.find(c => traceLine >= c.start && traceLine <= c.end);
  if (!cell) return null;
  const nb = vscode.workspace.notebookDocuments.find(n => samePath(n.uri.fsPath, current.fsPath));
  if (!nb) return null;
  const codeCells = nb.getCells().filter(c => c.kind === vscode.NotebookCellKind.Code);
  const nc = codeCells[cell.n - 1];
  if (!nc) return null;
  return { uri: nc.document.uri, line: traceLine - cell.start };
}

// trace 의 줄 번호 → { editor 찾는 함수, 0부터 시작하는 줄 }
function locate(traceLine) {
  const t = docLineFor(traceLine);
  if (!t) return null;
  if (!current.isNotebook) {
    return { match: doc => doc.uri.scheme === "file" && samePath(doc.uri.fsPath, current.fsPath), line: t.line };
  }
  const key = t.uri.toString();
  return { match: doc => doc.uri.toString() === key, line: t.line };
}

function clearDecorations() {
  for (const ed of vscode.window.visibleTextEditors) for (const d of Object.values(deco)) ed.setDecorations(d, []);
}

function applyDecorations(reveal) {
  const editors = vscode.window.visibleTextEditors;
  const buckets = new Map(); // editor -> {next,prev,err,note}
  const bucket = ed => { if (!buckets.has(ed)) buckets.set(ed, { next: [], prev: [], err: [], note: [] }); return buckets.get(ed); };
  for (const ed of editors) bucket(ed);
  if (!current || !lastStep) { clearDecorations(); return; }

  const place = (kind, traceLine, opts) => {
    const loc = locate(traceLine);
    if (!loc) return null;
    for (const ed of editors) {
      if (!loc.match(ed.document) || loc.line < 0 || loc.line >= ed.document.lineCount) continue;
      const r = ed.document.lineAt(loc.line).range;
      bucket(ed)[kind].push(opts ? Object.assign({ range: r }, opts) : r);
      if (reveal && kind === "next") ed.revealRange(r, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
    }
    return loc;
  };

  const s = lastStep;
  if (s.err) place("err", s.err);
  else {
    if (s.prev && s.prev !== s.next) place("prev", s.prev);
    if (s.next) place("next", s.next);
  }
  const inline = vscode.workspace.getConfiguration("pyrewind").get("inlineValues");
  const noteLine = s.err || s.prev || s.next;
  if (inline && noteLine && s.notes && s.notes.length) {
    place("note", noteLine, { renderOptions: { after: { contentText: "◀ " + s.notes.join("   ·   ") } } });
  }

  for (const [ed, b] of buckets) {
    ed.setDecorations(deco.next, b.next);
    ed.setDecorations(deco.prev, b.prev);
    ed.setDecorations(deco.err, b.err);
    ed.setDecorations(deco.note, b.note);
  }
}

// 에디터 커서가 있는 줄 → 그 줄이 다음에 실행되는 시점으로 이동
function jumpToEditorLine() {
  const ed = vscode.window.activeTextEditor;
  if (!ed || !panel || !current) return;
  const line0 = ed.selection.active.line;
  let traceLine = null;
  if (!current.isNotebook) {
    if (samePath(ed.document.uri.fsPath, current.fsPath)) traceLine = line0 + 1;
  } else {
    const nb = vscode.workspace.notebookDocuments.find(n => samePath(n.uri.fsPath, current.fsPath));
    if (nb) {
      const codeCells = nb.getCells().filter(c => c.kind === vscode.NotebookCellKind.Code);
      const k = codeCells.findIndex(c => c.document.uri.toString() === ed.document.uri.toString());
      const meta = current.cells[k];
      if (meta) traceLine = meta.start + line0;
    }
  }
  if (!traceLine) { vscode.window.showInformationMessage("시각화 중인 파일의 줄에서 실행해 주세요."); return; }
  post({ type: "jumpToLine", line: traceLine });
  panel.reveal(undefined, true);
}

module.exports = { activate, deactivate };
