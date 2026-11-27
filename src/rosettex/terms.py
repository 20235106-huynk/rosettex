from collections import Counter
from pathlib import Path
import re

from rosettex.segment import Segment

_PH = re.compile(r"⟦/?[a-z]\d+⟧")
_TOKEN = re.compile(r"[^\W\d_][\w\-]*[^\W_]|[^\W\d_]{2,}")  # Unicode letters: Rényi, sous-algèbres
_SENT_END = re.compile(r"[.!?:\u2014\u2013]\s*[\"'\u201c\u2018(\[]*$")
_ROMAN = re.compile(r"^[IVXLCDM]+$")

# Function words, modifiers, and auxiliary verbs that cannot start or end a candidate phrase.
_EDGE_STOP: frozenset[str] = frozenset(
    {
        "a", "an", "the", "of", "and", "or", "nor", "but", "yet", "so", "for", "to", "in", "on", "at",
        "by", "with", "from", "as", "into", "onto", "upon", "over", "under", "above", "below", "between",
        "among", "through", "across", "within", "without", "during", "before", "after", "until", "since",
        "while", "although", "though", "because", "unless", "whether", "if", "then", "else", "than",
        "that", "this", "these", "those", "which", "who", "whom", "whose", "what", "where", "when", "why",
        "how", "it", "its", "we", "our", "ours", "us", "they", "their", "theirs", "them", "he", "his",
        "she", "her", "you", "your", "is", "are", "was", "were", "be", "been", "being", "am", "have",
        "has", "had", "having", "do", "does", "did", "doing", "done", "will", "would", "shall", "should",
        "can", "could", "may", "might", "must", "need", "not", "no", "none", "neither", "either", "both",
        "all", "any", "some", "each", "every", "other", "another", "such", "same", "own", "more", "most",
        "less", "least", "many", "much", "few", "several", "various", "certain", "different", "similar",
        "new", "old", "good", "better", "best", "bad", "worse", "worst", "high", "higher", "highest",
        "low", "lower", "lowest", "large", "larger", "largest", "small", "smaller", "smallest", "long",
        "longer", "short", "shorter", "strong", "stronger", "strongest", "great", "greater", "further",
        "additional", "previous", "prior", "existing", "current", "recent", "final", "initial", "original",
        "main", "key", "full", "single", "multiple", "total", "top", "first", "second", "third", "last",
        "next", "only", "just", "even", "still", "also", "too", "very", "quite", "rather", "almost",
        "already", "often", "always", "never", "usually", "typically", "generally", "especially",
        "specifically", "particularly", "however", "therefore", "thus", "hence", "additionally", "finally",
        "here", "there", "now", "well", "about", "against", "along", "around", "behind", "beyond",
        "despite", "except", "inside", "outside", "toward", "towards", "use", "uses", "used", "using",
        "show", "shows", "shown", "find", "finds", "found", "make", "makes", "made", "give", "gives",
        "given", "take", "takes", "taken", "get", "gets", "got", "see", "sees", "seen", "one", "two",
        "three", "four", "five", "six", "seven", "eight", "nine", "ten", "per", "via", "vs", "et", "al",
        "eg", "ie", "etc",
    }
)

