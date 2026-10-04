import hashlib
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, connection, models, transaction
from django.db.models import F, Max
from django.utils import timezone

from voting.utils import _is_unique_vote_violation


class Character(models.Model):
    """Модель персонажа"""

    # Поля
    last_name = models.CharField("Фамилия", max_length=100)
    first_name = models.CharField("Имя", max_length=100)
    patronymic = models.CharField("Отчество", max_length=100, blank=True)
    photo = models.ImageField(
        "Фотография", upload_to="characters/", blank=True, null=True
    )
    age = models.PositiveIntegerField("Возраст")
    bio = models.TextField("Биография", blank=True)

    class Meta:
        verbose_name = "Персонаж"
        verbose_name_plural = "Персонажи"
        ordering = ("last_name", "first_name", "patronymic")

    def __str__(self) -> str:
        fio = f"{self.last_name} {self.first_name} {self.patronymic}".strip()
        return fio or f"Персонаж № {self.pk}"


class Voting(models.Model):
    """Модель голосования"""

    # Поля
    title = models.CharField("Название", max_length=255)
    start_date = models.DateTimeField("Дата и время начала")
    end_date = models.DateTimeField("Дата и время окончания")
    early_finish_votes = models.PositiveIntegerField(
        "Кол-во голосов для досрочного завершения",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )

    # Связи
    characters = models.ManyToManyField(
        Character,
        through="VotingCharacter",
        related_name="votings",
        verbose_name="Персонажи",
    )

    class Meta:
        verbose_name = "Голосование"
        verbose_name_plural = "Голосования"
        ordering = ("-start_date", "title")
        permissions = (
            ("can_export_voting_results", "Может экспортировать результаты в XLSX"),
        )
        constraints = (
            models.CheckConstraint(
                condition=models.Q(early_finish_votes__isnull=True)
                | models.Q(early_finish_votes__gt=0),
                name="early_finish_positive_or_null",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="voting_end_after_start",
            ),
        )

    def __str__(self) -> str:
        return self.title

    # =========== Статусы голосования ===========
    @property
    def is_upcoming(self) -> bool:
        """Голосование ещё не началось"""

        return timezone.now() < self.start_date

    @property
    def has_early_finish_reached(self) -> bool:
        """Кто-то набрал порог досрочного завершения.

        Если viewset/админка аннотировали объект ``_has_early_finish`` —
        используем аннотацию (0 запросов). Иначе — один ``exists()``
        вместо итерации по всем ``voting_characters``.
        """

        if not self.early_finish_votes or self.early_finish_votes <= 0:
            return False

        annotated = getattr(self, "_has_early_finish", None)

        if annotated is not None:
            return bool(annotated)

        return self.voting_characters.filter(  # type: ignore
            votes_count__gte=self.early_finish_votes,
        ).exists()

    @property
    def is_finished(self) -> bool:
        """Голосование завершено: время вышло ИЛИ достигнут порог досрочного завершения"""

        if timezone.now() > self.end_date:
            return True

        return self.has_early_finish_reached

    @property
    def is_active(self) -> bool:
        """Голосование идёт прямо сейчас"""

        now = timezone.now()
        if not (self.start_date < now <= self.end_date):
            return False
        return not self.has_early_finish_reached

    def get_winners(self):
        """Получить персонажей-победителей голосования (только для завершённого голосования)"""

        if not self.is_finished:
            return Character.objects.none()

        max_votes = self.voting_characters.aggregate(m=Max("votes_count"))["m"]  # type: ignore
        if max_votes is None or max_votes == 0:
            # Никто не голосовал — победителей нет
            return Character.objects.none()

        return Character.objects.filter(
            voting_characters__voting=self,
            voting_characters__votes_count=max_votes,
        )

    @staticmethod
    def build_fingerprint(ip: str, user_agent: str, client_id: str = "") -> str:
        """Формируем отпечаток голосующего:
        комбинируем IP + User-Agent + опциональный клиентский идентификатор (X-Client-Id),
        который в должен хранить фронт в localStorage. Так мы предупреждаем случаи,
        когда один и тот же пользователь не сможет проголосовать дважды ни через смену cookies,
        ни через новую сессию"""

        raw = f"{(ip or '').strip()}|{(user_agent or '').strip()}|{(client_id or '').strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @transaction.atomic
    def cast_vote(
        self,
        character: Character,
        *,
        ip: str,
        user_agent: str,
        client_id: str = "",
    ) -> Character:
        """Отдать голос за персонажа.

        Блокировка:
        * PostgreSQL — реальный row lock через SELECT ... FOR UPDATE.
        * SQLite — SELECT ... FOR UPDATE игнорируется; SQLite сериализует
        записи на уровне БД (WAL + busy_timeout). Для прод-нагрузки используйте PostgreSQL
        """

        qs = Voting.objects.filter(pk=self.pk)
        if connection.features.has_select_for_update:
            qs = qs.select_for_update()
        voting = qs.get()

        if not voting.is_active:
            raise ValidationError("Голосование неактивно.")

        vc_qs = VotingCharacter.objects.filter(voting=voting, character=character)
        if connection.features.has_select_for_update:
            vc_qs = vc_qs.select_for_update()

        vc = vc_qs.first()
        if vc is None:
            raise ValidationError("Этот персонаж не участвует в голосовании.")

        fingerprint = self.build_fingerprint(ip, user_agent, client_id)

        try:
            Vote.objects.create(
                voting=voting,
                character=character,
                voter_fingerprint=fingerprint,
                ip_address=ip or None,
                user_agent=(user_agent or "")[:255],
                client_id=(client_id or "")[:128],
            )
        except IntegrityError as exc:
            if _is_unique_vote_violation(exc):
                raise ValidationError("Вы уже голосовали в этом голосовании.") from exc
            raise  # все прочие IntegrityError — наверх, пусть будет 500 и лог

        VotingCharacter.objects.filter(pk=vc.pk).update(
            votes_count=F("votes_count") + 1
        )
        return character

    def clean(self) -> None:
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError(
                {"end_date": "Дата окончания должна быть позже даты начала."}
            )

    voting_characters = None


