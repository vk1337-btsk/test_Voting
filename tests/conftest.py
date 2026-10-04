"""Общие фикстуры для тестов приложения."""

from datetime import timedelta

import pytest
from django.db.models import F
from django.utils import timezone

from voting.models import Character, Vote, Voting, VotingCharacter


@pytest.fixture
def character(db) -> Character:
    return Character.objects.create(
        last_name="Иванов",
        first_name="Иван",
        patronymic="Иванович",
        age=30,
        bio="Тестовый персонаж",
    )


@pytest.fixture
def character2(db) -> Character:
    return Character.objects.create(
        last_name="Петров",
        first_name="Пётр",
        patronymic="Петрович",
        age=40,
        bio="Второй персонаж",
    )


@pytest.fixture
def active_voting(db, character, character2) -> Voting:
    now = timezone.now()
    voting = Voting.objects.create(
        title="Активное голосование",
        start_date=now - timedelta(hours=1),
        end_date=now + timedelta(hours=1),
    )
    voting.characters.set([character, character2])
    return voting


@pytest.fixture
def finished_voting(db, character, character2) -> Voting:
    now = timezone.now()
    voting = Voting.objects.create(
        title="Завершённое голосование",
        start_date=now - timedelta(days=2),
        end_date=now - timedelta(days=1),
    )
    voting.characters.set([character, character2])
    return voting


@pytest.fixture
def future_voting(db, character, character2) -> Voting:
    now = timezone.now()
    voting = Voting.objects.create(
        title="Будущее голосование",
        start_date=now + timedelta(days=1),
        end_date=now + timedelta(days=2),
    )
    voting.characters.set([character, character2])
    return voting


@pytest.fixture
def early_finish_voting(db, character, character2) -> Voting:
    now = timezone.now()
    voting = Voting.objects.create(
        title="Голосование с ранним завершением",
        start_date=now - timedelta(hours=1),
        end_date=now + timedelta(hours=1),
        early_finish_votes=3,
    )
    voting.characters.set([character, character2])
    return voting


@pytest.fixture
def set_votes(db):
    """Возвращает callable, который «назначает» количество голосов.

    Меняет только счётчик ``VotingCharacter.votes_count`` без создания
    строк ``Vote``. Подходит для тестов, где важен только результат
    голосования, а не журнал голосов.
    """

    def _set(voting: Voting, character: Character, count: int) -> None:
        VotingCharacter.objects.filter(voting=voting, character=character).update(
            votes_count=count
        )

    return _set


@pytest.fixture
def cast_votes(db):
    """Возвращает callable, который «наливает» N голосов.

    Создаёт реальные ``Vote`` (с разными fingerprint'ами) и увеличивает
    ``VotingCharacter.votes_count`` через ``F()``.
    """

    def _cast(
        voting: Voting,
        character: Character,
        count: int,
        *,
        prefix: str = "v",
    ) -> None:
        for i in range(count):
            ip = f"10.0.0.{i % 256}"
            ua = f"ua-{prefix}"
            cid = f"{prefix}-{i}"
            fp = Voting.build_fingerprint(ip, ua, cid)
            Vote.objects.create(
                voting=voting,
                character=character,
                voter_fingerprint=fp,
                ip_address=ip,
                user_agent=ua,
                client_id=cid,
            )
        VotingCharacter.objects.filter(voting=voting, character=character).update(
            votes_count=F("votes_count") + count
        )

    return _cast