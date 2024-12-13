from django.urls import path
from rest_framework.routers import SimpleRouter

from school.models import Person
from .apps import ApiConfig
from .views import PersonViewSet

app_name = ApiConfig.name

router_person = SimpleRouter()
router_person.register(r"person", PersonViewSet)

urlpatterns = (
    router_person.urls
    # + [
    #     path("login/", TokenObtainPairView.as_view(), name="login"),
    #     path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # ]
)