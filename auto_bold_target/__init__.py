from aqt import mw, gui_hooks
from aqt.qt import QAction
from aqt.utils import tooltip
import json

CONFIG = {
    # Leave empty while testing; later restrict to your exact model name(s).
    "note_types": ["JP Mining Note"],

    # Field names (first non-empty wins)
    "headword_fields": ["Word", "Key"],
    "reading_fields":  ["WordReading"],

    # Optional CSS to make our inserted <b> stand out (JPMN usually styles <b> already)
    "extra_css": """
    .full-sentence b.auto-bold { color:#ffe37a; font-weight:800; }
    """
}

def _get_first(note, names):
    for nm in names:
        try:
            val = note[nm].strip()
        except KeyError:
            continue
        if val:
            return val
    return ""

def _should_run(card):
    nts = CONFIG.get("note_types") or []
    if not nts:
        return True
    try:
        return card.note().model()["name"] in nts
    except Exception:
        return True

def _js_builder(headword, reading):
    H = json.dumps(headword or "")
    R = json.dumps(reading or "")
    return r"""
(function(){
  // Prefer a sentence container; otherwise operate on the full card.
  var sentenceEl = document.querySelector('.full-sentence')
                 || document.querySelector('.jpsentence, .sentence, .sentence-block, .expression--sentence');
  var root = sentenceEl || document.querySelector('#qa') || document.body;

  function convertTTags(scope){
    // Convert any <t>…</t> into <b class="auto-bold">…</b>
    var tEls = scope.querySelectorAll('t');
    var converted = 0;
    if (tEls.length){
      tEls = Array.prototype.slice.call(tEls); // snapshot
      for (var i=0;i<tEls.length;i++){
        var t = tEls[i];
        var b = document.createElement('b');
        b.className = 'auto-bold';
        while (t.firstChild) b.appendChild(t.firstChild);
        t.parentNode.replaceChild(b, t);
        converted++;
      }
    }
    return converted;
  }

  function attemptBold(){
    if (!root) return "no-sentence";

    // STEP 0) Convert any <t> tags first (your cards use these).
    var converted = convertTTags(root);
    if (converted > 0) return "applied:t";

    // STEP 1) If we *do* have a sentence container and it already has <b>, consider it done.
    if (sentenceEl && sentenceEl.querySelector('b')) return "already-bolded";

    // From here on, regex-based fallback (kanji/kana).
    var target  = %s;   // Word/Key
    var reading = %s;   // WordReading (e.g., 課長[かちょー])

    var KANA = "[\\u3040-\\u309F\\u30A0-\\u30FFー]";
    var JAP  = "[\\u3040-\\u30FF\\u4E00-\\u9FFF々ー]"; // kana+kanji+ー
    function esc(s){ return (s||"").replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&"); }
    function onlyKana(s){ return (s||"").replace(/[^ぁ-ゖァ-ヺー]/g,""); }
    function toHira(s){ return (s||"").replace(/[\\u30A1-\\u30FA]/g, c=>String.fromCharCode(c.charCodeAt(0)-0x60)); }
    function toKata(s){ return (s||"").replace(/[\\u3041-\\u3096]/g, c=>String.fromCharCode(c.charCodeAt(0)+0x60)); }
    function kanjiCore(s){ return (s||"").replace(/[^\\u4E00-\\u9FFF々]/g,""); }
    var hasKanji = /[\\u4E00-\\u9FFF々]/.test(target || "");

    var patterns = [];

    // A) Flexible bridge between target kanji: allow kana OR kanji (0..12) between/after.
    if (target && hasKanji){
      var ks = (target.match(/[\\u4E00-\\u9FFF々]/g) || []);
      if (ks.length){
        var bridge = "{0,12}";
        var core = ks.map(function(k){ return esc(k) + JAP + bridge; }).join("");
        patterns.push(new RegExp(core, "g"));
      }
    }

    // B) Reading fallback (kana) + trailing kana
    if (reading){
      var rd = onlyKana(reading); // 課長[かちょー] -> かちょー
      if (rd){
        patterns.push(new RegExp(esc(toHira(rd)) + KANA + "*", "g"));
        patterns.push(new RegExp(esc(toKata(rd)) + KANA + "*", "g"));
      }
    }

    // C) If headword itself is kana, match kana + trailing kana
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
          break;
        }
      });
      if (applied) break outer;
    }

    return applied ? "applied:regex" : "no-match";
  }

  // Try now; if other scripts mutate after load, observe longer & retry.
  var result = attemptBold();
  if (result === "no-match"){
    var retries = 6; // ~3s total
    var obs = new MutationObserver(function(){
      var r = attemptBold();
      if (r !== "no-match"){ obs.disconnect(); }
    });
    obs.observe(root, {childList:true, subtree:true});
    setTimeout(function(){ try{ obs.disconnect(); }catch(e){} }, 3000);
  }
})();
""" % (H, R)

def _inject_css_once():
    css = (CONFIG.get("extra_css") or "").strip()
    if not css:
        return
    js = "var s=document.getElementById('autoBoldCSS'); if(!s){s=document.createElement('style'); s.id='autoBoldCSS'; s.textContent=%s; document.documentElement.appendChild(s);}" % json.dumps(css)
    mw.reviewer.web.eval(js)

def _run(card):
    if not _should_run(card):
        return
    note = card.note()
    head = _get_first(note, CONFIG["headword_fields"])
    reading = _get_first(note, CONFIG["reading_fields"])
    js = _js_builder(head, reading)
    mw.reviewer.web.eval("(function(){setTimeout(function(){%s}, 60);})();" % js)

def _report_result():
    check_js = """
      (function(){
        var sentenceEl = document.querySelector('.full-sentence')
                       || document.querySelector('.jpsentence, .sentence, .sentence-block, .expression--sentence');
        var root = sentenceEl || document.querySelector('#qa') || document.body;
        if (!root) return 'no-sentence';
        var tCount = root.querySelectorAll('t').length;
        var boldCount = root.querySelectorAll('b.auto-bold').length;
        if (boldCount) return 'applied (t='+tCount+', b='+boldCount+')';
        if (sentenceEl && sentenceEl.querySelector('b')) return 'already-bolded';
        return 'no-match (t='+tCount+', b='+boldCount+')';
      })();
    """
    mw.reviewer.web.evalWithCallback(check_js, lambda res: tooltip(f"Auto-bold: {res}", period=2500))

def on_q(card):
    _inject_css_once()
    _run(card)

def on_a(card):
    _run(card)

gui_hooks.reviewer_did_show_question.append(on_q)
gui_hooks.reviewer_did_show_answer.append(on_a)

def manual():
    c = mw.reviewer.card
    if c:
        _inject_css_once()
        _run(c)
        mw.reviewer.web.eval("(function(){setTimeout(function(){}, 150);})();")
        _report_result()

act = QAction("Auto-bold now (report)", mw)
act.triggered.connect(manual)
mw.form.menuTools.addAction(act)
