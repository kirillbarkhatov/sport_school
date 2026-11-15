from django.urls import path

from . import views

app_name = "results"

urlpatterns = [
    path("", views.EventListView.as_view(), name="event_list"),
    path("new/", views.EventCreateView.as_view(), name="event_create"),
    path("<int:pk>/", views.EventDetailView.as_view(), name="event_detail"),
    path("<int:pk>/edit/", views.EventUpdateView.as_view(), name="event_update"),
    path("<int:pk>/panel/", views.PanelFragmentView.as_view(), name="panel_fragment"),
    path("<int:pk>/entries/<int:entry_id>/result/", views.EntryResultView.as_view(), name="entry_result"),
    path("<int:pk>/live/", views.LiveView.as_view(), name="live"),
    path("<int:pk>/live/fragment/", views.LiveFragmentView.as_view(), name="live_fragment"),
    path("public/<str:token>/", views.PublicLiveView.as_view(), name="public_live"),
    path("public/<str:token>/fragment/", views.PublicFragmentView.as_view(), name="public_fragment"),
]
