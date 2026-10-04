from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from voting.utils import annotate_votes_count

from .models import Character, Voting


class ErrorSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text="Текст ошибки")


class CharacterSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    number_votes = serializers.SerializerMethodField()

    class Meta:
        model = Character
        fields = (
            "id",
            "first_name",
            "last_name",
            "patronymic",
            "full_name",
            "photo",
            "age",
            "bio",
            "number_votes",
        )

    def get_full_name(self, obj: Character) -> str:
        return str(obj)

    def get_number_votes(self, obj: Character) -> int:
        # Если кверисет аннотирован — берём из аннотации.
        val = getattr(obj, "_votes_count", None)
        return int(val) if val is not None else 0


class VotingListSerializer(serializers.ModelSerializer):

    is_active = serializers.SerializerMethodField()
    is_finished = serializers.SerializerMethodField()
    is_upcoming = serializers.SerializerMethodField()

    class Meta:
        model = Voting
        fields = (
            "id",
            "title",
            "start_date",
            "end_date",
            "early_finish_votes",
            "is_active",
            "is_finished",
            "is_upcoming",
        )

    def get_is_active(self, obj: Voting) -> bool:
        val = getattr(obj, "_is_active", None)
        return bool(val) if val is not None else obj.is_active

    def get_is_finished(self, obj: Voting) -> bool:
        val = getattr(obj, "_is_finished", None)
        return bool(val) if val is not None else obj.is_finished

    def get_is_upcoming(self, obj: Voting) -> bool:
        val = getattr(obj, "_is_upcoming", None)
        return bool(val) if val is not None else obj.is_upcoming


class VotingDetailSerializer(VotingListSerializer):
    characters = serializers.SerializerMethodField()

    class Meta(VotingListSerializer.Meta):
        fields = VotingListSerializer.Meta.fields + ("characters",)

    @extend_schema_field(CharacterSerializer(many=True))
    def get_characters(self, obj: Voting) -> Any:
        qs = annotate_votes_count(
            Character.objects.filter(voting_characters__voting=obj),
            obj,
        ).order_by(
            "-_votes_count",
            "last_name",
            "first_name",
            "patronymic",
        )
        return CharacterSerializer(qs, many=True, context=self.context).data


class VoteRequestSerializer(serializers.Serializer):
    character_id = serializers.IntegerField(
        min_value=1,
        help_text="ID персонажа, участвующего в этом голосовании",
    )
