import json
import os
import random
import re
from collections import Counter
from typing import Dict, List, Optional

from openai import OpenAI

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


def _generate_questions_heuristic(text: str, rounds: int, difficulty: str) -> List[Dict]:
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


def _normalize_ai_questions(raw: Dict, rounds: int) -> Optional[List[Dict]]:
    questions = raw.get("questions")
    if not isinstance(questions, list):
        return None

    normalized = []
    for idx, item in enumerate(questions[:rounds], start=1):
        if not isinstance(item, dict):
            continue

        question = str(item.get("question", "")).strip()
        options = item.get("options")
        answer_index = item.get("answer_index")

        if not question or not isinstance(options, list) or len(options) != 4:
            continue
        if not isinstance(answer_index, int) or answer_index < 0 or answer_index > 3:
            continue

        cleaned_options = [str(opt).strip() for opt in options]
        if any(not opt for opt in cleaned_options):
            continue

        normalized.append(
            {
                "id": idx,
                "question": question,
                "options": cleaned_options,
                "correct_index": answer_index,
            }
        )

    if len(normalized) < rounds:
        return None
    return normalized[:rounds]


def _generate_questions_openai(text: str, rounds: int, difficulty: str) -> Optional[List[Dict]]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    context = _clean_text(text)[:25000]
    if not context:
        return None

    prompt = f"""
Generate exactly {rounds} multiple-choice quiz questions from the study material below.
Difficulty: {difficulty}

Rules:
- Output valid JSON only.
- JSON schema:
{{
  "questions": [
    {{
      "question": "string",
      "options": ["string", "string", "string", "string"],
      "answer_index": 0
    }}
  ]
}}
- Exactly 4 options per question.
- Exactly one correct option per question, answer_index in [0,1,2,3].
- Questions must be answerable from the provided material.
- Avoid duplicate questions and avoid trivial wording.
- Keep option lengths similar and plausible.

Study material:
\"\"\"{context}\"\"\"
"""

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=prompt,
        temperature=0.5,
        max_output_tokens=4000,
    )

    raw_text = getattr(response, "output_text", "") or ""
    if not raw_text:
        return None

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    return _normalize_ai_questions(parsed, rounds)


def generate_questions_from_text(text: str, rounds: int, difficulty: str) -> List[Dict]:
    try:
        ai_questions = _generate_questions_openai(text, rounds, difficulty)
        if ai_questions:
            return ai_questions
    except Exception:
        pass

    return _generate_questions_heuristic(text, rounds, difficulty)
