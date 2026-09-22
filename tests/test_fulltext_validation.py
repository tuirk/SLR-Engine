"""Unit tests for full-text download validation (stage 06)."""
from slr_engine.fulltext_validation import check_fulltext, detect_format

# Trimmed from what link.springer.com and nature.com return to scripted requests.
SPRINGER_CHALLENGE = b"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Client Challenge</title>
    <style>#loading-error { font-size: 16px; display: none; }</style>
  </head>
  <body>
    <noscript>
      <div class="noscript-container">
        <span class="noscript-span">JavaScript is disabled in your browser.</span>
        <p>Please enable JavaScript to proceed.</p>
      </div>
    </noscript>
    <div id="loading-error" role="alert" aria-live="polite">
      A required part of this site couldn't load. This may be due to a browser
      extension, network issues, or browser settings.
    </div>
    <script>loadScript('/_fs-ch-1T1wmsGaOgGaSxcX/errors.js');</script>
  </body>
</html>
"""

# Trimmed from https://osf.io/<id>_v1/download, which serves the web app shell.
OSF_APP_SHELL = b"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>OSF</title>
    <style>:root{--openSans:"Open Sans", sans-serif}</style>
  </head>
  <body ngcm="">
    <osf-root></osf-root>
    <script>window.prerenderReady = false;</script>
    <script src="main-NNFCM7IJ.js" type="module"></script>
  </body>
</html>
"""

# Trimmed from api.elsevier.com/content/article/PII:...?httpAccept=text/xml without a key.
ELSEVIER_METADATA_ONLY = b"""<full-text-retrieval-response xmlns="http://www.elsevier.com/xml/svapi/article/dtd" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"><coredata><prism:url>https://api.elsevier.com/content/article/pii/S0260691726003060</prism:url><dc:identifier>doi:10.1016/j.nedt.2026.107278</dc:identifier><dc:title>Vibe coding in nursing education: From user to creator </dc:title><prism:publicationName>Nurse Education Today</prism:publicationName><openaccess>1</openaccess><openaccessArticle>true</openaccessArticle><link href="https://www.sciencedirect.com/science/article/pii/S0260691726003060" rel="scidir"/></coredata></full-text-retrieval-response>"""

PARAGRAPH = (
    "Participants described how natural-language prompting changed the way "
    "they planned, tested and repaired the programs they built with the model. "
)


def _article_html(title="Vibe coding in practice", extra_head="", close_head=True):
    body = "".join(f"<p>{PARAGRAPH * 3}</p>" for _ in range(12))
    head = f"<head><title>{title}</title>{extra_head}"
    head += "</head>" if close_head else ""
    return (
        f"<!DOCTYPE html><html>{head}<body>"
        "<noscript>Please enable JavaScript for the full experience.</noscript>"
        f"<article><h1>{title}</h1>{body}</article></body></html>"
    ).encode("utf-8")


def test_springer_challenge_page_is_rejected():
    check = check_fulltext(SPRINGER_CHALLENGE, "html")
    assert not check.ok
    assert check.detected_format == "html"
    assert "challenge" in check.reason
    assert "Client Challenge" in check.reason


def test_challenge_page_behind_pdf_link_names_the_real_problem():
    check = check_fulltext(SPRINGER_CHALLENGE, "pdf")
    assert not check.ok
    assert check.reason.startswith("expected a PDF but got an HTML page")
    assert "challenge" in check.reason


def test_app_shell_without_text_is_rejected():
    check = check_fulltext(OSF_APP_SHELL, "html")
    assert not check.ok
    assert "only 0 characters" in check.reason


def test_short_page_asking_for_javascript_is_rejected():
    page = (b"<html><head><title>Loading article</title></head><body>"
            b"<p>This site requires JavaScript. Please enable JavaScript and reload.</p>"
            b"</body></html>")
    check = check_fulltext(page, "html")
    assert not check.ok
    assert "needs JavaScript" in check.reason


