import os
import re

import markdown as md_lib

# overridable for tests, same pattern as PROJECTMGR_DATA_DIR in app/db.py
COURSES_DIR = os.environ.get("PROJECTMGR_COURSES_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cours"
)

# "001_projet.md" yes, "001_projet_qcm.md" and "thématiques.md" no — matches
# the naming convention documented in Readme.md
TOPIC_FILENAME_RE = re.compile(r"^\d+_[^/\\]+\.md$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")

MD_EXTENSIONS = ["nl2br", "tables", "fenced_code"]


class TopicNotFoundError(Exception):
    pass


def _is_topic_filename(filename: str) -> bool:
    return bool(TOPIC_FILENAME_RE.match(filename)) and not filename.endswith("_qcm.md")


def _read_title(path: str, fallback: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                return stripped.lstrip("#").strip() or fallback
    except OSError:
        pass
    return fallback


def list_topics() -> list[dict]:
    if not os.path.isdir(COURSES_DIR):
        return []
    topics = []
    for filename in sorted(os.listdir(COURSES_DIR)):
        if not _is_topic_filename(filename):
            continue
        topics.append({"filename": filename, "title": _read_title(os.path.join(COURSES_DIR, filename), filename)})
    return topics


def topic_exists(filename: str) -> bool:
    return _is_topic_filename(filename) and os.path.isfile(os.path.join(COURSES_DIR, filename))


def parse_topic(filename: str) -> dict:
    """Split a course markdown file into a tree of slides.

    Every heading of level 1-3 (#, ##, ###) starts its own slide, nested
    under the nearest enclosing heading of a lower level; a slide with no
    body text before the next heading is simply a title-only slide. A
    heading of level 4-6 is not a slide of its own — it stays as literal
    markdown inside the body of the slide it appears under.
    """
    if not topic_exists(filename):
        raise TopicNotFoundError(filename)

    with open(os.path.join(COURSES_DIR, filename), encoding="utf-8") as f:
        text = f.read()

    # course markdown writes image paths relative to /cours (e.g.
    # "img/foo.jpg") — rewrite to wherever those images are actually served
    text = text.replace("](img/", "](/cours-images/")

    slides: dict[int, dict] = {}
    roots: list[dict] = []
    open_ancestor: dict[int, dict] = {}  # level (1 or 2) -> currently open node at that level
    current_slide_id: int | None = None
    next_id = 0
    doc_title: str | None = None
    seen_heading = False

    def start_slide(level: int, title: str) -> None:
        nonlocal current_slide_id, next_id
        node = {"id": next_id, "level": level, "title": title, "children": []}
        slides[next_id] = {"id": next_id, "level": level, "title": title, "body_lines": []}
        if level == 1:
            roots.append(node)
            open_ancestor[1] = node
            open_ancestor.pop(2, None)
        elif level == 2:
            parent = open_ancestor.get(1)
            (parent["children"] if parent else roots).append(node)
            open_ancestor[2] = node
        else:
            parent = open_ancestor.get(2) or open_ancestor.get(1)
            (parent["children"] if parent else roots).append(node)
        current_slide_id = next_id
        next_id += 1

    for line in text.split("\n"):
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if level <= 3:
                seen_heading = True
                start_slide(level, title)
            elif current_slide_id is not None:
                slides[current_slide_id]["body_lines"].append(line)
            continue
        if not seen_heading:
            if doc_title is None and line.strip():
                doc_title = line.strip()
            continue
        if current_slide_id is not None:
            slides[current_slide_id]["body_lines"].append(line)

    rendered_slides = {}
    for sid, s in slides.items():
        body_md = "\n".join(s["body_lines"]).strip()
        body_html = md_lib.markdown(body_md, extensions=MD_EXTENSIONS) if body_md else ""
        rendered_slides[sid] = {"id": sid, "level": s["level"], "title": s["title"], "body_html": body_html}

    title = doc_title or (roots[0]["title"] if roots else filename)
    return {"title": title, "tree": roots, "slides": rendered_slides}
