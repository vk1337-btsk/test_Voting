from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import (
    BooleanField,
    Case,
    Exists,
    Max,
    OuterRef,
    Q,
    Value,
    When,
)
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .filters import VotingFilter
from .models import Character, Voting, VotingCharacter
from .serializers import (
    CharacterSerializer,
    ErrorSerializer,
    VoteRequestSerializer,
    VotingDetailSerializer,
    VotingListSerializer,
)
from .throttling import VoteThrottle
from .utils import annotate_votes_count, get_client_ip


class VotingViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """ViewSet голосования: список, детали, участники, победители, произвести голосование"""

    filterset_class = VotingFilter
    authentication_classes = []
    permission_classes = [AllowAny]  # noqa: RUF012
    queryset = Voting.objects.all()

    def get_serializer_class(self):
        """Разный сериализатор для списка и деталей: retrieve тянет вложенных персонажей, а список нет"""

        if self.action == "retrieve":
            return VotingDetailSerializer
        return VotingListSerializer

    def filter_queryset(self, queryset):
        # DjangoFilterBackend применяется только к list.
        # На detail/vote/winners/characters get_object() вызывает
        # filter_queryset — там ?status= не должен ломать выборку по pk.
        if self.action != "list":
            return queryset
        return super().filter_queryset(queryset)

    def get_queryset(self):
        """Получить queryset в зависимости от параметров query"""

        now = timezone.now()
        qs = (
            super()
            .get_queryset()
            .annotate(
                _has_early_finish=Exists(
                    VotingCharacter.objects.filter(
                        voting=OuterRef("pk"),
                        votes_count__gte=OuterRef("early_finish_votes"),
                    )
                ),
            )
            .annotate(
                _is_finished=Case(
                    When(end_date__lt=now, then=Value(True)),
                    When(_has_early_finish=True, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
                _is_upcoming=Case(
                    When(start_date__gt=now, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
                _is_active=Case(
                    When(
                        Q(start_date__lt=now)
                        & Q(end_date__gte=now)
                        & Q(_has_early_finish=False),
                        then=Value(True),
                    ),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
            )
        )
        return qs.order_by("-_is_active", "start_date", "end_date")

    #
    #
    # =======================================
    # GET /api/votings/
    # =======================================
    @extend_schema(
        tags=["votings"],
        summary="Список голосований",
        description=(
            "Постраничный список голосований с опциональным фильтром по статусу.\n\n"
            "**Статусы голосования:**\n"
            "- `active` - идёт сейчас и никто не набрал `early_finish_votes`\n"
            "- `finished` - время ещё не наступило, уже прошло, либо кто-то набрал `early_finish_votes`\n\n"
            "**Порядок:** сначала активные, затем завершённые; внутри группы - "
            "по `start_date`, затем по `end_date` (по возрастанию).\n\n"
            "**Пагинация:** в ответе всегда объект `{count, next, previous, results}`. "
            "Управляется параметрами `page` и `page_size`."
        ),
        parameters=[
            OpenApiParameter(
                name="status",
                type=str,
                location=OpenApiParameter.QUERY,
                enum=["active", "finished", "upcoming"],
                required=False,
                description="Фильтр по статусу. Если не задан или неизвестен - возвращаются все голосования.",
            ),
            OpenApiParameter(
                name="page",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Номер страницы. По умолчанию 1.",
            ),
            OpenApiParameter(
                name="page_size",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Размер страницы, максимум 100. По умолчанию 20. "
                "Если не задан или задан неверно - применяется размер по умолчанию.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=VotingListSerializer(many=True),
                description="Страница голосований.",
            ),
            404: OpenApiResponse(
                response=ErrorSerializer,
                description="Страница не найдена или не существует.",
            ),
        },
    )
    def list(self, request, *args, **kwargs):
        """Список голосований: GET /api/votings/?status=active|finished"""

        return super().list(request, *args, **kwargs)

    #
    #
    # =======================================
    # GET /api/votings/{id}/
    # =======================================
    @extend_schema(
        tags=["votings"],
        summary="Детали голосования",
        description="Карточка голосования вместе со списком участников.",
        responses={
            200: OpenApiResponse(
                response=VotingDetailSerializer,
                description="Голосование с вложенными `characters`.",
            ),
            404: OpenApiResponse(
                response=ErrorSerializer,
                description="Голосование с таким `id` не найдено.",
            ),
        },
    )
    def retrieve(self, request, *args, **kwargs):
        """Детали голосования: GET /api/votings/{id}"""

        return super().retrieve(request, *args, **kwargs)

    #
    #
    # =======================================
    # GET /api/votings/{id}/characters/
    # =======================================
    @extend_schema(
        tags=["votings"],
        summary="Участники голосования",
        description=(
            "Список персонажей, участвующих в голосовании. "
            "Отсортирован по числу голосов (по убыванию), при равенстве - по ФИО."
        ),
        responses={
            200: OpenApiResponse(
                response=CharacterSerializer(many=True),
                description="Участники с актуальным `number_votes`.",
            ),
            404: OpenApiResponse(
                response=ErrorSerializer,
                description="Голосование не найдено или не существует.",
            ),
        },
    )
    @action(detail=True, methods=["get"], url_path="characters")
    def characters(self, request, pk=None):
        """Участники голосования: GET /api/votings/{id}/characters"""

        voting = self.get_object()
        qs = annotate_votes_count(
            Character.objects.filter(voting_characters__voting=voting),
            voting,
        ).order_by(
            "-_votes_count",
            "last_name",
            "first_name",
            "patronymic",
        )
        return Response(
            CharacterSerializer(qs, many=True, context={"request": request}).data
        )

    #
    #
    # =======================================
    # GET /api/votings/{id}/winners/
    # =======================================
    @extend_schema(
        tags=["votings"],
        summary="Победители голосования",
        description=(
            "Персонажи с максимальным числом голосов.\n\n"
            "Доступно **только для завершённых** голосований. "
            "Если максимум разделён между несколькими персонажами - вернётся массив из нескольких записей. "
            "Если у всех по 0 голосов - вернётся пустой массив."
        ),
        responses={
            200: OpenApiResponse(
                response=CharacterSerializer(many=True),
                description="Массив победителей (возможно, пустой или из нескольких человек).",
            ),
            400: OpenApiResponse(
                response=ErrorSerializer,
                description="Голосование ещё активно.",
                examples=[
                    OpenApiExample(
                        "not_finished",
                        value={"detail": "Голосование ещё не завершено."},
                    ),
                ],
            ),
            404: OpenApiResponse(
                response=ErrorSerializer,
                description="Голосование не найдено или не существует.",
            ),
        },
    )
    @action(detail=True, methods=["get"], url_path="winners")
    def winners(self, request, pk=None):
        """Победители голосования: GET /api/votings/{id}/winners"""

        voting = self.get_object()

        if not voting._is_finished:  # type: ignore[attr-defined]
            return Response(
                {"detail": "Голосование ещё не завершено."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_votes = voting.voting_characters.aggregate(m=Max("votes_count"))["m"]
        if not max_votes:
            return Response([])

        qs = annotate_votes_count(
            Character.objects.filter(
                voting_characters__voting=voting,
                voting_characters__votes_count=max_votes,
            ),
            voting,
        ).order_by(
            "-_votes_count",
            "last_name",
            "first_name",
            "patronymic",
        )
        return Response(
            CharacterSerializer(qs, many=True, context={"request": request}).data
        )

    # =======================================
    # POST /api/votings/{id}/vote/
    # =======================================
    @extend_schema(
        tags=["votings"],
        summary="Отдать голос",
        description=(
            "Приём голоса за персонажа. Авторизация не требуется.\n\n"
            "**Реплизована защита от накрутки.** Отпечаток голосующего - `sha256(IP + User-Agent + X-Client-Id)`. "
            "Повторный голос с теми же данными вернёт `400`. Клиент может присылать заголовок `X-Client-Id` "
            "(случайный идентификатор из `localStorage`) - это отсекает повторные голоса из одной сети.\n\n"
            "**Throttling.** 30 запросов в час с одного IP - при превышении `429`.\n\n"
            "**Заголовки запроса:**\n"
            "- `X-Client-Id` (опционально) - идентификатор клиента;\n"
            "- `User-Agent` - учитывается при построении отпечатка;\n"
            "- `X-Forwarded-For` - учитывается за прокси."
        ),
        request=VoteRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=CharacterSerializer,
                description="Голос принят. Возвращается персонаж с обновлённым `number_votes`.",
            ),
            400: OpenApiResponse(
                response=ErrorSerializer,
                description="Голосование неактивно, либо этот отпечаток уже голосовал в этом голосовании.",
                examples=[
                    OpenApiExample(
                        "inactive", value={"detail": "Голосование неактивно."}
                    ),
                    OpenApiExample(
                        "already_voted",
                        value={"detail": "Вы уже голосовали в этом голосовании."},
                    ),
                ],
            ),
            404: OpenApiResponse(
                response=ErrorSerializer,
                description="Персонаж не участвует в этом голосовании.",
                examples=[
                    OpenApiExample(
                        "not_a_participant",
                        value={"detail": "Персонаж не найден в этом голосовании."},
                    ),
                ],
            ),
            429: OpenApiResponse(
                response=ErrorSerializer,
                description="Превышен лимит голосований с этого IP (30/час).",
                examples=[
                    OpenApiExample(
                        "throttled",
                        value={
                            "detail": (
                                "Request was throttled. "
                                "Expected available in 3600 seconds."
                            )
                        },
                    ),
                ],
            ),
        },
    )
    @action(
        detail=True, methods=["post"], url_path="vote", throttle_classes=[VoteThrottle]
    )
    def vote(self, request, pk=None):
        """Произвести голосование за персонажа: POST /api/votings/{id}/vote"""

        voting = self.get_object()

        # Валидация тела запроса
        req = VoteRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)

        # Проверка наличия персонажа именно в этом голосовании
        try:
            character = voting.characters.get(pk=req.validated_data["character_id"])
        except Character.DoesNotExist:
            return Response(
                {"detail": "Персонаж не найден в этом голосовании."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Собираем всё, из чего складывается отпечаток голосующего
        ip = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        client_id = request.META.get("HTTP_X_CLIENT_ID", "")

        # Атомарная запись голосования
        try:
            voting.cast_vote(
                character, ip=ip, user_agent=user_agent, client_id=client_id
            )
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST
            )

        # После cast_vote возвращаем персонажа с уже обновлённым счётчиком.
        annotated = annotate_votes_count(
            Character.objects.filter(pk=character.pk), voting
        ).first()

        return Response(
            CharacterSerializer(annotated, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )
