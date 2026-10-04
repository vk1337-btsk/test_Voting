"""Тесты моделей."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from voting.models import Character, Vote, Voting, VotingCharacter

pytestmark = pytest.mark.django_db


class TestCharacter:
    def test_str_full_fio(self, character):
        assert str(character) == "Иванов Иван Иванович"

    def test_str_without_patronymic(self):
        ch = Character.objects.create(last_name="Иванов", first_name="Иван", age=30)
        assert str(ch) == "Иванов Иван"

    def test_ordering(self):
        Character.objects.create(last_name="Яковлев", first_name="А", age=1)
        Character.objects.create(last_name="Абрамов", first_name="Б", age=1)
        names = list(Character.objects.values_list("last_name", flat=True))
        assert names == ["Абрамов", "Яковлев"]


class TestVotingIsActive:
    def test_active_within_period(self, active_voting):
        assert active_voting.is_active is True

    def test_inactive_before_start(self, future_voting):
        assert future_voting.is_active is False

    def test_inactive_after_end(self, finished_voting):
        assert finished_voting.is_active is False

    def test_inactive_when_early_finish_reached(
        self, early_finish_voting, character, set_votes
    ):
        set_votes(early_finish_voting, character, 3)
        assert early_finish_voting.is_active is False

    def test_active_when_early_finish_not_reached(
        self, early_finish_voting, character, set_votes
    ):
        set_votes(early_finish_voting, character, 2)
        assert early_finish_voting.is_active is True

    # --- boundary (freeze через patch) ---
    def test_boundary_start_equal_now_is_inactive(self, db):
        now = timezone.now()
        v = Voting.objects.create(
            title="X",
            start_date=now,
            end_date=now + timedelta(hours=1),
        )
        with patch("django.utils.timezone.now", return_value=now):
            assert v.is_active is False

    def test_boundary_end_equal_now_is_active(self, db):
        now = timezone.now()
        v = Voting.objects.create(
            title="X",
            start_date=now - timedelta(hours=1),
            end_date=now,
        )
        with patch("django.utils.timezone.now", return_value=now):
            assert v.is_active is True

    def test_boundary_start_one_second_ago_is_active(self, db):
        now = timezone.now()
        v = Voting.objects.create(
            title="X",
            start_date=now - timedelta(seconds=1),
            end_date=now + timedelta(hours=1),
        )
        with patch("django.utils.timezone.now", return_value=now):
            assert v.is_active is True

    def test_boundary_end_one_second_ago_is_inactive(self, db):
        now = timezone.now()
        v = Voting.objects.create(
            title="X",
            start_date=now - timedelta(hours=1),
            end_date=now - timedelta(seconds=1),
        )
        with patch("django.utils.timezone.now", return_value=now):
            assert v.is_active is False

    def test_boundary_early_finish_equal_threshold(
        self, early_finish_voting, character, set_votes
    ):
        """votes_count == early_finish_votes считается достижением порога."""
        set_votes(early_finish_voting, character, 3)
        with patch("django.utils.timezone.now", return_value=timezone.now()):
            assert early_finish_voting.has_early_finish_reached is True
            assert early_finish_voting.is_active is False

    def test_boundary_early_finish_one_less(
        self, early_finish_voting, character, set_votes
    ):
        """votes_count == early_finish_votes - 1 — порог не достигнут."""
        set_votes(early_finish_voting, character, 2)
        with patch("django.utils.timezone.now", return_value=timezone.now()):
            assert early_finish_voting.has_early_finish_reached is False
            assert early_finish_voting.is_active is True


class TestGetWinners:
    def test_returns_empty_for_active_voting(self, active_voting, character, set_votes):
        set_votes(active_voting, character, 5)
        assert list(active_voting.get_winners()) == []

    def test_returns_single_winner(
        self, finished_voting, character, character2, set_votes
    ):
        set_votes(finished_voting, character, 5)
        set_votes(finished_voting, character2, 3)
        winners = list(finished_voting.get_winners())
        assert winners == [character]

    def test_returns_multiple_winners_on_tie(
        self, finished_voting, character, character2, set_votes
    ):
        set_votes(finished_voting, character, 5)
        set_votes(finished_voting, character2, 5)
        assert set(finished_voting.get_winners()) == {character, character2}

    def test_returns_empty_when_zero_votes(self, finished_voting):
        assert list(finished_voting.get_winners()) == []

    def test_works_for_early_finish_voting(
        self, early_finish_voting, character, character2, set_votes
    ):
        set_votes(early_finish_voting, character, 3)
        set_votes(early_finish_voting, character2, 1)
        assert list(early_finish_voting.get_winners()) == [character]


class TestFingerprint:
    def test_deterministic(self):
        a = Voting.build_fingerprint("1.1.1.1", "Mozilla", "abc")
        b = Voting.build_fingerprint("1.1.1.1", "Mozilla", "abc")
        assert a == b
        assert len(a) == 64

    def test_changes_on_ip(self):
        a = Voting.build_fingerprint("1.1.1.1", "Mozilla", "abc")
        b = Voting.build_fingerprint("2.2.2.2", "Mozilla", "abc")
        assert a != b

    def test_changes_on_ua(self):
        a = Voting.build_fingerprint("1.1.1.1", "Mozilla", "abc")
        b = Voting.build_fingerprint("1.1.1.1", "Chrome", "abc")
        assert a != b

    def test_changes_on_client_id(self):
        a = Voting.build_fingerprint("1.1.1.1", "Mozilla", "abc")
        b = Voting.build_fingerprint("1.1.1.1", "Mozilla", "xyz")
        assert a != b

    def test_handles_empty_strings(self):
        assert len(Voting.build_fingerprint("", "", "")) == 64


class TestCastVote:
    def test_successful_vote(self, active_voting, character):
        active_voting.cast_vote(
            character, ip="1.1.1.1", user_agent="UA", client_id="c1"
        )
        vc = VotingCharacter.objects.get(voting=active_voting, character=character)
        assert vc.votes_count == 1
        assert Vote.objects.filter(voting=active_voting).count() == 1

    def test_two_different_voters(self, active_voting, character):
        active_voting.cast_vote(character, ip="1.1.1.1", user_agent="UA")
        active_voting.cast_vote(character, ip="2.2.2.2", user_agent="UA")
        vc = VotingCharacter.objects.get(voting=active_voting, character=character)
        assert vc.votes_count == 2

    def test_rejects_inactive_voting(self, finished_voting, character):
        with pytest.raises(ValidationError, match="неактивно"):
            finished_voting.cast_vote(character, ip="1.1.1.1", user_agent="UA")

    def test_rejects_non_participant(self, active_voting):
        outsider = Character.objects.create(last_name="X", first_name="Y", age=20)
        with pytest.raises(ValidationError, match="не участвует"):
            active_voting.cast_vote(outsider, ip="1.1.1.1", user_agent="UA")

    def test_rejects_duplicate(self, active_voting, character):
        active_voting.cast_vote(
            character, ip="1.1.1.1", user_agent="UA", client_id="c1"
        )
        with pytest.raises(ValidationError, match="уже голосовали"):
            active_voting.cast_vote(
                character, ip="1.1.1.1", user_agent="UA", client_id="c1"
            )

    def test_truncates_long_user_agent(self, active_voting, character):
        active_voting.cast_vote(character, ip="1.1.1.1", user_agent="x" * 500)
        vote = Vote.objects.get(voting=active_voting)
        assert len(vote.user_agent) == 255

    def test_truncates_long_client_id(self, active_voting, character):
        active_voting.cast_vote(
            character, ip="1.1.1.1", user_agent="UA", client_id="y" * 500
        )
        vote = Vote.objects.get(voting=active_voting)
        assert len(vote.client_id) == 128

    def test_empty_ip_saved_as_null(self, active_voting, character):
        active_voting.cast_vote(character, ip="", user_agent="UA")
        vote = Vote.objects.get(voting=active_voting)
        assert vote.ip_address is None

    def test_early_finish_blocks_further_votes(
        self, early_finish_voting, character, character2
    ):
        for i in range(3):
            early_finish_voting.cast_vote(
                character, ip=f"1.1.1.{i}", user_agent="UA", client_id=f"c{i}"
            )
        with pytest.raises(ValidationError, match="неактивно"):
            early_finish_voting.cast_vote(character2, ip="2.2.2.2", user_agent="UA")


class TestConstraintEndAfterStart:
    """DB-констрейнт ``end_date > start_date``."""

    def test_rejects_end_before_start(self, db):
        now = timezone.now()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Voting.objects.create(
                    title="X",
                    start_date=now,
                    end_date=now - timedelta(hours=1),
                )

    def test_rejects_end_equal_start(self, db):
        now = timezone.now()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Voting.objects.create(title="X", start_date=now, end_date=now)

    def test_allows_end_after_start(self, db):
        now = timezone.now()
        v = Voting.objects.create(
            title="X",
            start_date=now,
            end_date=now + timedelta(seconds=1),
        )
        assert v.pk is not None