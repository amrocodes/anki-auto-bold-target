# Auto-bold target (JPMN/Migaku) — v2 with Config
# Put this file in: addons21/auto_bold_target/__init__.py

from aqt import mw, gui_hooks
from aqt.qt import QAction, QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox, QMenu
from aqt.utils import tooltip
import json

# ---- JS bridge handler (compatible across Anki versions) -------------------
def _handle_cmd(*args):
    """
    Newer Anki: (handled: bool, message: str, context) -> (handled, reply)
    Older Anki: (message: str, context) -> bool
    """
    if len(args) == 3:
        handled, message, context = args
        if message == "auto_bold_target__report":
            _report_result()
            return (True, None)
        return (handled, None)
    elif len(args) >= 1:
        message = args[0]
        if message == "auto_bold_target__report":
            _report_result()
            return True
        return False

# register (avoid duplicate registration on reload)
try:
    gui_hooks.webview_did_receive_js_message.remove(_handle_cmd)
except Exception:
    pass
gui_hooks.webview_did_receive_js_message.append(_handle_cmd)

# -------- Config helpers ----------------------------------------------------
DEFAULTS = {
    "note_types": ["JP Mining Note"],
    "headword_fields": ["Word", "Key"],
    "reading_fields": ["WordReading"],
    "sentence_selectors": [
        ".full-sentence",
        ".jpsentence",
        ".sentence",
        ".sentence-block",
        ".expression--sentence",
    ],
    "convert_t_tag": True,
    "regex_enable": True,
    "kana_bridge_len": 12,
    "first_match_only": True,
    "defer_ms": 60,
    "observe_ms": 3000,
    "extra_css": ".full-sentence b.auto-bold { color:#ffe37a; font-weight:800; }",
}

def _cfg():
    # Read current config, merge with defaults to be robust to missing keys
    raw = mw.addonManager.getConfig(__name__) or {}
    cfg = dict(DEFAULTS)
    for k, v in raw.items():
        cfg[k] = v
    return cfg

# -------- Utility -----------------------------------------------------------
def _get_first(note, names):
    for nm in names:
        try:
            val = note[nm].strip()
        except KeyError:
            continue
        if val:
            return val
    return ""

def _should_run(card, cfg):
    nts = cfg.get("note_types") or []
    if not nts:
        return True
    try:
        return card.note().model()["name"] in nts
    except Exception:
        return True