# Generic academic/structural single words (never useful as standalone glossary entries).
_GENERIC_SINGLE: frozenset[str] = frozenset(
    {
        "table", "tables", "figure", "figures", "fig", "figs", "section", "sections", "sec", "appendix",
        "appendices", "chapter", "chapters", "equation", "equations", "eq", "theorem", "lemma",
        "proposition", "corollary", "definition", "algorithm", "listing", "model", "models", "method",
        "methods", "approach", "approaches", "result", "results", "experiment", "experiments", "task",
        "tasks", "data", "dataset", "datasets", "paper", "papers", "work", "works", "study", "studies",
        "system", "systems", "example", "examples", "case", "cases", "problem", "problems", "setting",
        "settings", "setup", "setups", "detail", "details", "number", "numbers", "value", "values",
        "score", "scores", "rate", "rates", "level", "levels", "type", "types", "part", "parts", "step",
        "steps", "stage", "stages", "phase", "phases", "layer", "layers", "group", "groups", "class",
        "classes", "set", "sets", "list", "lists", "item", "items", "point", "points", "line", "lines",
        "row", "rows", "column", "columns", "page", "pages", "text", "word", "words", "term", "terms",
        "note", "notes", "input", "inputs", "output", "outputs", "target", "targets", "source", "sources",
        "baseline", "baselines", "performance", "accuracy", "evaluation", "analysis", "discussion",
        "introduction", "conclusion", "acknowledgments", "acknowledgements", "abstract", "overview",
        "background", "summary", "implementation", "training", "testing", "validation", "ablation",
        "comparison", "impact", "broader", "related", "future", "limitations", "references", "question",
        "questions", "answer", "answers", "document", "documents", "passage", "passages", "sentence",
        "sentences", "token", "tokens", "sequence", "sequences", "generation", "retrieval", "language",
        "knowledge", "memory", "index", "indices", "vector", "vectors", "time", "size", "cost", "loss",
        "error", "errors", "state", "art", "human", "real", "true", "false", "open", "end", "natural",
        "domain", "parameters", "parameter", "instances", "instance", "null", "standard", "common",
        "general", "specific", "particular", "average", "mean", "january", "february", "march", "april",
        "may", "june", "july", "august", "september", "october", "november", "december", "jan", "feb",
        "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    }
)


def _is_acronym(tok: str) -> bool:
    if len(tok) < 2 or _ROMAN.match(tok):
        return False
    uppers = sum(c.isupper() for c in tok)
    has_digit = any(c.isdigit() for c in tok)
    if uppers >= 2:
        return True
    return uppers >= 1 and has_digit and len(tok) <= 8


def _is_proper(tok: str) -> bool:
    return len(tok) >= 3 and tok[0].isupper() and any(c.islower() for c in tok[1:])


def _strip_placeholders(text: str) -> str:
    return _PH.sub(" ", text)


# CJK letters (Han, Kana, Hangul; no punctuation): a term is a repeated character n-gram inside one run
_CJK_RUN = re.compile(r"[\u3040-\u30FF\u31F0-\u31FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uAC00-\uD7AF]+")
_HIRAGANA = re.compile(r"[\u3040-\u309F]")
_ZH_EDGE = frozenset("的了是在和与及或为对中把被将从这那个之其也就都而以于由所等")
_KO_PARTICLES = frozenset("은는이가을를에의와과도로서만")


def _cjk_candidates(segs: list[Segment], script: str, stop: set[str]) -> list[tuple[int, str]]:
    """Spec §10.1: CJK sources → repeated character n-grams (2–6), maximal, without grammar at the edges."""
    counts: Counter[str] = Counter()
    for s in segs:
        if s.kind == "macro":
            continue
        for run in _CJK_RUN.findall(_strip_placeholders(s.text)):
            for n in range(2, 8):  # 7-grams only tell whether a 6-gram is a fragment of a longer term
                # Korean writes words with spaces: a term starts a word (inside it are particles and endings)
                for i in range(min(1, len(run) - n + 1) if script == "hangul" else len(run) - n + 1):
                    counts[run[i:i + n]] += 1
    ext: Counter[str] = Counter()  # best count of a one-character extension
    for e, c in counts.items():
        for sub in (e[:-1], e[1:]):
            ext[sub] = max(ext[sub], c)

    def edge_ok(g: str) -> bool:
        if g in stop:
            return False
        if script == "kana":  # Japanese terms are kanji/katakana compounds; hiragana is grammar
            return not _HIRAGANA.search(g)
        if script == "hangul":  # a word plus its particle: keep the word only
            return not (g[-1] in _KO_PARTICLES and counts.get(g[:-1], 0) >= 3)
        return g[0] not in _ZH_EDGE and g[-1] not in _ZH_EDGE

    # maximal: an n-gram that mostly occurs inside a longer one is a fragment
    return [(c * 2, g) for g, c in counts.items()
            if c >= 3 and len(g) <= 6 and ext[g] < 0.8 * c and edge_ok(g)]


def candidates(segs: list[Segment], src_lang: dict, limit: int = 40) -> list[str]:
    """Extract filtered terminology candidates per spec §8.2 (≤ 40)."""
    fw = _EDGE_STOP | {w.lower() for w in src_lang.get("function_words", [])}
    single_stop = fw | _GENERIC_SINGLE

    acronym_counts: Counter[str] = Counter()
    proper_mid_counts: Counter[str] = Counter()
    phrase_counts: Counter[str] = Counter()
    phrase_surface: dict[str, Counter[str]] = {}
    macro_terms: list[str] = []

    cjk = src_lang.get("script") in ("han", "kana", "hangul")
    for s in segs:
        clean = _strip_placeholders(s.text)
        if cjk:  # CJK runs get character n-grams below; the word path sees the Latin words only
            clean = _CJK_RUN.sub(" ", clean)
        if s.kind == "macro":
            stripped = re.sub(r"\s+", " ", clean).strip()
            if stripped and 1 <= len(stripped.split()) <= 4 and stripped.lower() not in single_stop:
                macro_terms.append(stripped)
            continue

        is_heading = s.kind in ("heading", "title")
        matches = list(_TOKEN.finditer(clean))
        toks = [m.group(0) for m in matches]

        for idx, m in enumerate(matches):
            tok = m.group(0)
            low = tok.lower()
            if low in single_stop:
                continue
            if _is_acronym(tok):
                acronym_counts[tok] += 1
                continue
            if not is_heading and _is_proper(tok):
                before = clean[: m.start()]
                sent_initial = idx == 0 or not before.strip() or bool(_SENT_END.search(before))
                if not sent_initial:
                    proper_mid_counts[tok] += 1

        for n in (2, 3):
            for i in range(len(matches) - n + 1):
                span_ok = True
                for j in range(i, i + n - 1):
                    gap = clean[matches[j].end() : matches[j + 1].start()]
                    if not gap or not gap.isspace():
                        span_ok = False
                        break
                if not span_ok:
                    continue
                window = toks[i : i + n]
                lows = [w.lower() for w in window]
                if lows[0] in fw or lows[-1] in fw:
                    continue
                if n == 2 and lows[0] == lows[1]:
                    continue
                title_phrase = all(w[0].isupper() for w in window)
                if n == 2 and not title_phrase and all(w in _GENERIC_SINGLE for w in lows):
                    if not any("-" in w for w in lows):
                        continue
                key = " ".join(lows)
                surface = " ".join(window)
                phrase_counts[key] += 1
                phrase_surface.setdefault(key, Counter())[surface] += 1

    scored: list[tuple[int, str]] = []
    seen_low: set[str] = set()

    for m_term in macro_terms:
        low = m_term.lower()
        if low not in seen_low:
            seen_low.add(low)
            scored.append((1000, m_term))

    for tok, cnt in acronym_counts.items():
        if cnt >= 3 and tok.lower() not in seen_low:
            seen_low.add(tok.lower())
            scored.append((cnt * 3, tok))

    for tok, cnt in proper_mid_counts.items():
        if cnt >= 3 and tok.lower() not in seen_low:
            seen_low.add(tok.lower())
            scored.append((cnt * 2, tok))

    accepted_phrases: list[tuple[int, str]] = []
    for key, cnt in phrase_counts.items():
        if cnt < 3:
            continue
        surface = phrase_surface[key].most_common(1)[0][0]
        accepted_phrases.append((cnt, surface))

    accepted_phrases.sort(key=lambda kv: (len(kv[1].split()), kv[0]), reverse=True)
    kept_phrase_lows: list[str] = []
    for cnt, surface in accepted_phrases:
        low = surface.lower()
        if low in seen_low:
            continue
        if any(low in longer for longer in kept_phrase_lows):
            continue
        seen_low.add(low)
        kept_phrase_lows.append(low)
        scored.append((cnt * 2, surface))

    if cjk:
        scored += [(sc, g) for sc, g in _cjk_candidates(segs, src_lang["script"], fw) if g not in seen_low]

    scored.sort(key=lambda kv: (-kv[0], kv[1].lower()))
    return [term for _, term in scored[:limit]]


def write_terms(work: Path, term_candidates: list[str]) -> None:
    """Write terms.md with field header, Terms table, and empty Expressions table (read_terms-compatible)."""
    p = Path(work) / "terms.md"
    if p.exists():
        return
    lines = [
        "field: ",
        "",
        "<!-- Fill 'field:' from the title and abstract, then fill the translation column. "
        "'=' keeps the term as is; empty rows are ignored. -->",
        "",
        "## Terms",
        "",
        "| term | translation | note |",
        "|---|---|---|",
    ]
    for c in term_candidates:
        lines.append(f"| {c} |  |  |")
    lines.extend(
        [
            "",
            "## Expressions",
            "",
            "<!-- List idioms, metaphors and figurative/field-specific expressions you meet in the sheets "
            "with their meaning-based translation; check enforces them everywhere. -->",
            "",
            "| term | translation | note |",
            "|---|---|---|",
        ]
    )
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
