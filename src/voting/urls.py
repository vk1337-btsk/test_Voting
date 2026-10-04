from rest_framework.routers import DefaultRouter

from .views import VotingViewSet

router = DefaultRouter()
router.register(r"votings", VotingViewSet, basename="voting")

urlpatterns = router.urls
