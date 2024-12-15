from django.urls import path
from rest_framework.routers import SimpleRouter

from school.models import Person
from .apps import ApiConfig
from .views import PersonViewSet, UserViewSet

app_name = ApiConfig.name

router_person = SimpleRouter()
router_person.register(r"person", PersonViewSet)

router_user = SimpleRouter()
router_user.register(r"user", UserViewSet)

urlpatterns = (
    router_person.urls
    + router_user.urls
    # + [
    #     path("login/", TokenObtainPairView.as_view(), name="login"),
    #     path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # ]
)