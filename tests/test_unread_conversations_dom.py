"""Browser-DOM tests for the unread conversation row reader and list scroll.

The unit suite mocks ``page.evaluate``, so neither ``_CONVERSATION_ROWS_JS`` nor the
scrolling JS in ``scroll_conversation_list`` ever executes there. These tests run
both against a synthetic conversation list in headless chromium, where each
scroll appends the next batch after a delay. That delay is the point: it stands
in for the user's connection, and the loop has to survive a batch that arrives
later than the previous one did.

The fixture drives a synthetic container rather than imitating LinkedIn's
markup, so these are claims about the algorithm. The one exception is the row
shape itself (``li.msg-conversation-listitem`` holding a ``label[aria-label]``,
a ``<time>`` and a snippet), which is a claim about LinkedIn taken from the live
DOM and from the shape ``_extract_conversation_thread_refs`` already documents.

Skipped automatically when chromium is not installed; run locally after
``uv run patchright install chromium --no-shell``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from patchright.async_api import async_playwright

from linkedin_mcp_server.core.utils import scroll_conversation_list
from linkedin_mcp_server.scraping.extractor import (
    _CONVERSATION_ROWS_JS,
    _UNREAD_CARD_CLASS,
    LinkedInExtractor,
)

#: CI uses ``--dist loadgroup``. Keep every test that launches Chromium on one
#: worker so browser startups cannot compete with the DOM cases' wall-clock
#: timers.
pytestmark = [
    pytest.mark.browser_dom,
    pytest.mark.xdist_group("browser_runtime"),
]


def row(i: int, *, skeleton: bool = False, unread: bool = False) -> str:
    """One conversation row; a skeleton is mounted but not yet hydrated."""
    if skeleton:
        # What LinkedIn mounts before the content arrives: the list item and its
        # card exist, the label, time and snippet do not.
        return (
            '<li class="ember-view msg-conversation-listitem">'
            '<div class="msg-conversation-card"></div></li>'
        )
    # The live modifier sits on a wrapper inside the row alongside other
    # classes, which is why the JS matches it as a substring.
    card = "msg-conversation-card"
    if unread:
        card = f"msg-conversation-card__{_UNREAD_CARD_CLASS} {card}"
    return (
        '<li class="ember-view msg-conversation-listitem">'
        f'<div class="{card}">'
        "<label"
        f' aria-label="Select conversation with Person {i}"></label>'
        f"<time>Sep {i}</time>"
        f'<p class="msg-conversation-card__message-snippet">Hello {i}</p>'
        "</div></li>"
    )


def conversation_list(
    total: int,
    batch: int,
    delays: list[int],
    initial: int = 5,
    skeletons: int = 0,
) -> str:
    """A scrollable list that appends `batch` rows after each scroll.

    ``delays`` gives the delay of each successive batch in ms, so a fixture can
    vary latency the way a real connection does; the last value repeats.
    ``skeletons`` appends that many unhydrated rows, which is what the live
    unread view holds below the rows it has filled in.
    """
    first = "".join(row(i) for i in range(initial))
    # Skeletons sit below the hydrated rows from the start when the fixture
    # never scrolls; the scroll path appends its own further down.
    if initial >= total:
        first += "".join(row(0, skeleton=True) for _ in range(skeletons))
    return f"""
<!doctype html>
<html><body style="margin:0">
<main>
  <div id="list" style="height:120px;overflow-y:scroll">
    <ul id="rows">{first}</ul>
  </div>
</main>
<script>
  const total = {total}, batch = {batch}, delays = {delays!r};
  const skeletons = {skeletons};
  let rendered = {initial}, round = 0, pending = false;
  const rowHtml = (i) =>
    '<li class="ember-view msg-conversation-listitem">' +
    '<div class="msg-conversation-card">' +
    '<label aria-label="Select conversation with Person ' + i + '"></label>' +
    '<time>Sep ' + i + '</time>' +
    '<p class="msg-conversation-card__message-snippet">Hello ' + i + '</p>' +
    '</div></li>';
  const skeletonHtml =
    '<li class="ember-view msg-conversation-listitem">' +
    '<div class="msg-conversation-card"></div></li>';
  const list = document.getElementById('list');
  const rows = document.getElementById('rows');
  list.addEventListener('scroll', () => {{
    if (pending || rendered >= total) return;
    pending = true;
    const delay = delays[Math.min(round, delays.length - 1)];
    round++;
    setTimeout(() => {{
      let html = '';
      for (let i = rendered; i < Math.min(total, rendered + batch); i++) {{
        html += rowHtml(i);
      }}
      rendered = Math.min(total, rendered + batch);
      if (rendered >= total) {{
        for (let s = 0; s < skeletons; s++) html += skeletonHtml;
      }}
      rows.insertAdjacentHTML('beforeend', html);
      pending = false;
    }}, delay);
  }});
