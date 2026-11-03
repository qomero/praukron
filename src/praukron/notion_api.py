"""A small Notion API client on the standard library (ADR-087).

It retries rate limits and server errors with backoff, pages through lists,
and never puts the token in an exception or a log line. A comment is not
idempotent, so its POST is retried only after a 429, which Notion did not act on.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator

from .notion_config import API, NOTION_VERSION, TIMEOUT, Token, malformed, redact

RETRY_STATUS = {429, 500, 502, 503, 504}


class NotionError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"Notion {status} {code}: {message}")
        self.status = status
        self.code = code


class Client:
    def __init__(self, token: Token, *, attempts: int = 5,
                 sleep: Callable[[float], None] = time.sleep,
                 opener: Callable[..., Any] | None = None) -> None:
        if token.value is None:
            raise NotionError(0, "missing-token", "no Notion key is configured; run `praukron notion doctor`")
        problem = malformed(token)
        if problem:   # refused before any header is built, so no library can echo it
            raise NotionError(0, "malformed-token", problem)
        self._token = token
        self.attempts = attempts
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self.calls = 0

    def request(self, method: str, path: str, body: dict | None = None, *, retry: bool = True) -> dict:
        """`retry=False` is for a request Notion may have acted on although no
        answer came back: only a 429 is tried again; every other failure raises."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        delay = 1.0
        for attempt in range(1, self.attempts + 1):
            request = urllib.request.Request(
                f"{API}/{path.lstrip('/')}", data=data, method=method,
                headers={
                    "Authorization": f"Bearer {self._token.value}",
                    "Notion-Version": NOTION_VERSION,
                    "Content-Type": "application/json",
                },
            )
            self.calls += 1
            try:
                with self.opener(request, timeout=TIMEOUT) as response:
                    return json.loads(response.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as error:
                try:
                    payload = json.loads(error.read().decode("utf-8") or "{}")
                except (ValueError, OSError):
                    payload = {}
                finally:
                    error.close()
                if error.code in (RETRY_STATUS if retry else {429}) and attempt < self.attempts:
                    retry_after = error.headers.get("Retry-After") if error.headers else None
                    self.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                    delay *= 2
                    continue
                raise NotionError(error.code, str(payload.get("code", "http-error")),
                                  redact(str(payload.get("message", error.reason)), self._token)) from None
            except (urllib.error.URLError, OSError) as error:
                if retry and attempt < self.attempts:
                    self.sleep(delay)
                    delay *= 2
                    continue
                # Refused or unresolvable before a byte was sent: Notion never saw it.
                reason = getattr(error, "reason", error)
                code = "not-sent" if isinstance(reason, (ConnectionRefusedError, socket.gaierror)) else "unreachable"
                raise NotionError(0, code, redact(str(error), self._token)) from None
            except Exception as error:
                # A header or protocol error (http.client's ValueError, HTTPException)
                # may quote the Authorization header, escaped: name its type only.
                raise NotionError(0, "transport", f"the request failed ({type(error).__name__})") from None
        raise AssertionError("unreachable")  # pragma: no cover

    def paginate(self, method: str, path: str, body: dict | None = None) -> Iterator[dict]:
        cursor = None
        while True:
            page_body = dict(body or {})
            if cursor:
                page_body["start_cursor"] = cursor
            if method == "GET":
                sep = "&" if "?" in path else "?"
                result = self.request("GET", path + (f"{sep}start_cursor={cursor}" if cursor else ""))
            else:
                page_body.setdefault("page_size", 100)
                result = self.request(method, path, page_body)
            yield from result.get("results", [])
            if not result.get("has_more"):
                return
            cursor = result.get("next_cursor")

    # Thin helpers over the endpoints the sync uses.
    def me(self) -> dict:
        return self.request("GET", "users/me")

    def search(self, query: str = "", kind: str | None = None) -> list[dict]:
        body: dict = {"query": query}
        if kind:
            body["filter"] = {"property": "object", "value": kind}
        return list(self.paginate("POST", "search", body))

    def page(self, page_id: str) -> dict:
        return self.request("GET", f"pages/{page_id}")

    def children(self, block_id: str) -> list[dict]:
        return list(self.paginate("GET", f"blocks/{block_id}/children?page_size=100"))

    def append(self, block_id: str, blocks: list[dict], after: str | None = None) -> dict:
        """Add children at the end, or right after the child `after`; the
        result's `results` hold every block each call returned."""
        results: list[dict] = []
        for start in range(0, len(blocks), 100):
            body: dict = {"children": blocks[start:start + 100]}
            if after:
                body["after"] = after
            chunk = self.request("PATCH", f"blocks/{block_id}/children", body).get("results") or []
            results.extend(chunk)
            if after and chunk:
                after = chunk[-1]["id"]
        return {"results": results}

    def delete_block(self, block_id: str) -> dict:
        return self.request("DELETE", f"blocks/{block_id}")

    def update_block(self, block_id: str, body: dict) -> dict:
        return self.request("PATCH", f"blocks/{block_id}", body)

    def database(self, database_id: str) -> dict:
        return self.request("GET", f"databases/{database_id}")

    def data_source(self, data_source_id: str) -> dict:
        return self.request("GET", f"data_sources/{data_source_id}")

    def query(self, data_source_id: str, filter: dict | None = None) -> list[dict]:
        body = {"filter": filter} if filter else {}
        return list(self.paginate("POST", f"data_sources/{data_source_id}/query", body))

    def create_database(self, parent_page: str, title: str, properties: dict,
                        icon: str | None = None) -> dict:
        body: dict = {
            "parent": {"type": "page_id", "page_id": parent_page},
            "title": [{"type": "text", "text": {"content": title}}],
            "initial_data_source": {"properties": properties},
        }
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        return self.request("POST", "databases", body)

    def update_data_source(self, data_source_id: str, properties: dict) -> dict:
        return self.request("PATCH", f"data_sources/{data_source_id}", {"properties": properties})

    def create_page(self, parent: dict, properties: dict, children: list[dict] | None = None,
                    icon: str | None = None) -> dict:
        body: dict = {"parent": parent, "properties": properties}
        if children:
            body["children"] = children[:100]
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        page = self.request("POST", "pages", body)
        if children and len(children) > 100:
            self.append(page["id"], children[100:])
        return page

    def update_page(self, page_id: str, properties: dict | None = None,
                    archived: bool | None = None, icon: dict | None = None) -> dict:
        body: dict = {}
        if properties is not None:
            body["properties"] = properties
        if archived is not None:
            body["in_trash"] = archived
        if icon is not None:
            body["icon"] = icon
        return self.request("PATCH", f"pages/{page_id}", body)

    def create_comment(self, page_id: str, rich_text: list[dict]) -> dict:
        """A comment on a page; a user mention in it reaches that user's phone.
        Never retried after an uncertain failure: the caller reconciles with
        `list_comments` before it posts again."""
        return self.request("POST", "comments", {"parent": {"page_id": page_id}, "rich_text": rich_text},
                            retry=False)

    def list_comments(self, page_id: str) -> list[dict]:
        """The unresolved comments on a page."""
        return list(self.paginate("GET", f"comments?block_id={page_id}&page_size=100"))
