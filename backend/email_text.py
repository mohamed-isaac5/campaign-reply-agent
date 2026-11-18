"""Separate current authored reply text from conventional quoted history.
Full bodies stay in storage for human review. Unknown empty/ambiguous authorship
is review-only, never a new consent decision from a historical footer.
"""
import re
from html.parser import HTMLParser


class _AuthoredHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.stack = []
        self.skipped = 0
        self.history_started = False

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "hr", "img", "input", "meta", "link"):
            if tag in ("br", "hr") and not self.skipped:
                self.parts.append("\n")
            return
        attrs = dict(attrs)
        marker = (attrs.get("class", "") + " " + attrs.get("id", "")).lower()
        if "divrplyfwdmsg" in marker:
            self.history_started = True
        quote = tag in ("blockquote", "script", "style") or any(token in marker for token in ("gmail_quote", "yahoo_quoted", "moz-cite-prefix", "divrplyfwdmsg"))
        self.stack.append((tag, quote))
        self.skipped += int(quote)
        if not self.skipped and not self.history_started and tag in ("div", "p", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if not any(item[0] == tag for item in self.stack):
            return
        while self.stack:
            name, quote = self.stack.pop()
            self.skipped -= int(quote)
            if name == tag:
                break
        if not self.skipped and not self.history_started and tag in ("div", "p", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipped and not self.history_started:
            self.parts.append(data)


def newest_authored_text(body):
    text = body or ""
    if re.search(r"<(?:html|div|p|blockquote|br)\b", text, re.I):
        parser = _AuthoredHTML()
        parser.feed(text)
        text = "".join(parser.parts)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    current = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        header_end = None
        # Folded conventional attribution: retain authored paragraphs outside
        # explicit > quote blocks, but not an unmarked historical body.
        if re.match(r"^on\s+", stripped, re.I):
            header = []
            for offset, continuation in enumerate(lines[index:index + 6]):
                part = continuation.strip()
                if not part or part.startswith(">"):
                    break
                header.append(part)
                if re.fullmatch(r"on\s+.+\s+wrote\s*:", " ".join(header), re.I):
                    header_end = index + offset + 1
                    break
        if re.match(r"^في .+كتب.*:", stripped, re.I):
            header_end = index + 1
        if header_end is not None:
            following = header_end
            while following < len(lines) and not lines[following].strip():
                following += 1
            if following < len(lines) and lines[following].lstrip().startswith(">"):
                index = following
                continue
            break
        if re.match(r"^(?:-{2,}\s*(?:original|forwarded) message\s*-*|begin forwarded message\s*:)", stripped, re.I):
            break
        if re.match(r"^(?:from|sent|date|to|subject):", stripped, re.I):
            neighborhood = "\n".join(lines[index:index+6])
            if len(re.findall(r"^(?:from|sent|date|to|subject):", neighborhood, re.I | re.M)) >= 2:
                break
        index += 1
        if stripped.startswith(">"):
            continue
        if stripped == "--":
            break
        current.append(line)
    return "\n".join(current).strip()
