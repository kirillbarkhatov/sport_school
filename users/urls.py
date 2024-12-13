from django.urls import path
from rest_framework.routers import SimpleRouter


from .apps import UsersConfig
from .views import UserViewSet

app_name = UsersConfig.name

router_user = SimpleRouter()
router_user.register(r"user", UserViewSet)

urlpatterns = (
    router_user.urls
    # + [
    #     path("login/", TokenObtainPairView.as_view(), name="login"),
    #     path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # ]
)
