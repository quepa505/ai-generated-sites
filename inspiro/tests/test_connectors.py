import json
import time

import httpx2 as httpx
import pytest

from inspiro.config import Settings
from inspiro.connectors import ConnectorError, files, reddit, sync_reddit, x

LISTING = {"kind": "Listing", "data": {"after": None, "children": [
    {"kind": "t3", "data": {"name": "t3_abc", "id": "abc", "title": "Self post", "selftext": "Treść",
                            "is_self": True, "permalink": "/r/godot/comments/abc/self_post/",
                            "url": "https://www.reddit.com/r/godot/comments/abc/self_post/",
                            "subreddit_name_prefixed": "r/godot", "author": "u1", "created_utc": 1700000000}},
    {"kind": "t3", "data": {"name": "t3_def", "id": "def", "title": "Link post", "selftext": "",
                            "is_self": False, "permalink": "/r/rust/comments/def/link_post/",
                            "url": "https://github.com/foo/bar", "subreddit": "rust", "author": "u2"}},
    {"kind": "t1", "data": {"name": "t1_ghi", "id": "ghi", "body": "Świetny komentarz",
                            "link_title": "Wątek", "permalink": "/r/rust/comments/def/link_post/ghi/",
                            "subreddit": "rust", "author": "u3"}},
]}}


def test_reddit_listing_parsing():
    items = reddit.parse_listing(LISTING)
    assert [i.external_id for i in items] == ["t3_abc", "t3_def", "t1_ghi"]
    self_post, link_post, comment = items
    assert self_post.url == "https://www.reddit.com/r/godot/comments/abc/self_post/"
    assert self_post.context == "r/godot" and self_post.created_at.startswith("2023-11-14")
    assert "https://github.com/foo/bar" in link_post.text and link_post.media_urls == ["https://github.com/foo/bar"]
    assert comment.title == "Komentarz w: Wątek" and comment.text == "Świetny komentarz"
    # lista stron z bookmarkletu też działa
    assert len(reddit.parse_listing([LISTING, LISTING])) == 6


def test_reddit_export_csv():
    csv_text = ("id,permalink\nabc,https://www.reddit.com/r/gamedev/comments/abc/my_first_game/\n"
                "xyz,https://www.reddit.com/r/gamedev/comments/abc/my_first_game/xyz/\n")
    post, comment = reddit.parse_export_csv(csv_text)
    assert post.external_id == "t3_abc" and post.title == "My first game" and post.context == "r/gamedev"
    assert comment.external_id == "t1_xyz" and comment.title.startswith("Komentarz w:")


X_PAYLOAD = {
    "data": [{"id": "111", "text": "Krótki https://t.co/aa", "author_id": "9", "created_at": "2026-01-01T10:00:00.000Z",
              "entities": {"urls": [{"url": "https://t.co/aa", "expanded_url": "https://example.com/post"}]},
              "note_tweet": {"text": "Długi wpis https://t.co/aa",
                             "entities": {"urls": [{"url": "https://t.co/aa", "expanded_url": "https://example.com/post"}]}}}],
    "includes": {"users": [{"id": "9", "username": "dev", "name": "Dev Person"}]},
    "meta": {"result_count": 1},
}


def test_x_bookmarks_parsing():
    (item,) = x.parse_bookmarks_response(X_PAYLOAD)
    assert item.external_id == "111" and item.author == "@dev"
    assert item.text == "Długi wpis https://example.com/post"       # note_tweet + rozwinięte linki
    assert item.url == "https://x.com/dev/status/111"
    assert item.media_urls == ["https://example.com/post"]


