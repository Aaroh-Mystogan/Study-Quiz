import random
import re
from collections import Counter
from typing import Dict, List

STOPWORDS = {
    "the", "and", "that", "with", "from", "this", "have", "were", "their", "there", "which", "would", "about",
    "could", "should", "into", "than", "then", "been", "being", "when", "where", "while", "also", "such", "only",
    "very", "your", "they", "them", "what", "will", "more", "most", "some", "many", "much", "each", "because",
    "between", "before", "after", "during", "over", "under", "just", "like", "these", "those", "through", "used",
    "using", "use", "into", "onto", "across", "without", "within", "whose", "other", "same", "different", "every",
    "make", "made", "does", "done", "did", "can", "may", "might", "must", "its", "our", "ours", "his", "her",
    "hers", "him", "she", "you", "yours", "we", "ourselves", "themselves", "it", "is", "are", "was", "be", "to",
    "of", "in", "on", "for", "as", "an", "a", "or", "at", "by", "if", "not", "no", "yes"
}


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _split_sentences(text: str) -> List[str]:
    raw = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in raw if len(s.strip()) > 40]


def _difficulty_match(sentences: List[str], difficulty: str) -> List[str]:
    if difficulty == "easy":
        return [s for s in sentences if 40 <= len(s) <= 110]
    if difficulty == "medium":
        return [s for s in sentences if 80 <= len(s) <= 170]
    return [s for s in sentences if len(s) >= 130]


def _candidate_terms(text: str) -> List[str]:
    words = re.findall(r"\b[A-Za-z][A-Za-z\-]{3,}\b", text)
    words = [w for w in words if w.lower() not in STOPWORDS]
    counts = Counter(w.lower() for w in words)
    ranked = [w for w, _ in counts.most_common(300)]
    return ranked


def _pick_answer_from_sentence(sentence: str, global_terms: List[str]) -> str:
    tokens = re.findall(r"\b[A-Za-z][A-Za-z\-]{3,}\b", sentence)
    tokens = [t.lower() for t in tokens if t.lower() not in STOPWORDS]
    if not tokens:
        return random.choice(global_terms) if global_terms else "information"

    token_counts = Counter(tokens)
    sentence_ranked = [w for w, _ in token_counts.most_common()]
    for word in sentence_ranked:
        if word in global_terms:
            return word
    return sentence_ranked[0]


def _build_question(sentence: str, answer: str, difficulty: str) -> str:
    masked = re.sub(rf"\b{re.escape(answer)}\b", "_____", sentence, flags=re.IGNORECASE)
    if masked == sentence:
        return f"According to the study material ({difficulty}), which term best fits this statement: {sentence}"
    return f"Fill the blank based on the study material ({difficulty}): {masked}"


def _build_options(answer: str, global_terms: List[str]) -> List[str]:
    pool = [w for w in global_terms if w != answer]
    random.shuffle(pool)
    distractors = []
    for word in pool:
        if word not in distractors and abs(len(word) - len(answer)) <= 8:
            distractors.append(word)
        if len(distractors) == 3:
            break

    while len(distractors) < 3:
        filler = random.choice(global_terms) if global_terms else f"option{len(distractors)+1}"
        if filler != answer and filler not in distractors:
            distractors.append(filler)

    options = [answer] + distractors[:3]
    random.shuffle(options)
    return [o.capitalize() for o in options]


def generate_questions_from_text(text: str, rounds: int, difficulty: str) -> List[Dict]:
    random.seed()
    text = _clean_text(text)
    sentences = _split_sentences(text)
    preferred = _difficulty_match(sentences, difficulty)
    if len(preferred) < rounds:
        preferred = sentences if sentences else []

    if not preferred:
        preferred = [
            "The uploaded material did not contain enough readable text to generate advanced questions.",
            "PDF extraction can fail when pages are scanned images without OCR text layers.",
            "Try uploading cleaner PDF files with selectable text for better quiz quality.",
        ]

    terms = _candidate_terms(text)
    if not terms:
        terms = ["concept", "process", "method", "system", "analysis", "model"]

    random.shuffle(preferred)
    selected = preferred[:rounds]
    while len(selected) < rounds:
        selected.append(random.choice(preferred))

    questions = []
    for idx, sentence in enumerate(selected, start=1):
        answer = _pick_answer_from_sentence(sentence, terms)
        options = _build_options(answer, terms)
        correct_index = next(i for i, opt in enumerate(options) if opt.lower() == answer.lower())
        questions.append(
            {
                "id": idx,
                "question": _build_question(sentence, answer, difficulty),
                "options": options,
                "correct_index": correct_index,
            }
        )

    return questions
