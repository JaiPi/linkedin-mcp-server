"""
LinkedIn messaging tools.

Provides inbox listing, conversation reading, message search, and sending.
"""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import (
    AuthenticationError,
    LinkedInScraperException,
)
from linkedin_mcp_server.dependencies import get_ready_extractor, handle_auth_error
from linkedin_mcp_server.error_handler import raise_tool_error
from linkedin_mcp_server.scraping.extractor import (
    SEND_INTERRUPTED_WARNING,
    refuse_an_invalid_message,
)

logger = logging.getLogger(__name__)


def register_messaging_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register all messaging-related tools with the MCP server."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Inbox",
        # Not read-only, though it reads, and the hint is not documentation:
        # `daemon_auth.a_repeat_could_change_something` reads `readOnlyHint` to
        # decide whether a failed call may be replayed automatically, after a
        # sign-in or after a replacement browser owner is elected. This tool
        # click-visits every row to recover thread ids, which marks those rows
        # read, so a silent replay would mark more of them. Same reason
        # `get_conversation` and `search_conversations` below omit it;
        # `get_all_conversations` reads the same listing without clicking.
        annotations={"openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_inbox(
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        List recent conversations from the LinkedIn messaging inbox.

        Args:
            ctx: FastMCP context for progress reporting
            limit: Maximum number of conversations to load (1-50, default 20)

        Returns:
            Dict with url, sections (inbox -> raw text), and optional references.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_inbox"
            )
            logger.info("Fetching inbox (limit=%d)", limit)

            await ctx.report_progress(
                progress=0, total=100, message="Loading messaging inbox"
            )

            result = await extractor.get_inbox(limit=limit)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_inbox")
        except Exception as e:
            raise_tool_error(e, "get_inbox")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        # Genuinely read-only, like get_unread_conversations and unlike
        # get_inbox: it reads the same sidebar listing without clicking a row, so
        # nothing is marked read and an automatic replay cannot change anything.
        title="Get All Conversations",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_all_conversations(
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        List every conversation, read and unread, without marking any read.

        Use this to inventory an inbox. Prefer it over get_inbox, which
        click-visits rows to recover thread IDs and thereby marks them read.

        Each row carries an `unread` flag taken from the conversation card's own
        state. Treat it as advisory: measured against a 190-conversation inbox it
        flagged 13 rows where LinkedIn's own unread filter reported 30. Call
        get_unread_conversations for the authoritative unread set.

        No thread IDs are returned — LinkedIn's sidebar does not carry them in
        the DOM, and recovering one requires the click this tool avoids. To read
        a specific conversation afterwards, pass its participant name to
        get_conversation as linkedin_username.

        Every conversation is loaded, with no limit to set, so expect tens of
        seconds on a large inbox.

        Args:
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, sections (all_conversations -> one line per
            conversation, unread rows prefixed), and all_conversations, a list of
            {name, timestamp, snippet, unread} in inbox order.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_all_conversations"
            )
            logger.info("Fetching all conversations")

            await ctx.report_progress(
                progress=0, total=100, message="Loading conversations"
            )

            result = await extractor.get_all_conversations()

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_all_conversations")
        except Exception as e:
            raise_tool_error(e, "get_all_conversations")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        # Genuinely read-only, unlike the tools below: it reads LinkedIn's own
        # unread filter and clicks nothing. Thread ids are the reason the others
        # click, and this tool returns none for exactly that reason, so an
        # unread conversation stays unread after it runs.
        title="Get Unread Conversations",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_unread_conversations(
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        List every unread conversation without marking any of them read.

        Use this to triage an inbox: it reads LinkedIn's own unread filter, so
        no conversation is opened and the unread state is left intact. Prefer it
        over get_inbox, which click-visits rows to recover thread IDs and
        thereby marks them read.

        No thread IDs are returned — LinkedIn's sidebar does not carry them in
        the DOM, and recovering one requires the click this tool avoids. To read
        a specific conversation afterwards, pass its participant name to
        get_conversation as linkedin_username.

        The entire unread list is loaded, with no limit to set.

        Args:
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url, sections (unread_conversations -> one line per
            conversation), and unread_conversations, a list of
            {name, timestamp, snippet} in inbox order.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_unread_conversations"
            )
            logger.info("Fetching unread conversations")

            await ctx.report_progress(
                progress=0, total=100, message="Loading unread conversations"
            )

            result = await extractor.get_unread_conversations()

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_unread_conversations")
        except Exception as e:
            raise_tool_error(e, "get_unread_conversations")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Get Conversation",
        # Not read-only, though it reads: resolving a username enumerates the
        # inbox by click-visiting rows, and LinkedIn marks a visited row as read.
        # The docstring below has always said so. An unread message the user has
        # not seen is state, and losing it is not something a reader should do.
        annotations={"openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_conversation(
        ctx: Context,
        linkedin_username: str | None = None,
        thread_id: str | None = None,
        index: Annotated[int, Field(ge=0)] = 0,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Read a specific messaging conversation.

        Provide either linkedin_username or thread_id to identify the conversation.

        When looked up by linkedin_username, resolution searches the messaging
        inbox for the participant's display name and click-visits every
        matching row to capture its thread ID — LinkedIn's sidebar has no
        anchor hrefs or thread-id attributes, so this is the only available
        path. Each visit selects the row in the LinkedIn UI and may mark it
        as read. Pass thread_id directly to skip this enumeration.

        A person can have several conversations, and a saved thread_id may not
        hold their newest reply. When the inbox shows more than one
        conversation under this thread's participant name, the result carries
        participant_threads: one entry (timestamp, snippet, unread) per
        conversation, newest first. If one of them is newer than this thread,
        the reply is there: open it with linkedin_username and index.

        Args:
            ctx: FastMCP context for progress reporting
            linkedin_username: LinkedIn username of the conversation participant; a full profile URL is accepted too
            thread_id: LinkedIn messaging thread ID
            index: 0-based selector for which thread to open when the
                participant has multiple threads (e.g. an organic 1-on-1 plus
                an InMail). Ignored when thread_id is provided. To enumerate
                thread IDs first, call search_conversations.

        Returns:
            Dict with url, sections (conversation -> raw text), optional
            references, participant_threads when the participant has more
            than one conversation, and attachments (file_name, size, sender,
            sent_at, index) when files were sent; save one with
            download_attachment.
        """
        if not linkedin_username and not thread_id:
            raise_tool_error(
                LinkedInScraperException(
                    "Provide at least one of linkedin_username or thread_id"
                ),
                "get_conversation",
            )

        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_conversation"
            )
            logger.info(
                "Fetching conversation: username=%s, thread_id=%s, index=%d",
                linkedin_username,
                thread_id,
                index,
            )

            await ctx.report_progress(
                progress=0, total=100, message="Loading conversation"
            )

            result = await extractor.get_conversation(
                linkedin_username=linkedin_username,
                thread_id=thread_id,
                index=index,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_conversation")
        except Exception as e:
            raise_tool_error(e, "get_conversation")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Download Attachment",
        # Not read-only: it opens the thread, which marks it read, and it writes
        # a file. It never overwrites one, so it is not destructive either.
        annotations={"openWorldHint": True},
        tags={"messaging", "scraping"},
        exclude_args=["extractor"],
    )
    async def download_attachment(
        thread_id: str,
        file_name: str,
        ctx: Context,
        index: Annotated[int, Field(ge=0)] = 0,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Save a file attached to a LinkedIn conversation and return its local path.

        Recruiters often send the job description as a PDF or .docx, and it
        tends to hold the rate, hours and contract type. get_conversation lists
        a thread's attachments under `attachments`; pass one's file_name here,
        and its index when the same name was sent more than once (0 = newest).

        The file is saved under downloads/ in the server's data directory
        (~/.linkedin-mcp by default), readable by the owner only, and never
        overwrites an existing file. Converting it to text, e.g. a .docx, is
        left to the caller. Opening the thread marks it read.

        Args:
            thread_id: LinkedIn messaging thread ID, as get_conversation returns in its url
            file_name: Exact attachment file name from get_conversation's attachments
            ctx: FastMCP context for progress reporting
            index: Which same-named attachment to save, 0 = newest

        Returns:
            Dict with url, file_name, saved_path, size_bytes, size, sender and sent_at.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="download_attachment"
            )
            logger.info(
                "Downloading attachment: thread_id=%s, file_name=%s, index=%d",
                thread_id,
                file_name,
                index,
            )

            await ctx.report_progress(
                progress=0, total=100, message="Opening conversation"
            )

            result = await extractor.download_attachment(
                thread_id=thread_id, file_name=file_name, index=index
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "download_attachment")
        except Exception as e:
            raise_tool_error(e, "download_attachment")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Search Conversations",
        # Same reason as `get_conversation`: enumerating result rows selects them
        # in LinkedIn's UI, which can mark them read. Its own `limit` argument is
        # documented in those terms.
        annotations={"openWorldHint": True},
        tags={"messaging", "search"},
        exclude_args=["extractor"],
    )
    async def search_conversations(
        keywords: str,
        ctx: Context,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Search messages by keyword.

        Args:
            keywords: Search keywords to filter conversations
            ctx: FastMCP context for progress reporting
            limit: Maximum number of search-result rows to enumerate as
                conversation references (1-50, default 20). Each enumeration
                selects the row in LinkedIn's UI and may mark it as read, so
                a low cap is preferable for noisy queries.

        Returns:
            Dict with url, sections (search_results -> raw text), and optional references.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="search_conversations"
            )
            logger.info(
                "Searching conversations: keywords='%s', limit=%d", keywords, limit
            )

            await ctx.report_progress(
                progress=0, total=100, message="Searching messages"
            )

            result = await extractor.search_conversations(keywords, limit=limit)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "search_conversations")
        except Exception as e:
            raise_tool_error(e, "search_conversations")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Send Message",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"messaging", "actions"},
        exclude_args=["extractor"],
    )
    async def send_message(
        linkedin_username: str,
        message: str,
        confirm_send: bool,
        ctx: Context,
        profile_urn: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Compose and send a new message to a LinkedIn user.

        Profile-based targeting opens LinkedIn's compose flow. It is not a safe
        reply path for an existing recruiter/InMail or messaging thread: it may
        create a separate DM even after you inspect that thread with
        get_conversation or search_conversations. Those tools only read an
        existing thread; they do not send a reply. Until a thread-targeted send
        path is available, do not treat profile-based send_message as a reply.

        The recipient must be directly messageable from the profile page. If
        LinkedIn does not expose a normal Message action, use connect_with_person
        first, then retry send_message only after the connection request is
        accepted. Recipient authorization comes from validating one
        recipient-specific Message action carrying the target URN, then following
        its browser navigation and pinning the exact final route. Visible profile
        links or recipient URNs in the composer are optional corroboration; any
        contradiction fails closed. No Voyager or other private API is used. This
        is a write operation when confirm_send is True.

        Args:
            linkedin_username: LinkedIn username of the recipient; a full profile URL is accepted too
            message: Single-line message text to send. C0 control characters and
                DEL are rejected, including CR, LF, and tab.
            confirm_send: Must be True to send the message
            ctx: FastMCP context for progress reporting
            profile_urn: Optional profile URN (e.g. ACoAAB...) to verify against
                the URN exposed by the loaded profile before opening its Message
                action. It never bypasses recipient verification. Obtain via
                get_person_profile. Note: inbox may not always show all messages;
                use search_conversations as a fallback.

        Returns:
            Dict with url, status, message, recipient_selected, sent, and
            retry_safe. ``sent`` is true only after the submitted message's DOM
            node gains a different opaque event ID; this does not claim delivery
            or read status. It is false both where nothing was submitted and
            where the outcome is unknown. ``retry_safe`` separates the two: it
            is false from the moment a submission is attempted, and calling
            again while it is
            false can deliver the message twice.
        """
        try:
            # Answered before a session is acquired. Caller-owned message
            # validation needs no browser, and acquiring one can spend a login
            # attempt and come back as an authentication error instead of the
            # refusal the caller can act on. Inside the `try` because building
            # the refusal normalizes the recipient, and an unusable one raises
            # `InvalidReferenceError`; outside, that error would skip
            # `raise_tool_error` and reach the caller masked by
            # `mask_error_details` instead of naming the correction.
            refusal = refuse_an_invalid_message(linkedin_username, message)
            if refusal is not None:
                return refusal
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="send_message"
            )
            logger.info(
                "Sending message to %s (confirm_send=%s)",
                linkedin_username,
                confirm_send,
            )

            await ctx.report_progress(progress=0, total=100, message="Sending message")

            result = await extractor.send_message(
                linkedin_username,
                message,
                confirm_send=confirm_send,
                profile_urn=profile_urn,
            )

            try:
                await ctx.report_progress(progress=100, total=100, message="Complete")
            except BaseException:
                # The send has already answered, and this notification is the
                # last await inside FastMCP's `anyio.fail_after()`. A deadline
                # landing here discards a result that may say the send was
                # confirmed, and nothing can hand it back afterwards, so the
                # log line is all that is left. Quiet where the result says a
                # retry is safe, because then there is nothing to warn about.
                if result.get("retry_safe") is False:
                    logger.warning(SEND_INTERRUPTED_WARNING)
                raise

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "send_message")
        except Exception as e:
            raise_tool_error(e, "send_message")  # NoReturn
