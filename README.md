# Auto-bold target for JPMN cards (Anki)

Automatically bold the target word in the sentence at review time without touching your templates.  
Works with JPMN and converted Anki cards. Handles kanji + kana conjugations, kana-only words, and even cards that already wrap targets with `<t>…</t>`.

## Features
- Bold on render, no template edits
- Conjugation aware (kanji bridge + kana fallback)
- Kana-only words supported (hiragana/katakana)
- Converts `<t>…</t>` markers to `<b>` automatically
- Plays nice with JPMN/AJT/Migaku scripts
- Tools menu action to force and report status

## Requirements
- Modern Anki Qt6 builds (e.g. 2.1.56+ and 2024–2025 releases)
- Desktop Anki (Windows/macOS/Linux)

## Install
### Option A: Release file
1. Download the latest `auto_bold_target.ankiaddon` from the Releases page.
2. In Anki: `Tools → Add-ons → Install from file…` and select it.
3. Restart Anki.

### Option B: Manual
1. Copy the `auto_bold_target` folder into your Anki `addons21/` directory.
2. Restart Anki.

## Configure
Open `auto_bold_target/__init__.py` and edit the `CONFIG` dict:
- `note_types`: leave empty to run on all notes while testing, then set to your exact model name(s).
- `headword_fields`: order of fields that contain the headword (e.g. `Word`, `Key`).
- `reading_fields`: order of fields with a reading (e.g. `WordReading`).

## Use
- Review cards as normal.  
- If needed: `Tools → Auto-bold now (report)`. You will see one of:
  - `applied` – bold added now
  - `already-bolded` – card already had bold
  - `no-match` – nothing matched; check fields/selector details
  - `no-sentence` – could not find a sentence root

## How it works
- First converts any `<t>…</t>` to `<b class="auto-bold">…</b>`.
- If no `<t>` found, tries:
  1. Flexible kanji bridge with kana/kanji between pieces
  2. Reading fallback (hiragana/katakana) with trailing kana
  3. Kana headword fallback
  4. Literal kanji core
  5. Literal headword

Skips `<rt>` to avoid breaking furigana.

## Troubleshooting
- **“no-match”**: confirm your headword/reading fields in `CONFIG`, and that your sentence container is one of:
  `.full-sentence`, `.jpsentence`, `.sentence`, `.sentence-block`, `.expression--sentence`.  
  The add-on falls back to the whole card if none are found.
- **Converted Migaku cards**: if the card already wraps the target with `<t>…</t>`, the add-on will convert it automatically.
- **Conflicts**: If another script also wraps bold, you may see `already-bolded` which is fine.

## License
MIT