# -------- Core JS builder ---------------------------------------------------
def _js_builder(headword, reading, cfg):
    # JSON-serialize values to embed safely in JS
    H = json.dumps(headword or "")
    R = json.dumps(reading or "")
    SELECTORS = json.dumps(cfg["sentence_selectors"])
    CONVERT_T = "true" if cfg["convert_t_tag"] else "false"
    REGEX_EN  = "true" if cfg["regex_enable"] else "false"
    BRIDGE    = int(cfg["kana_bridge_len"])
    FIRST_ONE = "true" if cfg["first_match_only"] else "false"
    OBS_MS    = int(cfg["observe_ms"])

    return r"""
(function(){
  // Config-injected values
  var SELECTORS   = %s;
  var CONVERT_T   = %s;
  var REGEX_EN    = %s;
  var BRIDGE_LEN  = %d;   // e.g., 12
  var FIRST_MATCH = %s;   // stop after first wrap?
  var OBS_MS      = %d;   // observer lifetime in ms

  // Find sentence container from selector list; else fall back to the whole card.
  function pickSentenceEl(){
    for (var i=0;i<SELECTORS.length;i++){
      var el = document.querySelector(SELECTORS[i]);
      if (el) return el;
    }
    return null;
  }
  var sentenceEl = pickSentenceEl();
  var root = sentenceEl || document.querySelector('#qa') || document.body;

  function convertTTags(scope){
    if (!CONVERT_T) return 0;
    var tEls = scope.querySelectorAll('t');
    if (!tEls.length) return 0;
    var list = Array.prototype.slice.call(tEls);
    var converted = 0;
    for (var i=0;i<list.length;i++){
      var t = list[i];
      var b = document.createElement('b');
      b.className = 'auto-bold';
      while (t.firstChild) b.appendChild(t.firstChild);
      t.parentNode.replaceChild(b, t);
      converted++;
    }
    return converted;
  }

  function attemptBold(){
    if (!root) return "no-sentence";

    // Step 0: convert <t>…</t> → <b class="auto-bold">…</b>
    var converted = convertTTags(root);
    if (converted > 0) return "applied:t";

    // If we do have an explicit sentence container and it already has bold, we consider it done.
    if (sentenceEl && sentenceEl.querySelector('b')) return "already-bolded";

    if (!REGEX_EN) return "no-match";

    var target  = %s;   // Word/Key
    var reading = %s;   // WordReading

    // Helpers / character classes
    var KANA = "[\\u3040-\\u309F\\u30A0-\\u30FFー]";
    var JAP  = "[\\u3040-\\u30FF\\u4E00-\\u9FFF々ー]"; // kana+kanji+ー
    function esc(s){ return (s||"").replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&"); }
    function onlyKana(s){ return (s||"").replace(/[^ぁ-ゖァ-ヺー]/g,""); }
    function toHira(s){ return (s||"").replace(/[\\u30A1-\\u30FA]/g, c=>String.fromCharCode(c.charCodeAt(0)-0x60)); }
    function toKata(s){ return (s||"").replace(/[\\u3041-\\u3096]/g, c=>String.fromCharCode(c.charCodeAt(0)+0x60)); }
    function kanjiCore(s){ return (s||"").replace(/[^\\u4E00-\\u9FFF々]/g,""); }
    var hasKanji = /[\\u4E00-\\u9FFF々]/.test(target || "");

    // Build patterns to try (first hit wins)
    var patterns = [];

    // A) Flexible kanji bridge: each kanji then up to BRIDGE_LEN Japanese chars
    if (target && hasKanji){
      var ks = (target.match(/[\\u4E00-\\u9FFF々]/g) || []);
      if (ks.length){
        var core = ks.map(function(k){ return esc(k) + JAP + "{0,"+BRIDGE_LEN+"}"; }).join("");
        patterns.push(new RegExp(core, "g"));
      }
    }

    // B) Reading fallback (kana) + trailing kana
    if (reading){
      var rd = onlyKana(reading);
      if (rd){
        patterns.push(new RegExp(esc(toHira(rd)) + KANA + "*", "g"));
        patterns.push(new RegExp(esc(toKata(rd)) + KANA + "*", "g"));
      }
    }

    // C) Kana headword + trailing kana
    if (target && !hasKanji && /[\\u3040-\\u30FF]/.test(target)){
      patterns.push(new RegExp(esc(toHira(target)) + KANA + "*", "g"));
      patterns.push(new RegExp(esc(toKata(target)) + KANA + "*", "g"));
    }

    // D) Literal kanji core, then E) literal target
    if (target){
      var kc = kanjiCore(target);
      if (kc) patterns.push(new RegExp(esc(kc), "g"));
      patterns.push(new RegExp(esc(target), "g"));
    }

    if (!patterns.length) return "no-match";

    function forEachTextNode(node, cb){
      if (node.nodeType === 1) {
        if (node.tagName && node.tagName.toLowerCase() === "rt") return; // skip furigana
        for (var i=0; i<node.childNodes.length; i++) forEachTextNode(node.childNodes[i], cb);
      } else if (node.nodeType === 3) {
        cb(node);
      }
    }

    function wrapInNode(textNode, start, end){
      var text = textNode.nodeValue;
      var before = document.createTextNode(text.slice(0, start));
      var mid    = document.createElement("b");
      mid.className = "auto-bold";
      mid.textContent = text.slice(start, end);
      var after  = document.createTextNode(text.slice(end));
      var parent = textNode.parentNode;
      parent.replaceChild(after, textNode);
      parent.insertBefore(mid, after);
      parent.insertBefore(before, mid);
    }

    var applied = false;
    outer:
    for (var p = 0; p < patterns.length; p++) {
      var re = patterns[p];
      forEachTextNode(root, function(tn){
        if (applied) return;
        var txt = tn.nodeValue;
        var m; re.lastIndex = 0;
        while ((m = re.exec(txt)) !== null) {
          wrapInNode(tn, m.index, m.index + m[0].length);
          applied = true;
          if (FIRST_MATCH) break;
        }
      });
      if (applied && FIRST_MATCH) break outer;
    }

    return applied ? "applied:regex" : "no-match";
  }

  var result = attemptBold();
  if (result === "no-match"){
    var obs = new MutationObserver(function(){
      var r = attemptBold();
      if (r !== "no-match"){ obs.disconnect(); }
    });
    obs.observe(root, {childList:true, subtree:true});
    setTimeout(function(){ try{ obs.disconnect(); }catch(e){} }, OBS_MS);
  }
})();
""" % (SELECTORS, CONVERT_T, REGEX_EN, BRIDGE, FIRST_ONE, OBS_MS, H, R)

