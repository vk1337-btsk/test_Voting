"""Management-команда для заполнения БД тестовыми данными.

Два режима:

* ``seed_data``          — идемпотентно создать/обновить данные;
* ``seed_data --flush``  — удалить всё, сбросить автоинкремент/sequence
  и создать заново.

Создаётся:

* суперпользователь ``admin / admin`` (если ещё нет);
* 8 персонажей;
* 10 голосований, покрывающих все ветки бизнес-логики
  (активные / завершённые / будущие, ничьи, нули,
  досрочное завершение по ``early_finish_votes``).

Сброс ID в ``--flush`` кроссплатформенный — через
``connection.ops.sequence_reset_sql``:

* SQLite       → ``DELETE FROM sqlite_sequence ...``
* PostgreSQL   → ``SELECT setval('..._id_seq', 1, false)``
* MySQL/MariaDB → ``ALTER TABLE ... AUTO_INCREMENT = 1``
* Oracle       → drop/create sequence
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.color import no_style
from django.db import connection, transaction
from django.db.models import Count, Model
from django.utils import timezone

from voting.models import Character, Vote, Voting, VotingCharacter


class Ch(StrEnum):
    """Символьные ключи персонажей"""

    IVANOV = "IVANOV"
    PETROV = "PETROV"
    SIDOROV = "SIDOROV"
    KUZNETSOVA = "KUZNETSOVA"
    SMIRNOV = "SMIRNOV"
    VOLKOVA = "VOLKOVA"
    MOROZOV = "MOROZOV"
    NOVIKOVA = "NOVIKOVA"


class Command(BaseCommand):
    help = "Заполняет БД тестовыми данными для ручной проверки и скриншотов"

    CHARACTERS: dict[Ch, tuple[str, str, str, int, str]] = {  # noqa: RUF012
        Ch.IVANOV: ("Иванов", "Иван", "Иванович", 30, "Инженер-программист"),
        Ch.PETROV: ("Петров", "Пётр", "Петрович", 40, "Дизайнер интерфейсов"),
        Ch.SIDOROV: ("Сидоров", "Сидор", "Сидорович", 25, "Аналитик данных"),
        Ch.KUZNETSOVA: ("Кузнецова", "Анна", "Сергеевна", 35, "Продакт-менеджер"),
        Ch.SMIRNOV: ("Смирнов", "Алексей", "Дмитриевич", 28, "QA-инженер"),
        Ch.VOLKOVA: ("Волкова", "Мария", "Ивановна", 32, "HR-специалист"),
        Ch.MOROZOV: ("Морозов", "Дмитрий", "Андреевич", 45, "CTO"),
        Ch.NOVIKOVA: ("Новикова", "Ольга", "Викторовна", 27, "DevOps-инженер"),
    }

    # ========= Аргументы =========
    def add_arguments(self, parser) -> None:

        parser.add_argument(
            "--flush",
            action="store_true",
            help=(
                "Удалить существующие данные и сбросить автоинкремент перед созданием тестовых объектов"
            ),
        )

    # ========= Точка входа =========
    def handle(self, *args, **options) -> None:

        # Ошибка, если забыли сделать migrate
        if Vote._meta.db_table not in connection.introspection.table_names():
            self.stderr.write(
                self.style.ERROR(
                    "Таблиц в БД нет. Сначала выполните: python src/manage.py migrate."
                )
            )
            return

        if options["flush"]:
            self._flush()

        # Транзакция — только на создание данных. DDL от MySQL
        with transaction.atomic():
            self._create_superuser()
            characters = self._create_characters()
            self._create_votings(characters)

        self.stdout.write(self.style.SUCCESS("Тестовые данные созданы."))

    def _flush(self) -> None:
        """Очистка и сброс последовательностей (всегда вместе)"""

        Voting.objects.all().delete()
        Character.objects.all().delete()

        self._reset_pk_sequences([Vote, Voting, VotingCharacter, Character])

        self.stdout.write(self.style.WARNING("Старые данные удалены, ID сброшены."))

    def _reset_pk_sequences(self, model_list: list[type[Model]]) -> None:
        """Кроссплатформенный сброс автоинкремента

        * SQLite      → DELETE FROM sqlite_sequence WHERE name IN (...)
        * PostgreSQL  → connection.ops.sequence_reset_sql(...)
        * MySQL/MariaDB → ALTER TABLE <t> AUTO_INCREMENT = 1
        * Oracle и др.  → не поддерживаем (это dev-команда)
        """
        table_names = [m._meta.db_table for m in model_list]

        if connection.vendor == "sqlite":
            # sqlite_sequence создаётся только после первого AUTOINCREMENT-INSERT
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='sqlite_sequence'"
                )
                if cursor.fetchone() is None:
                    return
                placeholders = ", ".join(["%s"] * len(table_names))
                cursor.execute(
                    f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                    table_names,
                )

        elif connection.vendor == "postgresql":
            sql_list = connection.ops.sequence_reset_sql(no_style(), model_list)
            with connection.cursor() as cursor:
                for sql in sql_list:
                    cursor.execute(sql)

        elif connection.vendor in ("mysql", "mariadb"):
            quote = connection.ops.quote_name
            with connection.cursor() as cursor:
                for table in table_names:
                    cursor.execute(f"ALTER TABLE {quote(table)} AUTO_INCREMENT = 1")

        else:  # pragma: no cover
            self.stdout.write(
                self.style.WARNING(
                    f"Сброс ID не поддержан для {connection.vendor}; пропускаю."
                )
            )

    def _create_superuser(self) -> None:
        """Создание админа"""

        User = get_user_model()
        if User.objects.filter(username="admin").exists():
            self.stdout.write("Суперпользователь admin уже существует.")
            return

        User.objects.create_superuser(  # type: ignore
            username="admin", email="admin@example.com", password="admin"
        )
        self.stdout.write(self.style.SUCCESS("Создан суперпользователь admin / admin"))

    def _create_characters(self) -> dict[Ch, Character]:
        """Создание персонажей"""

        chars: dict[Ch, Character] = {}

        for key, (last, first, patr, age, bio) in self.CHARACTERS.items():
            ch = Character.objects.filter(
                last_name=last, first_name=first, patronymic=patr
            ).first()

            if ch is None:
                ch = Character.objects.create(
                    last_name=last, first_name=first, patronymic=patr, age=age, bio=bio
                )

            else:
                ch.age = age
                ch.bio = bio
                ch.save(update_fields=["age", "bio"])
            chars[key] = ch
        self.stdout.write(f"Персонажей: {len(chars)}")

        return chars

    def _voting_specs(self, now) -> list[dict[str, Any]]:
        """Спецификация голосований"""

        return [
            # Активные
            {
                "title": "Лучший сотрудник месяца",
                "start": now - timedelta(days=1),
                "end": now + timedelta(days=7),
                "early_finish_votes": None,
                "characters": [Ch.IVANOV, Ch.PETROV, Ch.SIDOROV],
                "votes": {Ch.IVANOV: 2, Ch.PETROV: 3, Ch.SIDOROV: 1},
            },
            {
                "title": "Опрос без голосов",
                "start": now - timedelta(hours=2),
                "end": now + timedelta(days=3),
                "early_finish_votes": None,
                "characters": [Ch.MOROZOV, Ch.NOVIKOVA],
                "votes": {},
            },
            {
                "title": "Утренний опрос (скоро закроется)",
                "start": now - timedelta(hours=1),
                "end": now + timedelta(minutes=30),
                "early_finish_votes": None,
                "characters": [Ch.KUZNETSOVA, Ch.SMIRNOV],
                "votes": {Ch.KUZNETSOVA: 3, Ch.SMIRNOV: 2},
            },
            {
                "title": "Опрос с порогом (порог не достигнут)",
                "start": now - timedelta(hours=3),
                "end": now + timedelta(days=2),
                "early_finish_votes": 10,
                "characters": [Ch.SIDOROV, Ch.NOVIKOVA],
                "votes": {Ch.SIDOROV: 2, Ch.NOVIKOVA: 1},
            },
            # Завершённые
            {
                "title": "Прошлогодний конкурс талантов",
                "start": now - timedelta(days=30),
                "end": now - timedelta(days=10),
                "early_finish_votes": None,
                "characters": [Ch.IVANOV, Ch.KUZNETSOVA, Ch.VOLKOVA],
                "votes": {Ch.IVANOV: 1, Ch.KUZNETSOVA: 7, Ch.VOLKOVA: 5},
            },
            {
                "title": "Летний конкурс (ничья)",
                "start": now - timedelta(days=60),
                "end": now - timedelta(days=45),
                "early_finish_votes": None,
                "characters": [Ch.IVANOV, Ch.VOLKOVA, Ch.MOROZOV],
                "votes": {Ch.IVANOV: 4, Ch.VOLKOVA: 4, Ch.MOROZOV: 1},
            },
            {
                "title": "Хакатон (досрочное завершение)",
                "start": now - timedelta(days=2),
                "end": now + timedelta(days=5),
                "early_finish_votes": 3,
                "characters": [Ch.VOLKOVA, Ch.MOROZOV, Ch.NOVIKOVA],
                "votes": {Ch.VOLKOVA: 1, Ch.MOROZOV: 3, Ch.NOVIKOVA: 0},
            },
            {
                "title": "Завершённый опрос без голосов",
                "start": now - timedelta(days=15),
                "end": now - timedelta(days=5),
                "early_finish_votes": None,
                "characters": [Ch.MOROZOV, Ch.NOVIKOVA],
            },
            # Будущие
            {
                "title": "Новогодний конкурс (ещё не началось)",
                "start": now + timedelta(days=10),
                "end": now + timedelta(days=20),
                "early_finish_votes": None,
                "characters": [Ch.IVANOV, Ch.PETROV, Ch.SIDOROV],
            },
            {
                "title": "Завтрашний опрос (стартует вот-вот)",
                "start": now + timedelta(minutes=1),
                "end": now + timedelta(days=1),
                "early_finish_votes": None,
                "characters": [Ch.SMIRNOV, Ch.MOROZOV],
            },
        ]

    def _create_votings(self, characters: dict[Ch, Character]) -> list[Voting]:
        """Создание голосований"""

        now = timezone.now()
        votings: list[Voting] = []

        for spec in self._voting_specs(now):
            voting = Voting.objects.filter(title=spec["title"]).first()

            if voting is None:
                voting = Voting.objects.create(
                    title=spec["title"],
                    start_date=spec["start"],
                    end_date=spec["end"],
                    early_finish_votes=spec["early_finish_votes"],
                )

            else:
                voting.start_date = spec["start"]
                voting.end_date = spec["end"]
                voting.early_finish_votes = spec["early_finish_votes"]
                voting.save(
                    update_fields=["start_date", "end_date", "early_finish_votes"]
                )

            self._sync_characters(voting, characters, spec["characters"])
            self._sync_seed_votes(voting, characters, spec.get("votes") or {})
            self._reconcile_votes_count(voting)

            votes_repr = spec.get("votes") or {}
            self.stdout.write(
                f"  «{voting.title}»: "
                + (
                    ", ".join(
                        f"{characters[key]}={cnt}"
                        for key, cnt in votes_repr.items()
                        if cnt > 0
                    )
                    or "(без голосов)"
                )
            )
            votings.append(voting)

        self.stdout.write(f"Голосований: {len(votings)}")
        return votings

    def _sync_characters(
        self, voting: Voting, characters: dict[Ch, Character], keys: list[Ch]
    ) -> None:
        """Синхронизация состава персонажей голосования"""

        target_pks = {characters[key].pk for key in keys}

        Vote.objects.filter(voting=voting).exclude(character_id__in=target_pks).delete()
        VotingCharacter.objects.filter(voting=voting).exclude(
            character_id__in=target_pks
        ).delete()
        for key in keys:
            VotingCharacter.objects.get_or_create(
                voting=voting, character=characters[key]
            )

    def _sync_seed_votes(
        self, voting: Voting, characters: dict[Ch, Character], votes: dict[Ch, int]
    ) -> None:
        """Синхронизация голосований"""

        Vote.objects.filter(voting=voting, client_id__startswith="seed-").delete()
        if not votes:
            return

        slot = 0
        for key, count in votes.items():
            if count <= 0:
                continue

            character = characters.get(key)
            if character is None:
                self.stdout.write(
                    self.style.WARNING(f"Неизвестный персонаж {key}, пропуск.")
                )
                continue

            vc = VotingCharacter.objects.filter(
                voting=voting, character=character
            ).first()
            if vc is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Персонаж {character} не участвует в «{voting.title}», пропуск."
                    )
                )
                continue

            for _ in range(count):
                ip = f"10.{voting.pk % 256}.{(slot // 256) % 256}.{slot % 256}"
                ua = f"seed-agent/{voting.pk}"
                cid = f"seed-{voting.pk}-{slot}"
                fp = Voting.build_fingerprint(ip, ua, cid)
                slot += 1

                Vote.objects.create(
                    voting=voting,
                    character=character,
                    voter_fingerprint=fp,
                    ip_address=ip,
                    user_agent=ua,
                    client_id=cid,
                )

    def _reconcile_votes_count(self, voting: Voting) -> None:

        counts = dict(
            Vote.objects.filter(voting=voting)
            .values("character_id")
            .annotate(c=Count("id"))
            .values_list("character_id", "c")
        )

        for vc in voting.voting_characters.all(): # type: ignore
            actual = counts.get(vc.character_id, 0)
            if vc.votes_count != actual:
                VotingCharacter.objects.filter(pk=vc.pk).update(votes_count=actual)
