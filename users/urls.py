from django.urls import path
from rest_framework.routers import SimpleRouter


from .apps import UsersConfig
from .views import UserViewSet, LoginPageView, TelegramCallbackView

app_name = UsersConfig.name

router_user = SimpleRouter()
router_user.register(r"user", UserViewSet)

urlpatterns = (
    router_user.urls

    + [
        path('login_page/', LoginPageView.as_view(), name='login_page'),

        path('telegram-callback/<str:token>/', TelegramCallbackView.as_view(), name='telegram_callback'),
    ]

    # + [
    #     path("login/", TokenObtainPairView.as_view(), name="login"),
    #     path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # ]
)
