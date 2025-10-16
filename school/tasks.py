from django.utils import timezone
from celery import shared_task

from .models import AthleteContract
from .services import ensure_monthly_service_for_contract


@shared_task
def ensure_monthly_contract_services():
    today = timezone.now().date()
    contracts = AthleteContract.objects.select_related("profile__family", "profile__athlete__person")
    for contract in contracts:
        ensure_monthly_service_for_contract(contract, today)