def test_access_denied_page_is_rejected():
    page = (b"<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>"
            b"<H1>Access Denied</H1>You don't have permission to access this server."
            b"</BODY></HTML>")
    check = check_fulltext(page, "html")
    assert not check.ok
    assert 'challenge page ("Access Denied")' in check.reason


def test_elsevier_metadata_only_xml_is_rejected():
    check = check_fulltext(ELSEVIER_METADATA_ONLY, "xml")
    assert not check.ok
    assert check.detected_format == "xml"
    assert "metadata only" in check.reason


def test_elsevier_xml_with_full_text_is_accepted():
    body = "".join(f"<ce:para>{PARAGRAPH * 3}</ce:para>" for _ in range(12))
    doc = (
        '<full-text-retrieval-response xmlns="http://www.elsevier.com/xml/svapi/article/dtd">'
        "<coredata><dc:title>Vibe coding</dc:title></coredata>"
        f"<originalText><xocs:doc><ja:body><ce:sections>{body}</ce:sections>"
        "</ja:body></xocs:doc></originalText></full-text-retrieval-response>"
    ).encode("utf-8")
    assert check_fulltext(doc, "xml").ok


def test_jats_front_matter_only_is_rejected():
    abstract = PARAGRAPH * 30
    doc = (
        '<?xml version="1.0"?><article article-type="research-article">'
        "<front><article-meta><title-group><article-title>Vibe coding</article-title>"
        f"</title-group><abstract><p>{abstract}</p></abstract></article-meta></front>"
        "</article>"
    ).encode("utf-8")
    check = check_fulltext(doc, "xml")
    assert not check.ok
    assert "no <body>" in check.reason


def test_jats_with_body_is_accepted():
    sections = "".join(f"<sec><p>{PARAGRAPH * 3}</p></sec>" for _ in range(12))
    doc = (
        '<?xml version="1.0"?><article><front><article-meta><title-group>'
        "<article-title>Vibe coding</article-title></title-group></article-meta></front>"
        f"<body>{sections}</body></article>"
    ).encode("utf-8")
    assert check_fulltext(doc, "xml").ok


def test_article_page_with_noscript_banner_is_accepted():
    check = check_fulltext(_article_html(), "html")
    assert check.ok, check.reason


def test_article_page_without_closing_head_tag_is_accepted():
    check = check_fulltext(_article_html(close_head=False), "html")
    assert check.ok, check.reason


def test_long_article_whose_title_sounds_like_a_block_is_accepted():
    check = check_fulltext(_article_html(title="Access denied: vibe coding and gatekeeping"), "html")
    assert check.ok, check.reason


def test_pdf_bytes_are_accepted_whatever_the_link_said():
    pdf = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    for expected in ("pdf", "html", "xml", None):
        check = check_fulltext(pdf, expected)
        assert check.ok
        assert check.detected_format == "pdf"


def test_article_html_behind_pdf_link_is_rejected():
    check = check_fulltext(_article_html(), "pdf")
    assert not check.ok
    assert check.reason == "expected a PDF but got an HTML page"


def test_plain_text_needs_enough_text():
    assert check_fulltext((PARAGRAPH * 40).encode("utf-8"), "html").ok
    short = check_fulltext(b"Title: Vibe coding\nDOI: 10.1/x\n", "html")
    assert not short.ok
    assert short.detected_format == "text"


def test_empty_and_binary_bodies_are_rejected():
    assert check_fulltext(b"", "pdf").reason == "empty response body"
    assert check_fulltext(b"   \n", "html").reason == "empty response body"
    binary = bytes(range(256)) * 8
    check = check_fulltext(binary, "html")
    assert not check.ok
    assert check.detected_format == "binary"


def test_detect_format():
    assert detect_format(b"\xef\xbb\xbf  %PDF-1.4") == "pdf"
    assert detect_format(b'<?xml version="1.0"?><!DOCTYPE html><html></html>') == "html"
    assert detect_format(b'<?xml version="1.0"?><article></article>') == "xml"
    assert detect_format(b"<div>fragment</div>") == "html"
    assert detect_format(ELSEVIER_METADATA_ONLY) == "xml"
    assert detect_format(SPRINGER_CHALLENGE) == "html"
