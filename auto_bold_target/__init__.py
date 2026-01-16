# Auto-bold target (JPMN/Migaku) — v2.2.1
# Place in: Anki2/addons21/auto_bold_target/__init__.py
#
# Highlights the target word (incl. conjugations) on the *reviewed card only*,
# then optionally persists the highlighted sentence HTML back to a field so it
# syncs to mobile. Works with JPMN/Migaku templates, <t>…</t> tags, furigana.
#
# Key config (via Add-ons > auto_bold_target > Config):
#   "note_types": ["JP Mining Note"]         # limit to these models
#   "headword_fields": ["Word","Key"]         # first non-empty wins
#   "reading_fields": ["WordReading"]         # optional reading (kana)
#   "persist_to_field": true                  # write back to a field
#   "persist_field_name": "sentence"          # which field to overwrite
#   "persist_once": true                      # skip if already persisted
#   "strict_scope": true                      # only inside sentence container
#   "fallback_to_root": false                 # if no sentence match, skip
#   "extra_css": ".full-sentence b.auto-bold { color:#ffe37a; font-weight:800; }"
#
# Changelog:
# 2.2.1: Fix webview hook signature to (handled, message, context);
#        keep UI responsive; maintain persistence behavior.

from aqt import mw, gui_hooks
from aqt.qt import QAction, QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox
from aqt.utils import tooltip
import json, base64

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

    # behaviour
    "strict_scope": True,
    "fallback_to_root": False,

    # matching
    "convert_t_tag": True,
    "regex_enable": True,
    "kana_bridge_len": 12,
    "first_match_only": True,

    # timing
    "defer_ms": 60,
    "observe_ms": 3000,

    # persistence
    "persist_to_field": True,
    "persist_field_name": "sentence",
    "persist_once": True,

    # style
    "extra_css": ".full-sentence b.auto-bold { color:#ffe37a; font-weight:800; }",
}

def _cfg():
    raw = mw.addonManager.getConfig(__name__) or {}
    cfg = dict(DEFAULTS)
    cfg.update(raw)
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

# -------- CSS injection (append last) --------------------------------------
def _inject_css_once(cfg):
    css = (cfg.get("extra_css") or "").strip()
    if not css:
        return
    js = r"""
    (function(){
      try{
        var old = document.getElementById('autoBoldCSS');
        if (old && old.parentNode) old.parentNode.removeChild(old);
        var el = document.createElement('style');
        el.id = 'autoBoldCSS';
        el.textContent = %s;
        (document.head || document.documentElement).appendChild(el);
      }catch(e){}
    })();
    """ % json.dumps(css)
    try:
        mw.reviewer.web.eval(js)
    except Exception:
        pass

