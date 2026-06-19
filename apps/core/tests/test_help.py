import pytest
from django.utils.html import escape

from apps.core import help as help_kb


@pytest.mark.django_db
def test_help_index_renders_for_anonymous(client):
    resp = client.get("/help/")
    assert resp.status_code == 200
    assert b"How can we help?" in resp.content
    # Every category header is present (titles may contain escaped characters).
    for cat in help_kb.CATEGORIES:
        assert escape(cat).encode() in resp.content


@pytest.mark.django_db
def test_help_index_lists_all_articles(client):
    resp = client.get("/help/")
    for article in help_kb.ARTICLES:
        assert escape(article.title).encode() in resp.content


@pytest.mark.django_db
@pytest.mark.parametrize("article", help_kb.ARTICLES, ids=lambda a: a.slug)
def test_every_article_renders(client, article):
    resp = client.get(f"/help/{article.slug}/")
    assert resp.status_code == 200
    assert escape(article.title).encode() in resp.content


@pytest.mark.django_db
def test_unknown_article_is_404(client):
    assert client.get("/help/does-not-exist/").status_code == 404


@pytest.mark.django_db
def test_search_filters_results(client):
    resp = client.get("/help/", {"q": "dns"})
    assert resp.status_code == 200
    assert b"dns-setup" in resp.content  # the card links to /help/dns-setup/
    # A clearly unrelated term shows the no-results state.
    resp2 = client.get("/help/", {"q": "zzzznotarealterm"})
    assert b"No matching guides" in resp2.content


def test_search_matches_keywords_and_titles():
    assert help_kb.get_article("dns-setup") in help_kb.search("cloudflare")
    assert help_kb.get_article("team") in help_kb.search("invite teammate")
    assert help_kb.search("") == []


def test_grouped_covers_all_articles():
    grouped_slugs = {a.slug for _, items in help_kb.grouped() for a in items}
    assert grouped_slugs == {a.slug for a in help_kb.ARTICLES}
