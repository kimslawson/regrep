from regrep.extract import extract_visible

FULL_PAGE = """<!DOCTYPE html>
<html>
<head>
  <title>Secret title needle</title>
  <meta name="description" content="meta needle">
  <style>.needle-style { color: red; }</style>
</head>
<body>
  <script>var needle = "script needle";</script>
  <noscript>noscript needle</noscript>
  <template><span>template needle</span></template>
  <iframe src="x">iframe needle</iframe>
  <!-- comment needle -->
  <h1>Visible heading</h1>
  <p>A paragraph with the visible needle in it.</p>
  <div>café — 中文文本</div>
</body>
</html>"""


def test_strips_everything_invisible():
    text = extract_visible(FULL_PAGE)
    assert "Visible heading" in text
    assert "visible needle" in text
    assert "script needle" not in text
    assert "needle-style" not in text
    assert "comment needle" not in text
    assert "noscript needle" not in text
    assert "template needle" not in text
    assert "iframe needle" not in text
    assert "meta needle" not in text
    assert "Secret title" not in text  # head content is not page-visible


def test_preserves_unicode():
    assert "café — 中文文本" in extract_visible(FULL_PAGE)


def test_squeezes_blank_lines():
    text = extract_visible(FULL_PAGE)
    assert "" not in text.splitlines()


def test_br_and_block_boundaries_become_line_breaks():
    assert extract_visible("<p>one<br>two</p><p>three</p>").splitlines() == [
        "one",
        "two",
        "three",
    ]


def test_plain_text_passes_through():
    assert extract_visible("no markup at all") == "no markup at all"


def test_malformed_html_does_not_crash():
    text = extract_visible("<div><p>unclosed <b>bold <script>bad()</div>")
    assert "unclosed" in text
    assert "bad()" not in text