# -------- Core JS builder ---------------------------------------------------
def _js_builder(headword, reading, cfg):
    H = json.dumps(headword or "")
    R = json.dumps(reading or "")
    SELECTORS = json.dumps(cfg["sentence_selectors"])
    CONVERT_T = "true" if cfg["convert_t_tag"] else "false"
    REGEX_EN  = "true" if cfg["regex_enable"] else "false"
    BRIDGE    = int(cfg["kana_bridge_len"])
    FIRST_ONE = "true" if cfg["first_match_only"] else "false"
    OBS_MS    = int(cfg["observe_ms"])
    STRICT    = "true" if cfg.get("strict_scope", True) else "false"
    FALLBACK  = "true" if cfg.get("fallback_to_root", False) else "false"
    PERSIST   = "true" if cfg.get("persist_to_field", True) else "false"

    return r"""
(function(){
  // --- config injected ---
  var target  = %s;
  var reading = %s;

  var SELECTORS   = %s;
  var STRICT      = %s;
  var FALLBACK    = %s;

  var CONVERT_T   = %s;
  var REGEX_EN    = %s;
  var BRIDGE_LEN  = %d;
  var FIRST_MATCH = %s;
  var OBS_MS      = %d;
  var PERSIST     = %s;

  // --- helpers ---
  var KANA = "[\u3040-\u309F\u30A0-\u30FF\u30FC]";
  var JAP  = "[\u3040-\u30FF\u4E00-\u9FFF々\u30FC]";
  function esc(s){ return (s||"").replace(/[.*+?^${}()|[\]\\]/g,"\\$&"); }
  function onlyKana(s){ return (s||"").replace(/[^ぁ-ゖァ-ヺー]/g,""); }
  function toHira(s){ return (s||"").replace(/[\u30A1-\u30FA]/g, c=>String.fromCharCode(c.charCodeAt(0)-0x60)); }
  function toKata(s){ return (s||"").replace(/[\u3041-\u3096]/g, c=>String.fromCharCode(c.charCodeAt(0)+0x60)); }
  function kanjiCore(s){ return (s||"").replace(/[^\u4E00-\u9FFF々]/g,""); }
  function jaLen(s){ var m=(s||"").match(/[\u3040-\u30FF\u4E00-\u9FFF々ー]/g); return m?m.length:0; }

  function looksLikeSentence(el){
    if (!el) return false;
    var t = el.textContent || "";
    if (!t) return false;
    var ok = false;
    if (target && t.indexOf(target) >= 0) ok = true;
    var rd = onlyKana(reading);
    if (!ok && rd){
      if (t.indexOf(toHira(rd)) >= 0 || t.indexOf(toKata(rd)) >= 0) ok = true;
    }
    if (!ok && jaLen(t) >= 8) ok = true;
    return ok;
  }

  function pickSentenceEl(){
    var best = null, bestScore = -1;
    for (var i=0;i<SELECTORS.length;i++){
      var list = document.querySelectorAll(SELECTORS[i]);
      for (var j=0;j<list.length;j++){
        var el = list[j];
        if (!looksLikeSentence(el)) continue;
        var score = jaLen(el.textContent||"");
        if (score > bestScore){ best = el; bestScore = score; }
      }
    }
    return best;
  }

  var sentenceEl = pickSentenceEl();
  if (!sentenceEl && STRICT && !FALLBACK){
    return; // avoid whole-card effects
  }
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

  function attemptBold(){
    if (!root) return "no-sentence";

    var converted = convertTTags(root);
    if (converted > 0) return "applied:t";

    if (sentenceEl && sentenceEl.querySelector('b')) return "already-bolded";
    if (!REGEX_EN) return "no-match";

    var hasKanji = /[\u4E00-\u9FFF々]/.test(target || "");
    var patterns = [];

    // A) flexible kanji bridge
    if (target && hasKanji){
      var ks = (target.match(/[\u4E00-\u9FFF々]/g) || []);
      if (ks.length){
        var core = ks.map(function(k){ return esc(k) + JAP + "{0,"+BRIDGE_LEN+"}"; }).join("");
        patterns.push(new RegExp(core, "g"));
      }
    }
    // B) reading-based (kana) + trailing kana
    if (reading){
      var rd = onlyKana(reading);
      if (rd){
        patterns.push(new RegExp(esc(toHira(rd)) + KANA + "*", "g"));
        patterns.push(new RegExp(esc(toKata(rd)) + KANA + "*", "g"));
      }
    }
    // C) kana headword
    if (target && !hasKanji && /[\u3040-\u30FF]/.test(target)){
      patterns.push(new RegExp(esc(toHira(target)) + KANA + "*", "g"));
      patterns.push(new RegExp(esc(toKata(target)) + KANA + "*", "g"));
    }
    // D/E) literal cores / literal target
    if (target){
      var kc = kanjiCore(target);
      if (kc) patterns.push(new RegExp(esc(kc), "g"));
      patterns.push(new RegExp(esc(target), "g"));
    }

    if (!patterns.length) return "no-match";

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

  function persistIfNeeded(){
    if (!PERSIST) return;
    if (!sentenceEl) return;
    if (sentenceEl.__autoBoldSaved) return;

    var html = sentenceEl.innerHTML || "";
    if (!html || html.indexOf("auto-bold") === -1) return;

    try {
      var b64 = btoa(unescape(encodeURIComponent(html)));
      pycmd("auto_bold_target__persist:" + b64);
      sentenceEl.__autoBoldSaved = true;
    } catch(e){}
  }

  var result = attemptBold();
  if (result !== "no-match"){
    persistIfNeeded();
    return;
  }

  var obs = new MutationObserver(function(){
    var r = attemptBold();
    if (r !== "no-match"){
      try { obs.disconnect(); } catch(e){}
      persistIfNeeded();
    }
  });
  obs.observe(root, {childList:true, subtree:true});
  setTimeout(function(){ try{ obs.disconnect(); }catch(e){} }, OBS_MS);
})();
""" % (H, R, SELECTORS, STRICT, FALLBACK, CONVERT_T, REGEX_EN, BRIDGE, FIRST_ONE, OBS_MS, PERSIST)

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
    try:
        mw.reviewer.web.eval(f"(function(){{setTimeout(function(){{{js}}}, {delay});}})();")
    except Exception:
        return
    if report:
        mw.progress.timer(delay + 200, _report_result, False)