# -------- CSS injection -----------------------------------------------------
def _inject_css_once(cfg):
    css = (cfg.get("extra_css") or "").strip()
    if not css:
        return
    js = "var s=document.getElementById('autoBoldCSS'); if(!s){s=document.createElement('style'); s.id='autoBoldCSS'; s.textContent=%s; document.documentElement.appendChild(s);}" % json.dumps(css)
    mw.reviewer.web.eval(js)

# -------- Runner & reporter -------------------------------------------------
def _run(card, report=False):
    cfg = _cfg()
    if not _should_run(card, cfg):
        return
    note = card.note()
    head = _get_first(note, cfg["headword_fields"])
    reading = _get_first(note, cfg["reading_fields"])
    js = _js_builder(head, reading, cfg)
    delay = int(cfg.get("defer_ms", 60))
    # Inject JS, then (optionally) report a bit later
    mw.reviewer.web.eval(f"(function(){{setTimeout(function(){{{js}}}, {delay});}})();")
    if report:
        # give the injected JS time to apply before checking
        mw.reviewer.web.eval(
            f"(function(){{setTimeout(function(){{"
            f"pycmd('auto_bold_target__report');"
            f"}}, {delay + 150});}})();"
        )

def _report_result():
    # Summarize what happened on the visible side
    check_js = """
      (function(){
        function pickSentenceEl(){
          var sels = %s;
          for (var i=0;i<sels.length;i++){
            var el = document.querySelector(sels[i]);
            if (el) return el;
          }
          return null;
        }
        var sentenceEl = pickSentenceEl();
        var root = sentenceEl || document.querySelector('#qa') || document.body;
        if (!root) return 'no-sentence';
        var tCount = root.querySelectorAll('t').length;
        var boldCount = root.querySelectorAll('b.auto-bold').length;
        if (boldCount) return 'applied (t='+tCount+', b='+boldCount+')';
        if (sentenceEl && sentenceEl.querySelector('b')) return 'already-bolded';
        return 'no-match (t='+tCount+', b='+boldCount+')';
      })();
    """ % json.dumps(_cfg()["sentence_selectors"])
    mw.reviewer.web.evalWithCallback(check_js, lambda res: tooltip(f"Auto-bold: {res}", period=2500))

# -------- Hooks -------------------------------------------------------------
def on_q(card):
    _inject_css_once(_cfg())
    _run(card)

def on_a(card):
    _run(card)

# avoid double-adding hooks on reload:
try:
    gui_hooks.reviewer_did_show_question.remove(on_q)
except Exception:
    pass
try:
    gui_hooks.reviewer_did_show_answer.remove(on_a)
except Exception:
    pass
gui_hooks.reviewer_did_show_question.append(on_q)
gui_hooks.reviewer_did_show_answer.append(on_a)

