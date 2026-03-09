"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from api.views import (
    OnlineResultsCompetitionHardRefreshView,
    OnlineResultsCompetitionLivePublicStateView,
    OnlineResultsCompetitionLivePublicView,
    OnlineResultsCompetitionLiveStateView,
    OnlineResultsCompetitionLiveView,
    OnlineResultsCompetitionSoftRefreshView,
    OnlineResultsStreamRunsView,
    OnlineResultsWebhookEventsView,
    OnlineResultsWebhookView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("school.urls", namespace="school")),
    path("", include("users.urls", namespace="users")),
    path("api/", include("api.urls", namespace="api")),
    path("members/", include("members.urls", namespace="members")),
    path("classes/", include("classes.urls", namespace="classes")),
    path("groups/", include("groups.urls", namespace="groups")),
    path("notifications/", include("notifications.urls", namespace="notifications")),
    path(
        "integrations/online-results/webhook/",
        OnlineResultsWebhookView.as_view(),
        name="online-results-webhook",
    ),
    path(
        "integrations/online-results/streams/",
        OnlineResultsStreamRunsView.as_view(),
        name="online-results-stream-runs",
    ),
    path(
        "integrations/online-results/events/",
        OnlineResultsWebhookEventsView.as_view(),
        name="online-results-webhook-events",
    ),
    path(
        "integrations/online-results/live/",
        OnlineResultsCompetitionLiveView.as_view(),
        name="online-results-live",
    ),
    path(
        "integrations/online-results/live-state/",
        OnlineResultsCompetitionLiveStateView.as_view(),
        name="online-results-live-state",
    ),
    path(
        "integrations/online-results/soft-refresh/",
        OnlineResultsCompetitionSoftRefreshView.as_view(),
        name="online-results-soft-refresh",
    ),
    path(
        "integrations/online-results/hard-refresh/",
        OnlineResultsCompetitionHardRefreshView.as_view(),
        name="online-results-hard-refresh",
    ),
    path(
        "integrations/online-results/live/public/<str:token>/",
        OnlineResultsCompetitionLivePublicView.as_view(),
        name="online-results-live-public",
    ),
    path(
        "integrations/online-results/live/public/<str:token>/state/",
        OnlineResultsCompetitionLivePublicStateView.as_view(),
        name="online-results-live-public-state",
    ),
]
