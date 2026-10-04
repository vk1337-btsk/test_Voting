"""Celery-задачи: экспорт результатов голосования в XLSX."""

from io import BytesIO
from smtplib import SMTPException

from celery import shared_task
from celery.utils.log import get_task_logger
from django.core.mail import EmailMessage
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from .models import Voting

logger = get_task_logger(__name__)


def _voting_status_text(voting: Voting) -> str:
    """Единственное место, где вычисляется статус для экспорта.

    Возвращает одно из: ``запланировано``, ``активно``, ``завершено``.
    Один SQL-запрос (``exists()``) на проверку досрочного завершения.
    """
    now = timezone.now()
    if now < voting.start_date:
        return "запланировано"

    early_finish = voting.early_finish_votes
    has_early = bool(early_finish) and voting.voting_characters.filter(
        votes_count__gte=early_finish
    ).exists()
    if now > voting.end_date or has_early:
        return "завершено"
    return "активно"


def _format_period(voting: Voting) -> str:
    """Период голосования в локальной TZ (а не захардкоженный UTC)."""
    fmt = "%d.%m.%Y %H:%M"
    start = timezone.localtime(voting.start_date)
    end = timezone.localtime(voting.end_date)
    return f"{start.strftime(fmt)} - {end.strftime(fmt)} {timezone.get_current_timezone_name()}"


def _format_early_finish(voting: Voting) -> str:
    return str(voting.early_finish_votes) if voting.early_finish_votes else "не задан"


def _build_workbook(voting: Voting, status_text: str) -> BytesIO:
    """Собирает xlsx-книгу. Статус приходит снаружи — не считаем его повторно."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Результаты"

    vcs = list(
        voting.voting_characters.select_related("character").order_by(
            "-votes_count",
            "character__last_name",
            "character__first_name",
            "character__patronymic",
        )
    )
    total_votes = sum(vc.votes_count for vc in vcs)
    max_votes = max((vc.votes_count for vc in vcs), default=0)

    # Победители — только для завершённого голосования и только при max_votes > 0.
    if status_text == "завершено" and max_votes > 0:
        winners_ids = {vc.character.id for vc in vcs if vc.votes_count == max_votes}
    else:
        winners_ids = set()

    _write_header(ws, voting, vcs, winners_ids, total_votes, status=status_text)
    _write_participants_table(ws, vcs, winners_ids)
    _autosize_columns(ws)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def _write_header(ws, voting, vcs, winners_ids, total_votes, *, status: str) -> None:
    if winners_ids:
        winners_text = ", ".join(
            str(vc.character) for vc in vcs if vc.character.id in winners_ids
        )
    else:
        winners_text = "-"

    rows = [
        ("Голосование:", voting.title),
        ("Период:", _format_period(voting)),
        ("Статус:", status),
        ("Порог досрочного завершения:", _format_early_finish(voting)),
        ("Всего голосов:", str(total_votes)),
        ("Победители:", winners_text),
    ]
    for label, value in rows:
        ws.append([label, value])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    ws.append([])


def _write_participants_table(ws, vcs, winners_ids) -> None:
    headers = ["Место", "Фамилия", "Имя", "Отчество", "Возраст", "Голосов", "Победитель"]
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    place = 0
    last_votes = None
    for idx, vc in enumerate(vcs, start=1):
        ch = vc.character
        if vc.votes_count != last_votes:
            place = idx
            last_votes = vc.votes_count
        ws.append(
            [
                place,
                ch.last_name,
                ch.first_name,
                ch.patronymic,
                ch.age,
                vc.votes_count,
                "Да" if ch.id in winners_ids else "",
            ]
        )


def _autosize_columns(ws) -> None:
    for column_cells in ws.columns:
        length = max(
            (len(str(c.value)) for c in column_cells if c.value is not None),
            default=8,
        )
        col_letter = get_column_letter(column_cells[0].column)
        ws.column_dimensions[col_letter].width = max(14, length + 2)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def export_voting_results(self, voting_id: int, recipient_email: str) -> dict:
    """Формирует XLSX и отправляет письмо. Возвращает статус выполнения."""
    try:
        voting = Voting.objects.get(pk=voting_id)
    except Voting.DoesNotExist:
        logger.warning("Voting %s not found, skip export", voting_id)
        return {"status": "error", "detail": f"Voting {voting_id} not found"}

    status_text = _voting_status_text(voting)
    buffer = _build_workbook(voting, status_text=status_text)
    filename = f"voting_{voting.id}_results_{timezone.now():%Y%m%d_%H%M%S}.xlsx"

    email = EmailMessage(
        subject=f"Результаты голосования «{voting.title}»",
        body=(
            f"Файл с результатами голосования «{voting.title}» во вложении.\n\n"
            f"Статус: {status_text}\n"
            f"Период: {_format_period(voting)}"
        ),
        to=[recipient_email],
    )
    email.attach(
        filename,
        buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    try:
        sent = email.send()
    except SMTPException as exc:
        logger.exception("SMTP error while sending export for voting %s", voting_id)
        # Retry с исходным исключением — Celery сохранит трейсбек и попробует снова.
        raise self.retry(exc=exc)

    if sent != 1:
        logger.error("Email backend returned %s for voting %s", sent, voting_id)
        return {"status": "error", "detail": "email not sent"}

    logger.info("Export for voting %s sent to %s", voting_id, recipient_email)
    return {
        "status": "ok",
        "recipient": recipient_email,
        "filename": filename,
    }