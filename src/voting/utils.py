from django.conf import settings
from django.db import IntegrityError
from django.db.models import IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce


def get_client_ip(request) -> str:
    """Возвращает IP клиента.

    XFF: "<client>, <proxy1>, <proxy2>...". При TRUSTED_PROXY_COUNT = N
    реальный клиент — это N-й элемент справа (parts[-N]): N прокси
    «съедают» N-1 значений, оставляя клиента на позиции -N.

    Если XFF короче, чем число доверенных прокси, — заголовку не доверяем:
    откатываемся к REMOTE_ADDR (это IP прямого peer'а; если прокси
    настроен корректно, это адрес прокси, а не подделка).
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    trusted = getattr(settings, "TRUSTED_PROXY_COUNT", 0)

    if xff and trusted > 0:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= trusted:
            return parts[-trusted]
        # XFF короче, чем ожидаемое число доверенных прокси —
        # не доверяем левым (подделываемым) значениям.
        return request.META.get("REMOTE_ADDR", "")

    return request.META.get("REMOTE_ADDR", "")


def annotate_votes_count(characters_qs, voting):
    """Аннотирует кверисет персонажей полем `_votes_count` для указанного
    голосования. Используется в сериализаторах — снимает N+1."""
    from .models import VotingCharacter

    subquery = VotingCharacter.objects.filter(
        voting=voting, character=OuterRef("pk")
    ).values("votes_count")[:1]

    return characters_qs.annotate(
        _votes_count=Coalesce(Subquery(subquery, output_field=IntegerField()), Value(0))
    )


def _is_unique_vote_violation(exc: IntegrityError) -> bool:
    """True, если IntegrityError — нарушение UNIQUE(voting, voter_fingerprint).

    Проверяем по имени констрейнта / полю, а не «на глаз»: иначе любая
    ошибка целостности (например, слишком длинный voter_fingerprint)
    превращалась бы в «Вы уже голосовали».
    """
    cause = exc.__cause__

    # PostgreSQL (psycopg2/3): UniqueViolation, SQLSTATE 23505
    pgcode = getattr(cause, "pgcode", None) or getattr(cause, "sqlstate", None)
    if pgcode == "23505":
        diag = getattr(cause, "diag", None)
        name = getattr(diag, "constraint_name", "") if diag else ""
        return name == "unique_vote_per_voter_per_voting"

    # SQLite: message вида
    # "UNIQUE constraint failed: voting_vote.voting_id, voting_vote.voter_fingerprint"
    text = str(exc).lower()
    if cause is not None:
        text += " " + str(cause).lower()
    return "unique" in text and (
        "voter_fingerprint" in text or "unique_vote_per_voter_per_voting" in text
    )
