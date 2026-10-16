"""Metadata extraction uses only the supplied page bytes."""

from webcollect.parser import parse_html


def test_extracts_and_normalizes_html_metadata():
    html = b"""<html><head><title> Example &amp;\n Page </title>
      <meta name="Description" content=" A   useful page ">
      <link rel="canonical alternate" href="/canonical#fragment">
      </head><body><a href="/next#one">Next</a><a href="/next#two">Duplicate</a>
      <a href="https://OTHER.example/">External</a><area href="../map">
      <a href="javascript:alert(1)">Skip</a><a href="mailto:hi@example.com">Mail</a>
      <a href="https://user:pass@example.com/">Auth</a></body></html>"""
    result = parse_html(html, "https://example.com/docs/index")
    assert result == {
        "url": "https://example.com/docs/index",
        "canonical_url": "https://example.com/canonical",
        "title": "Example & Page",
        "description": "A useful page",
        "outbound_links": [
            "https://example.com/next",
            "https://other.example/",
            "https://example.com/map",
        ],
    }


def test_valid_base_is_used_for_canonical_and_outbound_links():
    result = parse_html(
        b'<base href="https://cdn.example/docs/"><link rel="canonical" href="article">'
        b'<a href="next">Next</a>',
        "https://example.com/",
    )
    assert result["canonical_url"] == "https://cdn.example/docs/article"
    assert result["outbound_links"] == ["https://cdn.example/docs/next"]


def test_invalid_base_and_canonical_fall_back_to_page_url():
    result = parse_html(
        b'<base href="javascript:bad"><link rel="canonical" href="mailto:bad">'
        b'<a href="relative">Link</a>',
        "https://example.com/dir/",
    )
    assert result["canonical_url"] == "https://example.com/dir/"
    assert result["outbound_links"] == ["https://example.com/dir/relative"]


def test_empty_and_malformed_html_are_safe():
    assert parse_html(b"", "http://example.com") == {
        "url": "http://example.com/",
        "canonical_url": "http://example.com/",
        "title": "",
        "description": "",
        "outbound_links": [],
    }
    assert parse_html(b"<title>Unclosed", "http://example.com")["title"] == "Unclosed"
