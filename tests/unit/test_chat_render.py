"""SPEC-013 A25 + tokenizer rules: hostile text escapes; only http(s) links; longest mention wins."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from mihomes.services.chat.render import HIT_START, HIT_STOP, tokenize_body

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "mihomes" / "web" / "templates"


def _render_tokens(tokens) -> str:
    """Render tokens through the same branch logic `_post.html` uses, with autoescape on."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                      autoescape=select_autoescape(["html"]))
    src = (TEMPLATES / "partials" / "chat" / "_post.html").read_text(encoding="utf-8")
    start = src.index("{%- for t in v.tokens -%}")
    end = src.index("{%- endfor -%}", start) + len("{%- endfor -%}")
    return env.from_string(src[start:end]).render(v={"tokens": tokens})


def test_hostile_text_escaped():
    body = ('<script>alert(1)</script> "><img src=x onerror=alert(2)> javascript:alert(3) '
            '@<b>Boss</b> see https://ok.example/a?x=1&y=2.')
    html = _render_tokens(tokenize_body(body, mention_names=["<b>Boss</b>"]))
    assert "<script>" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html
    assert 'href="javascript:' not in html
    hrefs = [part.split('"')[0] for part in html.split('href="')[1:]]
    assert hrefs == ["https://ok.example/a?x=1&amp;y=2"]
    assert all(h.startswith("https://") or h.startswith("http://") for h in hrefs)


def test_links_trim_trailing_punctuation():
    tokens = tokenize_body("go to https://a.example/x). and (https://b.example/wiki_(x))")
    links = [t.href for t in tokens if t.kind == "link"]
    assert links == ["https://a.example/x", "https://b.example/wiki_(x)"]


def test_longest_mention_wins_and_unknown_names_stay_text():
    tokens = tokenize_body("hi @Maria Lopez and @Maria and @Nobody",
                           mention_names=["Maria", "Maria Lopez"])
    assert [t.text for t in tokens if t.kind == "mention"] == ["@Maria Lopez", "@Maria"]
    assert "@Nobody" in "".join(t.text for t in tokens if t.kind == "text")


def test_hit_sentinels_become_hit_tokens():
    tokens = tokenize_body(f"the {HIT_START}pump{HIT_STOP} is <b>loud</b>")
    assert [(t.kind, t.text) for t in tokens] == [
        ("text", "the "), ("hit", "pump"), ("text", " is <b>loud</b>")]
    html = _render_tokens(tokens)
    assert "<mark>pump</mark>" in html and "&lt;b&gt;loud&lt;/b&gt;" in html


def test_spec012_callers_unchanged():
    assert [t.kind for t in tokenize_body("plain")] == ["text"]
