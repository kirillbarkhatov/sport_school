from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from school.apps import SchoolConfig

from . import views

app_name = SchoolConfig.name

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("athlete/", views.AthleteListView.as_view(), name="athlete_list"),
    path("athlete/simple/", views.AthleteSimpleListView.as_view(), name="athlete_simple_list"),
    path('athlete/<int:athlete_id>/edit/', views.edit_athlete, name='edit_athlete'),
    path('athlete/<int:pk>/compact/', views.AthleteCompactEditView.as_view(), name='athlete_edit_compact'),
    path(
        "athlete/<int:pk>/inline-update/",
        views.AthleteInlineUpdateView.as_view(),
        name="athlete_inline_update",
    ),
    path("competitions/", views.CompetitionListView.as_view(), name="competition_list"),
    path("competitions/new/", views.CompetitionCreateUpdateView.as_view(), name="competition_create"),
    path("competitions/<int:pk>/edit/", views.CompetitionCreateUpdateView.as_view(), name="competition_edit"),
    path(
        "competitions/<int:pk>/entries/toggle/",
        views.CompetitionEntryToggleView.as_view(),
        name="competition_entries_toggle",
    ),
    path("competitions/<int:pk>/export/", views.competition_export, name="competition_export"),
    path("competitions/<int:pk>/export/type2/", views.competition_export_type2, name="competition_export_type2"),
    path("competitions/<int:pk>/export/word/", views.competition_export_word_type1, name="competition_export_word_type1"),
    path("competitions/<int:pk>/export/word2/", views.competition_export_word_type2, name="competition_export_word_type2"),
    path("competitions/<int:pk>/export/word3/", views.competition_export_word_type3, name="competition_export_word_type3"),
    path("competitions/<int:pk>/export/word4/", views.competition_export_word_type4, name="competition_export_word_type4"),
    path("competitions/<int:pk>/documents/upload/", views.CompetitionDocumentUploadView.as_view(), name="competition_documents_upload"),
    path("competitions/<int:pk>/documents/<int:doc_id>/delete/", views.CompetitionDocumentDeleteView.as_view(), name="competition_documents_delete"),
    path("documents/bulk-upload/", views.BulkDocumentUploadView.as_view(), name="documents_bulk_upload"),
    path("documents/analysis/", views.DocumentAIAnalysisListView.as_view(), name="documents_analysis_list"),
    path("documents/signed/<int:document_id>/", views.SignedDocumentAccessView.as_view(), name="document_signed_access"),
    path("competitions/<int:pk>/apply/<str:token>/", views.CompetitionApplyView.as_view(), name="competition_apply"),
    path(
        "competitions/<int:pk>/apply/<str:token>/toggle/",
        views.CompetitionApplyToggleView.as_view(),
        name="competition_apply_toggle",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
