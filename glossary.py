"""
KMG Studio task #8: the self-growing glossary system.
See STUDIO_SYSTEM_DESIGN.md, "The glossary system: self-growing, per-client
+ global."

Decision from the design doc worth repeating here since it's the whole
point: semi-automatic growth, not fully automatic. This module only
detects *candidates* by diffing Degas's original (pre-edit) transcript
against the current (possibly human-edited) one -- it never writes
directly into the confirmed glossary. A human still has to click confirm.
"""

import difflib
import re

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "to",
    "of", "in", "on", "for", "with", "at", "by", "from", "as", "that",
    "this", "it", "he", "she", "they", "we", "you", "i", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "can",
    "could", "not", "so", "if", "then", "there", "here", "just", "very",
}


def _clean(word):
    return word.strip(".,!?;:\"'()[]{}")


def _looks_like_candidate(word):
    """Heuristic for 'this substitution looks like a name/company/figure,
    not just a grammar fix' -- see Section 4: 'substitutions that look like
    names/companies/figures (not generic grammar) become candidate
    entries.'"""
    clean = _clean(word)
    if len(clean) < 2:
        return False
    if clean.lower() in STOPWORDS:
        return False
    if re.search(r"\d", clean) or "$" in clean:
        return True  # figure signal: contains digits or a dollar sign
    if clean[0].isupper() and clean[1:].islower() and len(clean) > 2:
        return True  # capitalized-not-all-caps signal: likely a proper noun
    return False


def _category_for(word):
    clean = _clean(word)
    if re.search(r"\d", clean) or "$" in clean:
        return "figure"
    return "other"  # can't reliably tell name vs. company from text alone;
    # left for the human to correct on confirm (Studio's UI lets the
    # category be edited at confirm time, not just accepted as-is).


def detect_candidates(original_segments, current_segments):
    """Compares original vs. current segment text (matched by exact start/
    end timestamp) and returns a list of {term, category} dicts for words
    that were substituted or inserted and look like a name/company/figure.
    Segments with no timestamp match, or with identical text, are skipped
    entirely -- only genuine edits get diffed."""
    candidates = []
    orig_by_time = {(s["start"], s["end"]): s["text"] for s in original_segments}

    for seg in current_segments:
        key = (seg["start"], seg["end"])
        orig_text = orig_by_time.get(key)
        if orig_text is None or orig_text == seg["text"]:
            continue

        orig_words = orig_text.split()
        new_words = seg["text"].split()
        matcher = difflib.SequenceMatcher(None, orig_words, new_words)

        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag not in ("replace", "insert"):
                continue
            for w in new_words[j1:j2]:
                if _looks_like_candidate(w):
                    candidates.append({
                        "term": _clean(w),
                        "category": _category_for(w),
                    })

    return candidates


def record_candidates(db, client_id, candidates):
    """Upserts detected candidates into glossary_terms as 'pending'.

    - If a CONFIRMED entry already matches the term (client-specific or
      global), skip it entirely -- it's already known, no need to re-flag.
    - If a PENDING entry for this client already has the term, just bump
      occurrence_count instead of creating a duplicate row.
    - Otherwise, insert a new pending row.

    Returns the number of new pending rows created (for a simple "N new
    candidates found" message)."""
    created = 0
    for c in candidates:
        term = c["term"]
        already_confirmed = db.execute(
            """SELECT id FROM glossary_terms
               WHERE status = 'confirmed' AND lower(term) = lower(?)
               AND (client_id = ? OR client_id IS NULL)""",
            (term, client_id),
        ).fetchone()
        if already_confirmed:
            continue

        existing_pending = db.execute(
            """SELECT id, occurrence_count FROM glossary_terms
               WHERE status = 'pending' AND lower(term) = lower(?) AND client_id = ?""",
            (term, client_id),
        ).fetchone()
        if existing_pending:
            db.execute(
                "UPDATE glossary_terms SET occurrence_count = ? WHERE id = ?",
                (existing_pending["occurrence_count"] + 1, existing_pending["id"]),
            )
        else:
            db.execute(
                """INSERT INTO glossary_terms (client_id, term, category, status, occurrence_count)
                   VALUES (?, ?, ?, 'pending', 1)""",
                (client_id, term, c["category"]),
            )
            created += 1
    db.commit()
    return created