def test_file_import_detects_formats():
    assert [i.source for i in files.parse_file("r.json", json.dumps(LISTING))] == ["reddit"] * 3
    assert files.parse_file("api.json", json.dumps(X_PAYLOAD))[0].source == "x"
    scraped = {"source": "x", "items": [{"id": "5", "url": "https://x.com/a/status/5", "text": "hej", "author": "a"}]}
    (s,) = files.parse_file("x-bookmarks.json", json.dumps(scraped))
    assert (s.source, s.external_id, s.author) == ("x", "5", "@a")
    txt = files.parse_file("links.txt", "Fajne: https://example.com/a\nbez linku\nhttps://x.com/u/status/77")
    assert [(i.source, i.external_id) for i in txt] == [("manual", "https://example.com/a"), ("x", "77")]
    assert txt[0].title == "Fajne"
    csv_items = files.parse_file("l.csv", "url,title\nhttps://e.com,Tytuł\n")
    assert csv_items[0].title == "Tytuł"
    with pytest.raises(ConnectorError):
        files.parse_file("bad.json", '{"foo": 1}')


def test_manual_item_recognizes_platforms():
    assert files.manual_item(url="https://www.reddit.com/r/a/comments/k9z/x/").external_id == "t3_k9z"
    assert files.manual_item(url="https://twitter.com/u/status/123").source == "x"
    with pytest.raises(ConnectorError):
        files.manual_item()


def test_reddit_sync_paginates_and_stops_on_known(db):
    s = Settings(db_path=db.path, reddit_client_id="id", reddit_client_secret="sec",
                 reddit_username="me", reddit_password="pw")
    pages = {
        None: {"kind": "Listing", "data": {"after": "t3_p2", "children": LISTING["data"]["children"][:2]}},
        "t3_p2": {"kind": "Listing", "data": {"after": None, "children": LISTING["data"]["children"][2:]}},
    }
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if req.url.path == "/api/v1/access_token":
            assert req.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok"})
        assert req.headers["authorization"] == "bearer tok"
        assert req.url.path == "/user/me/saved"
        return httpx.Response(200, json=pages[req.url.params.get("after")])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert sync_reddit(s, db, http) == {"source": "reddit", "added": 3, "skipped": 0}
    calls.clear()
    # druga synchronizacja: pierwsza strona w całości znana -> koniec bez paginacji
    assert sync_reddit(s, db, http)["added"] == 0
    assert len([c for c in calls if "/saved" in c]) == 1


def test_reddit_requires_config(db):
    with pytest.raises(ConnectorError):
        sync_reddit(Settings(db_path=db.path), db, httpx.Client(transport=httpx.MockTransport(lambda r: None)))


def test_x_sync_refreshes_expired_token(db):
    s = Settings(db_path=db.path, x_client_id="cid")
    db.kv_set(x.TOKEN_KEY, {"access_token": "old", "refresh_token": "r1", "expires_at": int(time.time()) - 5})

    def handler(req: httpx.Request):
        if req.url.path == "/2/oauth2/token":
            body = dict(httpx.QueryParams(req.content.decode()))
            assert body["grant_type"] == "refresh_token" and body["refresh_token"] == "r1"
            return httpx.Response(200, json={"access_token": "new", "expires_in": 7200})
        assert req.headers["authorization"] == "Bearer new"
        if req.url.path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": "42"}})
        assert req.url.path == "/2/users/42/bookmarks"
        return httpx.Response(200, json=X_PAYLOAD)

    client = x.XClient(s, db, httpx.Client(transport=httpx.MockTransport(handler)))
    items = client.fetch_bookmarks()
    assert [i.external_id for i in items] == ["111"]
    saved = db.kv_get(x.TOKEN_KEY)
    assert saved["access_token"] == "new" and saved["refresh_token"] == "r1"   # refresh token zachowany


def test_x_pkce_and_authorize_url():
    verifier, challenge = x.make_pkce()
    assert 43 <= len(verifier) <= 128 and "=" not in challenge
    url = x.authorize_url("cid", "http://127.0.0.1:8000/auth/x/callback", "st", challenge)
    assert "bookmark.read" in url.replace("+", " ").replace("%20", " ") and "code_challenge_method=S256" in url
