"""Check that a downloaded file is really a full text before stage 06 keeps it.

Publishers often answer a scripted request with something other than the
article: a bot or JavaScript challenge page, an empty single-page-app shell,
or (Elsevier's API without a key) an XML envelope holding metadata only.
Those responses come back with HTTP 200, so the status code alone cannot tell
them apart from a real download.

``check_fulltext`` sniffs what the bytes actually are, then applies a
format-specific check. It returns the detected format and, when the file is
not usable as a full text, a short human-readable reason that ends up in the
download error column and the not-downloaded report.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

# Below this much readable text a page cannot be an article's full text.
# A short editorial still has several thousand characters; challenge pages,
# app shells and metadata envelopes have a few hundred.
MIN_TEXT_CHARS = 3000

EXTENSIONS = {"pdf": "pdf", "xml": "xml", "html": "html", "text": "txt"}

_CHALLENGE_TITLE_RE = re.compile(
    r"client challenge|just a moment|attention required|access denied"
    r"|are you a robot|captcha|security check|checking your browser"
    r"|verify(?:ing)? (?:you are|that you are) (?:a )?human|human verification"
    r"|pardon our interruption|request rejected|bot (?:verification|protection)"
    r"|unusual traffic|too many requests|403 forbidden|cookies? (?:absent|disabled)",
    re.IGNORECASE,
)
_JS_REQUIRED_RE = re.compile(
    r"enable javascript|javascript (?:is )?(?:disabled|required|must be enabled)"
    r"|requires javascript|turn on javascript",
    re.IGNORECASE,
)


@dataclass
class FulltextCheck:
    detected_format: str          # pdf | xml | html | text | binary | empty
    reason: Optional[str] = None  # None means the file is usable

    @property
    def ok(self) -> bool:
        return self.reason is None


_HTML_FIRST_TAGS = {
    "html", "head", "body", "meta", "link", "title", "script", "style",
    "noscript", "div", "p", "span", "form", "table", "iframe",
}


def detect_format(data: bytes) -> str:
    """Classify a response body by its content, ignoring URL and headers."""
    if not data or not data.strip():
        return "empty"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n\x00").lower()
    if head.startswith(b"%pdf") or (not head.startswith(b"<") and b"%PDF-" in data[:1024]):
        return "pdf"
    if head.startswith(b"<"):
        if b"<!doctype html" in head:
            return "html"
        first = re.search(rb"<([a-z][\w:.-]*)", head)
        if first and first.group(1).decode("ascii", "replace") in _HTML_FIRST_TAGS:
            return "html"
        return "xml"
    # A multi-byte character cut at the sample boundary costs one U+FFFD.
    text = data[:4096].decode("utf-8", errors="replace")
    printable = sum(ch.isprintable() or ch.isspace() for ch in text)
    if text.count("�") <= 1 and printable / len(text) > 0.95:
        return "text"
    return "binary"


def check_fulltext(data: bytes, expected_format: Optional[str]) -> FulltextCheck:
    """Decide whether ``data`` is a usable full text.

    ``expected_format`` is the format the resolver advertised for the URL. A
    real PDF is accepted whatever was advertised; anything else is accepted
    only when a PDF was not specifically promised and the content passes the
    check for its own format.
    """
    fmt = detect_format(data)
    expected = (expected_format or "").lower()

    if fmt == "empty":
        return FulltextCheck(fmt, "empty response body")
    if fmt == "pdf":
        return FulltextCheck(fmt)

    if fmt == "html":
        problem = _html_problem(data)
    elif fmt == "xml":
        problem = _xml_problem(data)
    elif fmt == "text":
        problem = _text_problem(data)
    else:
        problem = "unrecognised binary content (not PDF, HTML, XML or text)"

    if expected == "pdf":
        got = {"html": "an HTML page", "xml": "an XML document",
               "text": "plain text", "binary": "binary data"}[fmt]
        detail = f": {problem}" if problem else ""
        return FulltextCheck(fmt, f"expected a PDF but got {got}{detail}")
    return FulltextCheck(fmt, problem)


def _html_problem(data: bytes) -> Optional[str]:
    doc = _decode(data)
    parser = _VisibleText()
    try:
        parser.feed(doc)
        parser.close()
    except Exception:
        pass
    title = " ".join(parser.title.split())
    text = " ".join(" ".join(parser.parts).split())
    hidden = " ".join(parser.noscript)

    if len(text) >= MIN_TEXT_CHARS:
        return None
    if title and _CHALLENGE_TITLE_RE.search(title):
        return f'bot or JavaScript challenge page ("{title[:80]}")'
    if _JS_REQUIRED_RE.search(text) or _JS_REQUIRED_RE.search(hidden):
        return (f"page needs JavaScript to show content "
                f"(only {len(text)} characters of text)")
    return (f"page has only {len(text)} characters of text "
            f"(app shell or landing page, not the article)")


def _xml_problem(data: bytes) -> Optional[str]:
    doc = _decode(data)
    head = doc[:4096]
    if "full-text-retrieval-response" in head and "<originalText" not in doc:
        return "Elsevier API returned metadata only (no <originalText> full text)"
    if (re.search(r"<article[\s>]", doc) and re.search(r"<(?:front|article-meta)[\s>]", doc)
            and not re.search(r"<body[\s>]", doc)):
        return "JATS XML has front matter only (no <body>)"
    text = _xml_text(doc)
    if len(text) < MIN_TEXT_CHARS:
        return f"XML has only {len(text)} characters of text (metadata, not the article)"
    return None


def _text_problem(data: bytes) -> Optional[str]:
    text = " ".join(_decode(data).split())
    if len(text) < MIN_TEXT_CHARS:
        return f"plain-text response has only {len(text)} characters"
    return None


def _xml_text(doc: str) -> str:
    doc = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r" \1 ", doc, flags=re.S)
    doc = re.sub(r"<!--.*?-->", " ", doc, flags=re.S)
    doc = re.sub(r"<[^>]+>", " ", doc)
    return " ".join(html.unescape(doc).split())


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class _VisibleText(HTMLParser):
    """Collects the text a reader would see, plus <title> and <noscript>."""

    _SKIP = {"script", "style", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.noscript: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_head = False
        self._in_title = False
        self._title_done = False
        self._in_noscript = 0

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self._skip_depth and not self._title_done:
            self._in_title = True
        elif tag == "head":
            self._in_head = True
        elif tag == "body":
            # </head> is optional in HTML, so <body> also ends the head.
            self._in_head = False
        elif tag == "noscript":
            self._in_noscript += 1
        elif tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = bool(self.title.strip())
        elif tag == "head":
            self._in_head = False
        elif tag == "noscript" and self._in_noscript:
            self._in_noscript -= 1
        elif tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_noscript:
            self.noscript.append(data)
        elif not (self._skip_depth or self._in_head):
            self.parts.append(data)
