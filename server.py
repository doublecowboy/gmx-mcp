import os
import ssl
import imaplib
import smtplib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Dict, List, Optional, Tuple

from fastmcp import FastMCP


IMAP_HOST = os.environ.get("GMX_IMAP_HOST", "imap.gmx.com")
IMAP_PORT = int(os.environ.get("GMX_IMAP_PORT", "993"))
SMTP_HOST = os.environ.get("GMX_SMTP_HOST", "mail.gmx.com")
SMTP_PORT = int(os.environ.get("GMX_SMTP_PORT", "587"))


mcp = FastMCP(name="gmx-mail")


class GmxClient:
    def __init__(self, email_addr: str, password: str):
        self.email = email_addr
        self.password = password

    def _imap_connect(self) -> imaplib.IMAP4_SSL:
        ctx = ssl.create_default_context()
        return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)

    def list_messages(
        self,
        mailbox: str = "INBOX",
        limit: int = 10,
        unread_only: bool = False,
    ) -> List[Dict[str, str]]:
        imap = self._imap_connect()
        try:
            imap.login(self.email, self.password)
            imap.select(mailbox, readonly=True)

            criteria = "UNSEEN" if unread_only else "ALL"
            typ, data = imap.uid("search", None, criteria)
            if typ != "OK":
                raise RuntimeError("IMAP search failed")

            uids = (data[0] or b"").split()
            uids = list(reversed(uids))[: max(0, int(limit))]

            results: List[Dict[str, str]] = []

            for uid in uids:
                typ, fetch_data = imap.uid("fetch", uid, b"BODY.PEEK[HEADER]")
                if typ != "OK" or not fetch_data or fetch_data[0] is None:
                    continue

                raw = fetch_data[0][1]
                msg = BytesParser(policy=policy.default).parsebytes(raw)

                results.append(
                    {
                        "uid": uid.decode(),
                        "from": msg.get("From", ""),
                        "to": msg.get("To", ""),
                        "subject": msg.get("Subject", ""),
                        "date": msg.get("Date", ""),
                    }
                )

            return results

        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def _extract_bodies(self, msg) -> Tuple[Optional[str], Optional[str]]:
        text_part = None
        html_part = None

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disposition = str(part.get("Content-Disposition", "")).lower()

                if part.get_content_maintype() == "multipart":
                    continue

                if "attachment" in disposition:
                    continue

                try:
                    payload = part.get_content()
                except Exception:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, (bytes, bytearray)):
                        payload = payload.decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace",
                        )

                if ctype == "text/plain" and text_part is None:
                    text_part = str(payload)

                elif ctype == "text/html" and html_part is None:
                    html_part = str(payload)

        else:
            ctype = msg.get_content_type()
            try:
                body = msg.get_content()
            except Exception:
                body = msg.get_payload(decode=True)
                if isinstance(body, (bytes, bytearray)):
                    body = body.decode(
                        msg.get_content_charset() or "utf-8",
                        errors="replace",
                    )

            if ctype == "text/html":
                html_part = str(body)
            else:
                text_part = str(body)

        return text_part, html_part

    def read_message(
        self,
        uid: str,
        mailbox: str = "INBOX",
        mark_seen: bool = False,
    ) -> Dict[str, Optional[str]]:
        imap = self._imap_connect()
        try:
            imap.login(self.email, self.password)
            imap.select(mailbox, readonly=not mark_seen)

            fetch_part = b"RFC822" if mark_seen else b"BODY.PEEK[]"
            typ, fetch_data = imap.uid("fetch", uid.encode(), fetch_part)

            if typ != "OK" or not fetch_data or fetch_data[0] is None:
                raise RuntimeError(f"Message uid {uid} not found")

            raw = fetch_data[0][1]
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            text_part, html_part = self._extract_bodies(msg)

            return {
                "uid": uid,
                "from": msg.get("From", ""),
                "to": msg.get("To", ""),
                "cc": msg.get("Cc", ""),
                "subject": msg.get("Subject", ""),
                "date": msg.get("Date", ""),
                "text": text_part,
                "html": html_part,
            }

        finally:
            try:
                imap.logout()
            except Exception:
                pass

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        content_type: str = "plain",
    ) -> str:
        if content_type not in ("plain", "html"):
            raise ValueError("content_type must be 'plain' or 'html'")

        msg = EmailMessage()
        msg["From"] = self.email
        msg["To"] = to
        msg["Subject"] = subject

        subtype = "html" if content_type == "html" else "plain"
        msg.set_content(body, subtype=subtype)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(self.email, self.password)
            smtp.send_message(msg)

        return "sent"


def get_client() -> GmxClient:
    email_addr = os.environ.get("GMX_EMAIL")
    password = os.environ.get("GMX_PASSWORD")

    if not email_addr or not password:
        raise RuntimeError("GMX_EMAIL and GMX_PASSWORD must be set")

    return GmxClient(email_addr, password)


@mcp.tool
def list_messages(
    mailbox: str = "INBOX",
    limit: int = 10,
    unread_only: bool = False,
) -> List[Dict[str, str]]:
    """
    List recent messages from a GMX mailbox.

    Args:
        mailbox: mailbox name, for example INBOX.
        limit: maximum number of messages to return.
        unread_only: if true, only unread messages are returned.
    """
    return get_client().list_messages(
        mailbox=mailbox,
        limit=limit,
        unread_only=unread_only,
    )


@mcp.tool
def read_message(
    uid: str,
    mailbox: str = "INBOX",
    mark_seen: bool = False,
) -> Dict[str, Optional[str]]:
    """
    Read a specific GMX email by IMAP UID.

    Args:
        uid: IMAP UID of the message.
        mailbox: mailbox name, for example INBOX.
        mark_seen: if true, marks the message as read.
    """
    return get_client().read_message(
        uid=uid,
        mailbox=mailbox,
        mark_seen=mark_seen,
    )


@mcp.tool
def send_email(
    to: str,
    subject: str,
    body: str,
    content_type: str = "plain",
) -> str:
    """
    Send an email through GMX SMTP.

    Args:
        to: recipient email address.
        subject: subject of the email.
        body: email body.
        content_type: plain or html.
    """
    return get_client().send_email(
        to=to,
        subject=subject,
        body=body,
        content_type=content_type,
    )


@mcp.tool
def health_check() -> Dict[str, str]:
    """
    Simple health check for the GMX MCP server.
    """
    return {
        "status": "ok",
        "service": "gmx-mail-mcp",
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))

    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=port,
        path="/mcp",
    )