</script>
</body></html>
"""


async def read_rows(page) -> list[dict[str, str]]:
    """What `get_unread_conversations` reads, return shape alike."""
    return await page.evaluate(
        _CONVERSATION_ROWS_JS, {"unreadClass": _UNREAD_CARD_CLASS}
    )


def _chrome_path() -> str | None:
    """A full chromium to drive these fixtures with.

    Patchright's default launch wants a ``chromium_headless_shell`` build, which
    this project does not install: the server launches a full chromium and pins
    it with ``--chrome-path``/``CHROME_PATH`` so the browser matches the one that
    wrote the saved profile. Honour that same variable, then fall back to the
    newest revision under the server's own browser directory.
    """
    explicit = os.getenv("CHROME_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    root = Path.home() / ".linkedin-mcp" / "patchright-browsers"
    candidates = sorted(root.glob("chromium-*/chrome-linux64/chrome"))
    return str(candidates[-1]) if candidates else None


@pytest.fixture
async def page():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True, executable_path=_chrome_path()
            )
        except Exception as exc:  # noqa: BLE001 - environment, not a failure
            pytest.skip(f"chromium unavailable: {exc}")
        ctx = await browser.new_context()
        yield await ctx.new_page()
        await browser.close()


async def test_reads_name_time_and_snippet(page):
    await page.set_content(conversation_list(total=5, batch=5, delays=[10], initial=5))
    rows = await read_rows(page)

    assert len(rows) == 5
    assert rows[0] == {
        "ariaLabel": "Select conversation with Person 0",
        "timestamp": "Sep 0",
        "snippet": "Hello 0",
        "unread": False,
    }


async def test_reports_the_unread_flag_per_row(page):
    """The flag must discriminate, not just be present.

    A reader that marked every row unread, or none, would satisfy a test that
    only checked the field exists.
    """
    marked = row(0, unread=True) + row(1) + row(2, unread=True)
    await page.set_content(
        f"<!doctype html><html><body><main><ul>{marked}</ul></main></body></html>"
    )

    rows = await read_rows(page)

    assert [r["unread"] for r in rows] == [True, False, True]


async def test_skips_unhydrated_skeleton_rows(page):
    """A mounted row with no content is not a conversation.

    The live unread view held 25 such rows out of 55. Emitting them would fill a
    triage list with blanks that cannot be opened or dismissed.
    """
    await page.set_content(
        conversation_list(total=3, batch=3, delays=[10], initial=3, skeletons=4)
    )
    # The skeletons are in the DOM and match the row selector.
    assert await page.locator("li.msg-conversation-listitem").count() == 7

    rows = await read_rows(page)

    assert len(rows) == 3
    assert all(r["ariaLabel"] for r in rows)


async def test_scroll_loads_every_row(page):
    await page.set_content(
        conversation_list(total=40, batch=10, delays=[60], initial=5)
    )
    assert len(await read_rows(page)) == 5

    raised = await scroll_conversation_list(page, settle_timeout=2.0, deadline=20.0)

    assert raised is False
    assert len(await read_rows(page)) == 40


async def test_survives_a_batch_slower_than_the_last(page):
    """The confirmation round at the full settle timeout is what buys these rows.

    A loop that gave up after one shrunken budget would stop at the row count
    the fast batches reached. Delays rise so the shrinking budget is certain to
    be too small for the final batch.
    """
    await page.set_content(
        conversation_list(total=35, batch=10, delays=[40, 40, 900], initial=5)
    )

    raised = await scroll_conversation_list(
        page, settle_timeout=2.0, min_budget=0.1, deadline=25.0
    )

    assert raised is False
    assert len(await read_rows(page)) == 35


def named_row(name: str, timestamp: str, snippet: str) -> str:
    return (
        '<li class="ember-view msg-conversation-listitem">'
        '<div class="msg-conversation-card">'
        f'<label aria-label="Select conversation with {name}"></label>'
        f"<time>{timestamp}</time>"
        f'<p class="msg-conversation-card__message-snippet">{snippet}</p>'
        "</div></li>"
    )


THREAD_PAGE = f"""
<!doctype html>
<html><body>
<main>
  <ul>
    {named_row("Vitor Duarte", "11:00 AM", "Vitor: Vamos retomar o processo")}
    {named_row("Someone Else", "10:30 AM", "Someone: Unrelated")}
    {named_row("Vitor Duarte", "Oct 14", "EPAM Systems Portugal :)")}
  </ul>
  <p>Load more conversations</p>
  <p>Open the options list in your conversation with Vitor Duarte and Jaime Pignatelli</p>
  <p>Vitor Duarte sent the following message at 9:00 AM</p>
  <p>Conta para a semana com o next step</p>
  <p>Jaime Pignatelli sent the following message at 10:52 AM</p>
  <p>Como estamos quanto à entrevista?</p>
  <p>Maximize compose field</p>
  <p>Open GIF Keyboard</p>
  <p>Open send options</p>
</main>
</body></html>
"""


async def test_get_conversation_lists_the_participants_other_thread(page):
    """The 2026-10-15 case: the reply sat in a second thread with Vitor.

    The opened thread is the old one, complete and plausible on its own; the
    sidebar on the same page is the only place the newer thread shows.
    """
    extractor = LinkedInExtractor(page)

    async def load(_url: str) -> None:
        await page.set_content(THREAD_PAGE)

    with (
        patch.object(extractor, "_navigate_to_page", AsyncMock(side_effect=load)),
        patch(
            "linkedin_mcp_server.scraping.extractor.detect_rate_limit",
            new_callable=AsyncMock,
        ),
        patch(
            "linkedin_mcp_server.scraping.extractor.handle_modal_close",
            new_callable=AsyncMock,
        ),
    ):
        result = await extractor.get_conversation(thread_id="2-abc")

    assert result["participant_threads"] == [
        {
            "timestamp": "11:00 AM",
            "snippet": "Vitor: Vamos retomar o processo",
            "unread": False,
        },
        {
            "timestamp": "Oct 14",
            "snippet": "EPAM Systems Portugal :)",
            "unread": False,
        },
    ]
    conversation = result["sections"]["conversation"]
    assert conversation.endswith("Como estamos quanto à entrevista?")
    assert "Vamos retomar" not in conversation


async def test_stops_on_a_list_that_never_grows(page):
    """An exhausted list ends the loop instead of spending the whole deadline."""
    await page.set_content(conversation_list(total=5, batch=0, delays=[10], initial=5))

    raised = await scroll_conversation_list(page, settle_timeout=0.5, deadline=20.0)

    assert raised is False
    assert len(await read_rows(page)) == 5
