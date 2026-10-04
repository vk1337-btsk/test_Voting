from django.contrib import admin, messages
from django.db.models import Exists, OuterRef, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from .models import Character, Vote, Voting, VotingCharacter
from .tasks import export_voting_results


@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    """Персонаж-кандидат"""

    list_display = ("last_name", "first_name", "patronymic", "age")
    search_fields = ("last_name", "first_name", "patronymic")


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    """Голос — только для просмотра и аудита

    Ни создавать, ни редактировать голоса через админку нельзя.
    Использовать как журнал для разбора инцидентов с накруткой.
    """

    list_display = ("voting", "character", "ip_address", "created_at")
    list_filter = ("voting",)
    list_select_related = ("voting", "character")
    readonly_fields = (
        "voting",
        "character",
        "ip_address",
        "user_agent",
        "client_id",
        "voter_fingerprint",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class VotingCharacterInline(admin.TabularInline):
    model = VotingCharacter
    extra = 0
    readonly_fields = ("votes_count",)
    can_delete = False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Voting)
class VotingAdmin(admin.ModelAdmin):
    """Управление голосованиями.

    К стандартным операциям CRUD добавляем action **Скачать XLSX**.
    Требует права ``voting.can_export_voting_results``.
    Выгрузка идёт асинхронно через Celery; файл приходит письмом с вложением.
    """

    list_filter = ("start_date", "end_date")
    search_fields = ("title",)
    actions = ("export_results_action",)
    inlines = (VotingCharacterInline,)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_total_votes=Sum("voting_characters__votes_count"))
            .annotate(
                _has_early_finish=Exists(
                    VotingCharacter.objects.filter(
                        voting=OuterRef("pk"),
                        votes_count__gte=OuterRef("early_finish_votes"),
                    )
                )
            )
        )

    @admin.display(boolean=True, description="Активно")
    def is_active_display(self, obj: Voting) -> bool:
        return obj.is_active

    @admin.display(description="Всего голосов", ordering="_total_votes")
    def total_votes(self, obj: Voting) -> int:
        return getattr(obj, "_total_votes", None) or 0

    def get_list_display(self, request):
        """Ссылка на экспорт рендерится только при наличии права.

        Замыкаем ``request`` — это безопаснее thread-local.
        """
        show_export = request.user.has_perm("voting.can_export_voting_results")

        @admin.display(description="Экспорт")
        def export_link(obj: Voting) -> str:
            if not show_export:
                return "—"
            url = reverse("admin:voting_export_results", args=[obj.pk])
            return format_html('<a class="button" href="{}">Скачать XLSX</a>', url)

        return (
            "title",
            "start_date",
            "end_date",
            "early_finish_votes",
            "is_active_display",
            "total_votes",
            export_link,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:pk>/export/",
                self.admin_site.admin_view(self.export_view),
                name="voting_export_results",
            ),
        ]
        return custom + urls

    def _check_export_permission(self, request) -> bool:
        if not request.user.has_perm("voting.can_export_voting_results"):
            self.message_user(
                request,
                "Недостаточно прав для экспорта.",
                level=messages.ERROR,
            )
            return False
        return True

    def _get_recipient_or_warn(self, request) -> str | None:
        recipient = request.user.email
        if not recipient:
            self.message_user(
                request,
                "У вашей учётной записи не указан email — экспорт не запущен. "
                "Укажите email в профиле и попробуйте снова.",
                level=messages.WARNING,
            )
            return None
        return recipient

    @admin.action(description="Экспортировать результаты в XLSX")
    def export_results_action(self, request, queryset):
        if not self._check_export_permission(request):
            return
        recipient = self._get_recipient_or_warn(request)
        if not recipient:
            return

        votings = list(queryset)
        for voting in votings:
            export_voting_results.delay(voting.pk, recipient)  # type: ignore[attr-defined]

        self.message_user(
            request,
            f"Запущен экспорт {len(votings)} голосований. Файлы придут на {recipient}.",
            level=messages.SUCCESS,
        )

    def export_view(self, request, pk: int):
        """Кнопка на конкретном голосовании"""

        changelist_url = reverse("admin:voting_voting_changelist")

        if not self._check_export_permission(request):
            return HttpResponseRedirect(changelist_url)

        voting = get_object_or_404(Voting, pk=pk)

        recipient = self._get_recipient_or_warn(request)
        if not recipient:
            return HttpResponseRedirect(changelist_url)

        export_voting_results.delay(voting.pk, recipient)  # type: ignore[attr-defined]

        self.message_user(
            request,
            f"Задача на экспорт голосования «{voting.title}» запущена. Файл придёт на {recipient}.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(changelist_url)
