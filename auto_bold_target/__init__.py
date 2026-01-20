# Auto-bold target (JPMN/Migaku) — v2.3.1 (pure-Python persist, robust field detection, debug tooltips)
# Place in: Anki2/addons21/auto_bold_target/__init__.py

from aqt import mw, gui_hooks
from aqt.qt import QAction, QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox
from aqt.utils import tooltip
import json, re

def _save_note_undoably(note, label="Auto-bold"):
    """Save note in a way that plays nice with Undo on modern & older Anki."""
    try:
        # Newer Anki: update_note creates a proper undo step
        mw.checkpoint(label)
        mw.col.update_note(note)
    except Exception:
        # Fallback: checkpoint + flush on older builds
        try:
            mw.checkpoint(label)
        except Exception:
            pass
        note.flush()


# ---------------- Config ----------------
DEFAULTS = {
    # Which note types to act on (empty = all)
    "note_types": ["JP Mining Note"],

    # Headword/reading sources
    "headword_fields": ["Word", "Key"],
    "reading_fields":  ["WordReading"],

    # NEW: sentence field candidates (first one that exists on the note wins)
    "sentence_fields": ["sentence", "Sentence", "Sentence (JPMN)", "SentenceText", "Japanese Sentence"],

    # Backward-compat: if set and present on the note, this is used first
    "persist_field_name": "sentence",

    # Behavior
    "auto_persist_on_show": True,     # run when a card is shown (Q/A)
    "persist_once": True,             # skip if already contains <b class="auto-bold">
    "skip_if_has_ruby": True,         # skip notes whose sentence contains <rt>…</rt> (protect furigana)
    "first_match_only": True,         # wrap only the first match
    "kana_bridge_len": 12,            # how far to allow kana between kanji pieces
    "enable_regex": True,             # if false, only <t> → bold conversion is attempted
    "convert_t_tag": True,            # convert <t>…</t> to bold before regex

    # Optional CSS for review display (not required for persistence)
    "extra_css": ".full-sentence b.auto-bold { color:#ffe37a; font-weight:800; }",

    # Force Japanese glyphs in reviewer webview
    "force_lang_ja": True,

    # Debug
    "debug_tooltips": True,
}

def _cfg():
    raw = mw.addonManager.getConfig(__name__) or {}
    cfg = dict(DEFAULTS); cfg.update(raw); return cfg

# ---------------- Utilities ----------------
def _get_first(note, names):
    for nm in names:
        try:
            v = note[nm].strip()
        except KeyError:
            continue
        if v:
            return v
    return ""

def _should_run(card, cfg):
    nts = cfg.get("note_types") or []
    if not nts:
        return True
    try:
        return card.note().model()["name"] in nts
    except Exception:
        return True

def _pick_sentence_field(note, cfg):
    """Return (field_name, html) for the sentence field, or (None, '') if not found."""
    pf = (cfg.get("persist_field_name") or "").strip()
    if pf and pf in note:
        return pf, note[pf]

    for nm in cfg.get("sentence_fields", []) or []:
        if nm in note:
            return nm, note[nm]

    # last resort: case-insensitive 'sentence'
    for k in note.keys():
        try:
            if k.lower() == "sentence":
                return k, note[k]
        except Exception:
            pass
    return None, ""

def _inject_css_once(cfg):
    css = (cfg.get("extra_css") or "").strip()
    if not css:
        return
    js = r"""
    (function(){
      try{
        var el = document.getElementById('autoBoldCSS');
        if (!el){
          el = document.createElement('style');
          el.id = 'autoBoldCSS';
          (document.head || document.documentElement).appendChild(el);
        }
        el.textContent = %s;
      }catch(e){}
    })();
    """ % json.dumps(css)
    try:
        mw.reviewer.web.eval(js)
    except Exception:
        pass

def _force_lang_ja():
    if not _cfg().get("force_lang_ja", True):
        return
    try:
        mw.reviewer.web.eval(r"""
          (function(){
            try{
              document.documentElement.setAttribute('lang','ja');
              document.documentElement.classList.add('lang-ja');
            }catch(e){}
          })();
        """)
    except Exception:
        pass