# -------- Menu actions ------------------------------------------------------
def action_run_now():
    c = mw.reviewer.card
    if c:
        _inject_css_once(_cfg())
        _run(c, report=True)

def action_config():
    # Try modern Anki API
    try:
        fn = getattr(mw.addonManager, "editConfig")
        return fn(__name__)
    except Exception:
        pass

    # Try older API name
    try:
        fn = getattr(mw.addonManager, "showConfigDialog")
        return fn(__name__)
    except Exception:
        pass

    # Fallback: minimal JSON editor (works everywhere)
    current = mw.addonManager.getConfig(__name__) or {}
    try:
        initial = json.dumps(current if current else DEFAULTS, ensure_ascii=False, indent=2)
    except Exception:
        initial = "{}"

    dlg = QDialog(mw)
    dlg.setWindowTitle("Auto-bold: Configure (fallback)")
    layout = QVBoxLayout(dlg)

    edit = QTextEdit(dlg)
    edit.setPlainText(initial)
    layout.addWidget(edit)

    # PyQt6/Qt6 uses StandardButton; PyQt5 uses top-level attrs.
    try:
        Std = QDialogButtonBox.StandardButton
        buttons = QDialogButtonBox(Std.Save | Std.Cancel, parent=dlg)
    except AttributeError:
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=dlg)

    layout.addWidget(buttons)

    def on_save():
        try:
            data = json.loads(edit.toPlainText())
            if not isinstance(data, dict):
                raise ValueError("Config must be a JSON object")
            mw.addonManager.writeConfig(__name__, data)
            tooltip("Auto-bold: config saved", period=2000)
            dlg.accept()
        except Exception as e:
            tooltip(f"Save failed: {e}", period=2500)

    buttons.accepted.connect(on_save)
    buttons.rejected.connect(dlg.reject)
    dlg.exec()


# helper to avoid duplicate menu entries on reload
# -------- Top-level "Auto-bold" menu ---------------------------------------
_menu_ref = None

def _get_or_create_menu() -> QMenu:
    """Create/return a top-level 'Auto-bold' menu. Fallback to Tools if needed."""
    global _menu_ref
    if _menu_ref and not _menu_ref.isHidden():
        return _menu_ref

    bar = getattr(mw.form, "menubar", None)
    if not bar:
        # some builds expose QMainWindow.menuBar()
        try:
            bar = mw.menuBar()
        except Exception:
            bar = None

    if not bar:
        # last resort: Tools menu (shouldn’t happen on desktop)
        return mw.form.menuTools

    # Reuse if already created (e.g., on live reload)
    for m in bar.findChildren(QMenu):
        if m.objectName() == "menuAutoBold":
            _menu_ref = m
            return m

    _menu_ref = bar.addMenu("Auto-bold")
    _menu_ref.setObjectName("menuAutoBold")
    return _menu_ref

def _add_menu_action_to(menu: QMenu, action: QAction):
    """Avoid duplicate actions by stable objectName."""
    action.setObjectName("AutoBold|" + action.text())
    for a in menu.actions():
        if a.objectName() == action.objectName():
            return
    menu.addAction(action)

def _remove_from_tools(texts):
    """Clean up any old entries from Tools menu if they exist."""
    tools = getattr(mw.form, "menuTools", None)
    if not tools:
        return
    for a in list(tools.actions()):
        if a.text() in texts:
            tools.removeAction(a)

# Build actions
act_run = QAction("Auto-bold now (report)", mw)
act_run.triggered.connect(action_run_now)

act_cfg = QAction("Configure Auto-bold…", mw)
act_cfg.triggered.connect(action_config)

# Remove old Tools entries (if you had them previously)
_remove_from_tools(["Auto-bold now (report)", "Configure Auto-bold…"])

# Add to the top-level "Auto-bold" menu
auto_menu = _get_or_create_menu()
_add_menu_action_to(auto_menu, act_run)
_add_menu_action_to(auto_menu, act_cfg)