def _report_result():
    check_js = """
      (function(){
        var sels = %s;
        var count = 0;
        for (var i=0;i<sels.length;i++){
          var x = document.querySelectorAll(sels[i] + " b.auto-bold");
          count += x.length;
        }
        if (count) return "applied ("+count+")";
        return "no-match";
      })();
    """ % json.dumps(_cfg()["sentence_selectors"])
    try:
        mw.reviewer.web.evalWithCallback(check_js, lambda res: tooltip(f"Auto-bold: {res}", period=2500))
    except Exception:
        pass

# -------- JP font/locale helper --------------------------------------------
def _force_lang_ja():
    try:
        mw.reviewer.web.eval(r"""
            (function(){
              try {
                document.documentElement.setAttribute('lang','ja');
                document.documentElement.classList.add('lang-ja');
              } catch(e) {}
            })();
        """)
    except Exception:
        pass

# -------- Hooks -------------------------------------------------------------
def _on_q(card):
    _force_lang_ja()
    _inject_css_once(_cfg())
    _run(card)

def _on_a(card):
    _force_lang_ja()
    _run(card)

try: gui_hooks.reviewer_did_show_question.remove(_on_q)
except Exception: pass
try: gui_hooks.reviewer_did_show_answer.remove(_on_a)
except Exception: pass
gui_hooks.reviewer_did_show_question.append(_on_q)
gui_hooks.reviewer_did_show_answer.append(_on_a)

# -------- WebView bridge (persist callback, 3-arg signature) ---------------
def _handle_cmd(handled, message, context):
    """(handled, message, context) -> (handled, result)"""
    if handled:
        return (handled, None)
    if not isinstance(message, str) or not message.startswith("auto_bold_target__persist:"):
        return (False, None)

    # Decode payload
    try:
        b64 = message.split(":", 1)[1]
        html = base64.b64decode(b64).decode("utf-8", "replace")
    except Exception:
        return (True, None)

    cfg = _cfg()
    if not cfg.get("persist_to_field", True):
        return (True, None)

    card = getattr(mw.reviewer, "card", None)
    if not card:
        return (True, None)

    field_name = cfg.get("persist_field_name", "sentence")
    try:
        note = card.note()
        if field_name not in note:
            tooltip(f"Auto-bold: field '{field_name}' not found", period=2000)
            return (True, None)

        if cfg.get("persist_once", True) and "auto-bold" in note[field_name]:
            return (True, None)

        note[field_name] = html
        note.flush()
        tooltip("Auto-bold: saved to note", period=1200)
    except Exception as e:
        tooltip(f"Auto-bold: save failed ({e})", period=3000)

    return (True, None)

try:
    gui_hooks.webview_did_receive_js_message.remove(_handle_cmd)
except Exception:
    pass
gui_hooks.webview_did_receive_js_message.append(_handle_cmd)

# -------- Tools menu actions -----------------------------------------------
def action_run_now():
    c = getattr(mw.reviewer, "card", None)
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
    # Fallback JSON editor (Qt5/Qt6)
    current = mw.addonManager.getConfig(__name__) or {}
    try:
        initial = json.dumps(current if current else DEFAULTS, ensure_ascii=False, indent=2)
    except Exception:
        initial = "{}"
    dlg = QDialog(mw); dlg.setWindowTitle("Auto-bold: Configure (fallback)")
    layout = QVBoxLayout(dlg)
    edit = QTextEdit(dlg); edit.setPlainText(initial); layout.addWidget(edit)
    try:
        Std = QDialogButtonBox.StandardButton  # PyQt6
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

def _add_menu_action(action: QAction):
    action.setObjectName("AutoBold|" + action.text())
    for a in mw.form.menuTools.actions():
        if a.objectName() == action.objectName():
            return
    mw.form.menuTools.addAction(action)

act1 = QAction("Auto-bold now (report)", mw); act1.triggered.connect(action_run_now); _add_menu_action(act1)
act2 = QAction("Configure Auto-bold…", mw);   act2.triggered.connect(action_config); _add_menu_action(act2)