# ---------------- Matching helpers ----------------
def _to_hira(s: str) -> str:
    return re.sub(r"[\u30A1-\u30FA]", lambda m: chr(ord(m.group(0)) - 0x60), s or "")

def _to_kata(s: str) -> str:
    return re.sub(r"[\u3041-\u3096]", lambda m: chr(ord(m.group(0)) + 0x60), s or "")

def _only_kana(s: str) -> str:
    return re.sub(r"[^ぁ-ゖァ-ヺー]", "", s or "")

def _kanji_core(s: str) -> str:
    return re.sub(r"[^\u4E00-\u9FFF々]", "", s or "")

def _has_kanji(s: str) -> bool:
    return bool(re.search(r"[\u4E00-\u9FFF々]", s or ""))

def _convert_t_to_b(html: str) -> str:
    # conservative: replace <t>...</t> with <b class="auto-bold">...</b>
    return re.sub(
        r"<t>(.*?)</t>",
        r'<b class="auto-bold">\1</b>',
        html or "",
        flags=re.DOTALL,
    )

def _build_patterns(target: str, reading: str, bridge: int):
    """Pattern priority is tuned to avoid over-highlighting:
       1) exact literal target (NO trailing kana)  ← nouns like 予想 will stop here
       2) kanji-bridge with KANA between kanji + KANA trailing (no kanji leap)
       3) reading-based (hiragana/katakana) + KANA trailing
       4) kanji-core literal (fallback)
    """
    if not (target or reading):
        return []

    KANA = r"[\u3040-\u309F\u30A0-\u30FFー]"
    # JAP no longer used for bridging to avoid jumping across kanji.
    pats = []

    # 1) Exact literal target (stop exactly at the headword)
    if target:
        pats.append(re.compile(re.escape(target)))

    # 2) Kanji-bridge: allow only kana between kanji, and only kana after the last kanji
    #    e.g., 予 + KANA{0,bridge} + 想 + KANA*  (won't cross into 期待)
    if target and re.search(r"[\u4E00-\u9FFF々]", target):
        ks = re.findall(r"[\u4E00-\u9FFF々]", target)
        if ks:
            mid = (KANA + "{0," + str(int(bridge)) + "}")
            # join kanji with limited kana between
            core = (mid).join([re.escape(k) for k in ks])
            # allow trailing kana only
            pats.append(re.compile(core + KANA + "*"))

    # 3) Reading-based (kana) with trailing kana
    if reading:
        rd = re.sub(r"[^ぁ-ゖァ-ヺー]", "", reading)
        if rd:
            hira = re.sub(r"[\u30A1-\u30FA]", lambda m: chr(ord(m.group(0)) - 0x60), rd)
            kata = re.sub(r"[\u3041-\u3096]", lambda m: chr(ord(m.group(0)) + 0x60), rd)
            pats.append(re.compile(re.escape(hira) + KANA + "*"))
            pats.append(re.compile(re.escape(kata) + KANA + "*"))

    # 4) Kanji core (super-conservative fallback)
    if target:
        kc = re.sub(r"[^\u4E00-\u9FFF々]", "", target)
        if kc:
            pats.append(re.compile(re.escape(kc)))

    return pats


def _wrap_first_match(html: str, pattern: re.Pattern, first_only=True) -> str:
    # Simple guard: if ruby present, we skip (safer)
    if "<rt" in (html or "").lower():
        return html

    def repl(m):
        return f'<b class="auto-bold">{m.group(0)}</b>'

    return re.sub(pattern, repl, html, count=1 if first_only else 0)

