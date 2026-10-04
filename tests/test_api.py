"""Тесты API."""

import pytest
from rest_framework.test import APIClient

from voting.models import Character, VotingCharacter

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


# =================================================
# GET /api/votings/
# =================================================
class TestVotingList:
    def test_returns_all_votings(self, api_client, active_voting, finished_voting):
        resp = api_client.get("/api/votings/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_filter_active(self, api_client, active_voting, finished_voting):
        data = api_client.get("/api/votings/?status=active").json()
        assert data["count"] == 1
        assert data["results"][0]["id"] == active_voting.id

    def test_filter_finished(self, api_client, active_voting, finished_voting):
        data = api_client.get("/api/votings/?status=finished").json()
        assert data["count"] == 1
        assert data["results"][0]["id"] == finished_voting.id

    def test_filter_upcoming(self, api_client, active_voting, future_voting):
        data = api_client.get("/api/votings/?status=upcoming").json()
        assert data["count"] == 1
        assert data["results"][0]["id"] == future_voting.id

    def test_unknown_status_returns_all(
        self, api_client, active_voting, finished_voting
    ):
        # django-filter ChoiceFilter со strict=False игнорирует невалидное значение.
        resp = api_client.get("/api/votings/?status=foobar")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_pagination_response_shape(self, api_client, active_voting):
        body = api_client.get("/api/votings/").json()
        assert set(body.keys()) == {"count", "next", "previous", "results"}

    def test_page_size_override(self, api_client, active_voting, finished_voting):
        body = api_client.get("/api/votings/?page_size=1").json()
        assert len(body["results"]) == 1
        assert body["count"] == 2

    def test_page_size_capped_at_max(self, api_client, active_voting, finished_voting):
        resp = api_client.get("/api/votings/?page_size=1000")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) <= 100

    def test_list_includes_status_flags(self, api_client, active_voting):
        item = api_client.get("/api/votings/").json()["results"][0]
        assert "is_active" in item
        assert "is_finished" in item
        assert "is_upcoming" in item


# =================================================
# GET /api/votings/{id}/
# =================================================
class TestVotingRetrieve:
    def test_returns_detail_with_characters(self, api_client, active_voting):
        resp = api_client.get(f"/api/votings/{active_voting.id}/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == active_voting.id
        assert len(body["characters"]) == 2

    def test_returns_404_for_unknown(self, api_client):
        assert api_client.get("/api/votings/99999/").status_code == 404


# =================================================
# GET /api/votings/{id}/characters/
# =================================================
class TestCharacters:
    def test_lists_participants(self, api_client, active_voting):
        resp = api_client.get(f"/api/votings/{active_voting.id}/characters/")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_sorted_by_votes_desc(
        self, api_client, active_voting, character, character2, set_votes
    ):
        set_votes(active_voting, character, 5)
        set_votes(active_voting, character2, 10)
        resp = api_client.get(f"/api/votings/{active_voting.id}/characters/")
        names = [c["last_name"] for c in resp.json()]
        assert names == ["Петров", "Иванов"]

    def test_returns_404_for_unknown_voting(self, api_client):
        assert api_client.get("/api/votings/99999/characters/").status_code == 404


# =================================================
# GET /api/votings/{id}/winners/
# =================================================
class TestWinners:
    def test_returns_400_for_active_voting(self, api_client, active_voting):
        resp = api_client.get(f"/api/votings/{active_voting.id}/winners/")
        assert resp.status_code == 400
        assert "не завершено" in resp.json()["detail"]

    def test_returns_winner_for_finished(
        self, api_client, finished_voting, character, set_votes
    ):
        set_votes(finished_voting, character, 5)
        resp = api_client.get(f"/api/votings/{finished_voting.id}/winners/")
        assert resp.status_code == 200
        winners = resp.json()
        assert len(winners) == 1
        assert winners[0]["id"] == character.id

    def test_returns_multiple_winners_on_tie(
        self, api_client, finished_voting, character, character2, set_votes
    ):
        set_votes(finished_voting, character, 5)
        set_votes(finished_voting, character2, 5)
        winners = api_client.get(f"/api/votings/{finished_voting.id}/winners/").json()
        assert len(winners) == 2

    def test_returns_empty_when_zero_votes(self, api_client, finished_voting):
        resp = api_client.get(f"/api/votings/{finished_voting.id}/winners/")
        assert resp.status_code == 200
        assert resp.json() == []


# =================================================
# POST /api/votings/{id}/vote/
# =================================================
class TestVote:
    def test_successful_vote_returns_200(self, api_client, active_voting, character):
        resp = api_client.post(
            f"/api/votings/{active_voting.id}/vote/",
            {"character_id": character.id},
            format="json",
            HTTP_USER_AGENT="test-ua",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["number_votes"] == 1
        vc = VotingCharacter.objects.get(voting=active_voting, character=character)
        assert vc.votes_count == 1

    def test_rejects_duplicate(self, api_client, active_voting, character):
        payload = {"character_id": character.id}
        headers = {"HTTP_USER_AGENT": "test-ua", "HTTP_X_CLIENT_ID": "abc"}
        api_client.post(
            f"/api/votings/{active_voting.id}/vote/", payload, format="json", **headers
        )
        resp = api_client.post(
            f"/api/votings/{active_voting.id}/vote/", payload, format="json", **headers
        )
        assert resp.status_code == 400
        assert "уже голосовали" in resp.json()["detail"]

    def test_rejects_inactive_voting(self, api_client, finished_voting, character):
        resp = api_client.post(
            f"/api/votings/{finished_voting.id}/vote/",
            {"character_id": character.id},
            format="json",
        )
        assert resp.status_code == 400
        assert "неактивно" in resp.json()["detail"]

    def test_rejects_character_not_in_voting(self, api_client, active_voting):
        outsider = Character.objects.create(last_name="X", first_name="Y", age=20)
        resp = api_client.post(
            f"/api/votings/{active_voting.id}/vote/",
            {"character_id": outsider.id},
            format="json",
        )
        assert resp.status_code == 404
        assert "не найден" in resp.json()["detail"]

    def test_rejects_invalid_body(self, api_client, active_voting):
        resp = api_client.post(
            f"/api/votings/{active_voting.id}/vote/", {}, format="json"
        )
        assert resp.status_code == 400
        assert "character_id" in resp.json()

    def test_rejects_missing_character_id(self, api_client, active_voting):
        resp = api_client.post(
            f"/api/votings/{active_voting.id}/vote/",
            {"character_id": 99999},
            format="json",
        )
        assert resp.status_code == 404

    def test_different_client_ids_can_vote(
        self, api_client, active_voting, character
    ):
        payload = {"character_id": character.id}
        api_client.post(
            f"/api/votings/{active_voting.id}/vote/",
            payload,
            format="json",
            HTTP_USER_AGENT="ua",
            HTTP_X_CLIENT_ID="user-1",
        )
        resp = api_client.post(
            f"/api/votings/{active_voting.id}/vote/",
            payload,
            format="json",
            HTTP_USER_AGENT="ua",
            HTTP_X_CLIENT_ID="user-2",
        )
        assert resp.status_code == 200
        vc = VotingCharacter.objects.get(voting=active_voting, character=character)
        assert vc.votes_count == 2