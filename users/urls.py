from django.urls import path
from rest_framework.routers import SimpleRouter


from .apps import UsersConfig
from .views import (
    UserViewSet,
    LoginPageView,
    TelegramCallbackView,
    LogoutView,
    AwaitingApprovalView,
    IssueXferTokenView,
    XferLoginView,
)

app_name = UsersConfig.name

router_user = SimpleRouter()
router_user.register(r"user", UserViewSet)

urlpatterns = (
    router_user.urls

    + [
        path('login_page/', LoginPageView.as_view(), name='login_page'),
        path('awaiting-approval/', AwaitingApprovalView.as_view(), name='awaiting_approval'),
        path('logout/', LogoutView.as_view(), name='logout'),
        path('xfer/issue/', IssueXferTokenView.as_view(), name='xfer_issue'),
        path('xfer/<str:token>/', XferLoginView.as_view(), name='xfer_login'),

        path('telegram-callback/<str:token>/', TelegramCallbackView.as_view(), name='telegram_callback'),
    ]

    # + [
    #     path("login/", TokenObtainPairView.as_view(), name="login"),
    #     path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # ]
)
