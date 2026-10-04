"""Тесты Celery-задачи экспорта XLSX."""

from io import BytesIO
from smtplib import SMTPException
from unittest.mock import patch

import pytest
from django.core import mail
from openpyxl import load_workbook

from voting.tasks import export_voting_results

pytestmark = pytest.mark.django_db


def _attachment_bytes(msg) -> bytes:
    assert len(msg.attachments) == 1, msg.attachments
    name, content, mimetype = msg.attachments[0]
    assert name.endswith(".xlsx")
    assert "spreadsheetml" in mimetype
    return content


def test_export_task_sends_email_with_correct_xlsx(
    finished_voting, settings, character, character2, set_votes
):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    set_votes(finished_voting, character, 5)   # Иванов — победитель
    set_votes(finished_voting, character2, 2)

    result = export_voting_results(finished_voting.id, "admin@example.com")

    assert result["status"] == "ok"
    assert result["recipient"] == "admin@example.com"
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert "Результаты голосования" in msg.subject

    content = _attachment_bytes(msg)
    wb = load_workbook(BytesIO(content))
    ws = wb.active

    # Собираем все строки в список.
    rows = [[cell.value for cell in row] for row in ws.iter_rows()]
    header = {row[0]: row[1] for row in rows if row and row[0] in {
        "Голосование:", "Статус:", "Всего голосов:", "Победители:",
    }}

    assert header["Статус:"] == "завершено"
    assert header["Всего голосов:"] == "7"
    assert header["Победители:"] == "Иванов Иван Иванович"

    # В таблице участников есть строка с 5 голосами и отметкой "Да".
    participant_rows = [
        row for row in rows
        if row and isinstance(row[0], int) and len(row) >= 7
    ]
    assert any(
        row[1] == "Иванов" and row[5] == 5 and row[6] == "Да"
        for row in participant_rows
    )


def test_export_task_returns_error_for_missing_voting(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    result = export_voting_results(99999, "admin@example.com")
    assert result["status"] == "error"
    assert "not found" in result["detail"]
    assert len(mail.outbox) == 0


def test_export_task_returns_error_when_send_fails(finished_voting, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    with patch("voting.tasks.EmailMessage.send", return_value=0):
        result = export_voting_results(finished_voting.id, "admin@example.com")
    assert result["status"] == "error"
    assert result["detail"] == "email not sent"


def test_export_task_retries_on_smtp_error(finished_voting, settings):
    """SMTPException → self.retry (в eager-режиме Celery поднимет Retry)."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    from celery.exceptions import Retry

    with patch(
        "voting.tasks.EmailMessage.send", side_effect=SMTPException("boom")
    ):
        with pytest.raises((Retry, SMTPException)):
            export_voting_results(finished_voting.id, "admin@example.com")