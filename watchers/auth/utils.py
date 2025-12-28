from __future__ import annotations

from bs4 import BeautifulSoup


def extract_request_verification_token(html: bytes) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "__RequestVerificationToken"})
    if not token_input:
        return None

    token = token_input.get("value")
    return token if token else None

def extract_student_id(html: bytes) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one('table#tbl__PartialListStudent tbody tr a')
    if not link:
        return None

    href = link.get('href', '')
    try:
        student_id = href.split('studentID=')[-1]
        if not student_id:
            return None
        return student_id
    except Exception as e:
        return None