import httpx
import pytest

from yourfeed.fetch import FetchError, SafeHttpClient, check_url
from yourfeed.pipeline.pagetext import PageFetcher, html_page

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731
ARTICLE = ("<html><head><title>月面着陸の計画 | 宇宙ニュース</title>"
           "<meta property='og:site_name' content='宇宙ニュース'>"
           "<meta property='article:published_time' content='2026-11-03T09:00:00+09:00'></head>"
           "<body><nav>ホーム ニュース 特集</nav><article><h1>月面着陸の計画</h1>"
           + "<p>機構は2027年に月面着陸を目指すと発表した。費用は300億円の見込み。</p>" * 8
           + "</article><footer>著作権</footer></body></html>")


def pdf(text: str) -> bytes:
    """A one-page PDF with `text` in Helvetica."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def fetcher(routes):
    def handler(request):
        route = routes.get(request.url.path)
        if route is None:
            return httpx.Response(404)
        status, content_type, body = route
        return httpx.Response(status, content=body, headers={"Content-Type": content_type})
    return PageFetcher(SafeHttpClient(httpx.Client(transport=httpx.MockTransport(handler)), resolver=PUBLIC,
                                      allow_private=False))


def test_html_pages_give_the_article_text_and_metadata():
    page = html_page(ARTICLE, "https://space.example/a")
    assert page.text.startswith("月面着陸の計画\n機構は2027年")
    assert "ホーム" not in page.text and "著作権" not in page.text
    assert page.title == "月面着陸の計画" and page.site_name == "宇宙ニュース"
    assert page.published_at.date().isoformat() == "2026-11-03" and page.language is None
    assert html_page(ARTICLE.replace("<html>", "<html class='a' lang=\"ja\">"), "https://space.example/a").language == "ja"


def test_reader_follows_robots_and_reads_html_and_pdf():
    reader = fetcher({
        "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /private/\n"),
        "/a": (200, "text/html; charset=utf-8", ARTICLE.encode()),
        "/paper.pdf": (200, "application/pdf", pdf("Results of the lunar landing study. " * 12)),
        "/private/x": (200, "text/html", ARTICLE.encode()),
        "/image": (200, "image/png", b"\x89PNG"),
        "/short": (200, "text/html", b"<html><body><p>short</p></body></html>"),
    })
    page, reason = reader.read("https://space.example/a")
    assert reason is None and page.site_name == "宇宙ニュース"
    page, reason = reader.read("https://space.example/paper.pdf")
    assert reason is None and page.text.startswith("Results of the lunar landing study.")
    assert reader.read("https://space.example/private/x") == (None, "robots_disallow")
    assert reader.read("https://space.example/image") == (None, "not_a_page")
    assert reader.read("https://space.example/short") == (None, "too_little_text")
    assert reader.read("https://space.example/missing") == (None, "http_404")


def test_broken_pdf_is_skipped():
    reader = fetcher({"/robots.txt": (404, "text/plain", b""),
                      "/broken.pdf": (200, "application/pdf", b"%PDF-1.4 broken")})
    page, reason = reader.read("https://space.example/broken.pdf")
    assert page is None and reason in {"unreadable", "too_little_text"}


def test_private_addresses_are_refused():
    for url in ("http://127.0.0.1/", "http://localhost/", "http://[::1]/", "http://169.254.169.254/latest",
                "http://10.0.0.1/", "http://printer.local/", "http://[::ffff:127.0.0.1]/"):
        with pytest.raises(FetchError) as error:
            check_url(url, resolver=PUBLIC)
        assert error.value.code == "private_address"
    with pytest.raises(FetchError):
        check_url("https://rebind.example/", resolver=lambda host: ["93.184.216.34", "192.168.1.2"])
    with pytest.raises(FetchError):
        check_url("https://user:pass@example.com/", resolver=PUBLIC)
    check_url("https://example.com/", resolver=PUBLIC)


def test_redirects_are_checked_and_size_is_limited():
    def handler(request):
        if request.url.host == "start.example":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})
        return httpx.Response(200, content=b"x" * 2000)

    resolver = lambda host: ["127.0.0.1"] if host == "127.0.0.1" else ["93.184.216.34"]  # noqa: E731
    client = SafeHttpClient(httpx.Client(transport=httpx.MockTransport(handler)), resolver=resolver,
                            allow_private=False, max_bytes=1000)
    with pytest.raises(FetchError) as error:
        client.fetch("https://start.example/")
    assert error.value.code == "private_address"
    with pytest.raises(FetchError) as error:
        client.fetch("https://big.example/")
    assert error.value.code == "too_large"
