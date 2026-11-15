from django.urls import path

from .apps import UsersConfig
from .views import (
    AwaitingApprovalView,
    IssueXferTokenView,
    LoginPageView,
    LogoutView,
    TelegramCallbackView,
    XferLoginView,
)

app_name = UsersConfig.name

urlpatterns = (
    [
        path("login_page/", LoginPageView.as_view(), name="login_page"),
        path("awaiting-approval/", AwaitingApprovalView.as_view(), name="awaiting_approval"),
        path("logout/", LogoutView.as_view(), name="logout"),
        path("xfer/issue/", IssueXferTokenView.as_view(), name="xfer_issue"),
        path("xfer/<str:token>/", XferLoginView.as_view(), name="xfer_login"),
        path("telegram-callback/<str:token>/", TelegramCallbackView.as_view(), name="telegram_callback"),
    ]
    # + [
    #     path("login/", TokenObtainPairView.as_view(), name="login"),
    #     path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # ]
)
