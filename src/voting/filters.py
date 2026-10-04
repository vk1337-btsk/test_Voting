import django_filters

from .models import Voting


class VotingFilter(django_filters.FilterSet):
    """Фильтр списка голосований.

    Статус — вычисляемое свойство, поэтому фильтруем по аннотациям
    `_is_active / _is_finished / _is_upcoming`, которые навешиваются
    в `VotingViewSet.get_queryset`.
    """

    status = django_filters.ChoiceFilter(
        choices=[
            ("active", "active"),
            ("finished", "finished"),
            ("upcoming", "upcoming"),
        ],
        method="filter_status",
        label="Статус голосования",
    )

    class Meta:
        model = Voting
        fields = []  # других полей фильтрации нет

    def filter_status(self, queryset, name, value):
        if value == "active":
            return queryset.filter(_is_active=True)
        if value == "finished":
            return queryset.filter(_is_finished=True)
        if value == "upcoming":
            return queryset.filter(_is_upcoming=True)
        return queryset