# ---------------- Saver (Python-side, persistent) ----------------
def _python_persist_bold(note, cfg) -> bool:
    """Compute and save bolded sentence directly to the note. Return True if saved."""
    field, sentence_html = _pick_sentence_field(note, cfg)
    if not field:
        if cfg.get("debug_tooltips"):
            tooltip("Auto-bold: no sentence field found on this note", period=2000)
        return False

    if not sentence_html:
        if cfg.get("debug_tooltips"):
            tooltip(f"Auto-bold: '{field}' is empty", period=2000)
        return False

    if cfg.get("persist_once", True) and 'class="auto-bold"' in sentence_html:
        if cfg.get("debug_tooltips"):
            tooltip("Auto-bold: already bolded, skipping", period=1600)
        return False

    if cfg.get("skip_if_has_ruby", True) and "<rt" in sentence_html.lower():
        if cfg.get("debug_tooltips"):
            tooltip("Auto-bold: ruby detected; skipped (set skip_if_has_ruby=false to override)", period=2400)
        return False

    # Step 1: convert <t>…</t> → <b class="auto-bold">…</b>
    if cfg.get("convert_t_tag", True):
        new_html = _convert_t_to_b(sentence_html)
        if new_html != sentence_html:
            note[field] = new_html
            note.flush()
            if cfg.get("debug_tooltips"):
                tooltip(f"Auto-bold: saved (<t>→<b>) to '{field}'", period=1600)
            return True
        # else continue to regex

    if not cfg.get("enable_regex", True):
        if cfg.get("debug_tooltips"):
            tooltip("Auto-bold: regex disabled; nothing to do", period=1600)
        return False

    head = _get_first(note, cfg["headword_fields"])
    reading = _get_first(note, cfg["reading_fields"])
    if not (head or reading):
        if cfg.get("debug_tooltips"):
            tooltip("Auto-bold: no headword/reading on this note", period=1800)
        return False

    patterns = _build_patterns(head, reading, int(cfg.get("kana_bridge_len", 12)))
    if not patterns:
        if cfg.get("debug_tooltips"):
            tooltip("Auto-bold: no patterns built", period=1600)
        return False

    first_only = cfg.get("first_match_only", True)

    for pat in patterns:
        new_html = _wrap_first_match(sentence_html, pat, first_only=first_only)
        if new_html != sentence_html:
            note[field] = new_html
            note.flush()
            if cfg.get("debug_tooltips"):
                tooltip(f"Auto-bold: saved to '{field}'", period=1500)
            return True

    if cfg.get("debug_tooltips"):
        snippet = (head or reading or "")[:14]
        tooltip(f"Auto-bold: no match (target='{snippet}…')", period=2000)
    return False

# ---------------- Runner & Hooks ----------------
def _run_current(report=False):
    c = getattr(mw.reviewer, "card", None)
    if not c:
        return False
    cfg = _cfg()
    if not _should_run(c, cfg):
        return False
    saved = _python_persist_bold(c.note(), cfg)
    if report:
        tooltip("Auto-bold: " + ("saved" if saved else "no change"), period=1800)
    return saved

def _on_show(card):
    cfg = _cfg()
    _force_lang_ja()
    _inject_css_once(cfg)
    if cfg.get("auto_persist_on_show", True):
        _run_current(report=False)

try: gui_hooks.reviewer_did_show_question.remove(_on_show)
except Exception: pass
try: gui_hooks.reviewer_did_show_answer.remove(_on_show)
except Exception: pass
gui_hooks.reviewer_did_show_question.append(_on_show)
gui_hooks.reviewer_did_show_answer.append(_on_show)

# ---------------- Tools Menu ----------------
def action_run_now():
    _run_current(report=True)

def action_config():
    # Prefer modern API if present
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
            tooltip("Auto-bold: config saved", period=1500)
            dlg.accept()
        except Exception as e:
            tooltip(f"Save failed: {e}", period=2200)
    buttons.accepted.connect(on_save)
    buttons.rejected.connect(dlg.reject)
    dlg.exec()

def _add_menu_action(action: QAction):
    action.setObjectName("AutoBold|" + action.text())
    for a in mw.form.menuTools.actions():
        if a.objectName() == action.objectName():
            return
    mw.form.menuTools.addAction(action)

act1 = QAction("Auto-bold (save to note)", mw); act1.triggered.connect(action_run_now); _add_menu_action(act1)
act2 = QAction("Auto-bold (debug this note)", mw); act2.triggered.connect(lambda: _run_current(report=True)); _add_menu_action(act2)
act3 = QAction("Configure Auto-bold…", mw); act3.triggered.connect(action_config); _add_menu_action(act3)