class VotingCharacter(models.Model):
    """Связь «голосование–персонаж» со счётчиком голосов"""

    voting = models.ForeignKey(
        Voting, on_delete=models.CASCADE, related_name="voting_characters"
    )
    character = models.ForeignKey(
        Character, on_delete=models.CASCADE, related_name="voting_characters"
    )
    votes_count = models.PositiveIntegerField("Количество голосов", default=0)

    class Meta:
        verbose_name = "Персонаж голосования"
        verbose_name_plural = "Персонажи голосований"
        constraints = (
            models.UniqueConstraint(
                fields=("voting", "character"), name="unique_voting_character"
            ),
        )

    def __str__(self) -> str:
        return f"{self.voting} — {self.character} ({self.votes_count})"


class Vote(models.Model):
    """Техническая модель для защиты от накрутки голосов"""

    if TYPE_CHECKING:
        voting_id: int
        character_id: int

    # Поля
    ip_address = models.GenericIPAddressField("IP адрес", null=True, blank=True)
    user_agent = models.CharField("User-Agent", max_length=255, blank=True)
    client_id = models.CharField(
        "Клиентский ID",
        max_length=128,
        blank=True,
        help_text="Опциональный идентификатор из localStorage",
    )
    voter_fingerprint = models.CharField(
        "Отпечаток голосующего",
        max_length=64,
        help_text="sha256(IP + User-Agent + X-Client-Id)",
    )
    created_at = models.DateTimeField("Дата и время голосования", auto_now_add=True)

    # Связи
    voting = models.ForeignKey(Voting, on_delete=models.CASCADE, related_name="votes")
    character = models.ForeignKey(
        Character, on_delete=models.CASCADE, related_name="votes"
    )

    class Meta:
        verbose_name = "Голос"
        verbose_name_plural = "Голоса"
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("voting", "voter_fingerprint"),
                name="unique_vote_per_voter_per_voting",
            ),
        )
        indexes = [models.Index(fields=("voting", "character"))]  # noqa: RUF012

    def __str__(self) -> str:
        return f"Голос ({int(self.voting_id)}, {int(self.character_id)})"
