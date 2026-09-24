import os
import re

from app.courses import COURSES_DIR

COMMENT_RE = re.compile(r"^--")
HEADING_RE = re.compile(r"^(#{1,2})\s+(.*?)\s*$")
OPTION_RE = re.compile(r"^([+-])\s+(.*?)\s*$")


class QcmNotFoundError(Exception):
    pass


def qcm_filename_for(topic: str) -> str:
    base = topic[:-3] if topic.endswith(".md") else topic
    return f"{base}_qcm.md"


def qcm_exists_for(topic: str) -> bool:
    return os.path.isfile(os.path.join(COURSES_DIR, qcm_filename_for(topic)))


def parse_qcm(topic: str) -> dict[str, list[dict]]:
    """Parse a course's _qcm.md file into {chapter_title: [question, ...]}.

    Each question is {"label": str, "text": str, "options": [{"text": str, "correct": bool}, ...]}.
    A "--" line is a comment. A "#" heading starts a new chapter (matched
    against the course's own chapter titles). A "##" heading starts a new
    question within the current chapter; the plain text lines that follow
    are its prompt, and "+"/"-" lines are its options (correct/incorrect).
    """
    filename = qcm_filename_for(topic)
    path = os.path.join(COURSES_DIR, filename)
    if not os.path.isfile(path):
        raise QcmNotFoundError(filename)

    chapters: dict[str, list[dict]] = {}
    current_chapter: str | None = None
    current_question: dict | None = None
    text_lines: list[str] = []

    def flush_question():
        nonlocal current_question, text_lines
        if current_question is not None and current_chapter is not None:
            current_question["text"] = "\n".join(text_lines).strip()
            chapters[current_chapter].append(current_question)
        current_question = None
        text_lines = []

    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if COMMENT_RE.match(line):
                continue
            m = HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                flush_question()
                if level == 1:
                    current_chapter = title
                    chapters.setdefault(current_chapter, [])
                else:
                    current_question = {"label": title, "text": "", "options": []}
                continue
            om = OPTION_RE.match(line)
            if om and current_question is not None:
                current_question["options"].append({"text": om.group(2).strip(), "correct": om.group(1) == "+"})
                continue
            if current_question is not None and line.strip():
                text_lines.append(line.strip())
    flush_question()
    return chapters


def get_chapter_questions(topic: str, chapter_title: str) -> list[dict]:
    chapters = parse_qcm(topic)
    questions = chapters.get(chapter_title)
    if not questions:
        raise QcmNotFoundError(chapter_title)
    return questions


def available_chapters(topic: str) -> dict[str, int]:
    """{chapter_title: question_count} for every chapter that has at least one question."""
    if not qcm_exists_for(topic):
        return {}
    return {title: len(questions) for title, questions in parse_qcm(topic).items() if questions}


def sanitize_question(question: dict, index: int) -> dict:
    """The student-facing shape: no `correct` flags, options carry their index."""
    return {
        "index": index,
        "label": question["label"],
        "text": question["text"],
        "options": [{"index": i, "text": o["text"]} for i, o in enumerate(question["options"])],
    }
