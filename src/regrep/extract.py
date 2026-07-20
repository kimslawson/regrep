"""Visible-text extraction for --visible mode.

Turns archived HTML into roughly what a reader saw: markup, scripts,
styles, comments, and head metadata are removed. Non-HTML input passes
through unchanged (html.parser treats plain text as one big text node).
"""

from bs4 import BeautifulSoup, Comment

# Elements whose entire subtree is invisible to a reader.
_INVISIBLE_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "head",
    "iframe",
    "svg",
    "object",
    "embed",
)


def extract_visible(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_INVISIBLE_TAGS):
        tag.decompose()
    # bs4's get_text() would otherwise include comment bodies.
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    text = soup.get_text(separator="\n")
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)
