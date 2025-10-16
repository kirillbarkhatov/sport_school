from datetime import date, timedelta
from django.utils import timezone

from .models import AthleteContract, DiscountType, FamilyService, ServiceType


def get_season_bounds(reference_date=None):
    if reference_date is None:
        reference_date = timezone.now().date()
    year = reference_date.year
    boundary = date(year, 9, 1)
    if reference_date < boundary:
        start = date(year - 1, 9, 1)
    else:
        start = boundary
    end = date(start.year + 1, 8, 31)
    return start, end


def compute_season_label(reference_date=None):
    start, _ = get_season_bounds(reference_date)
    return f"{start.year}-{start.year + 1}"


def get_month_range(reference_date=None):
    if reference_date is None:
        reference_date = timezone.now().date()
    month_start = reference_date.replace(day=1)
    if month_start.month == 12:
        next_month = date(month_start.year + 1, 1, 1)
    else:
        next_month = date(month_start.year, month_start.month + 1, 1)
    month_end = next_month - timedelta(days=1)
    return month_start, month_end, next_month


def ensure_monthly_service_for_contract(contract: AthleteContract, reference_date=None):
    if contract.status != "active":
        return None

    if reference_date is None:
        reference_date = timezone.now().date()

    month_start, _, next_month = get_month_range(reference_date)

    existing = FamilyService.objects.filter(
        contract=contract,
        service_type=ServiceType.MONTHLY,
        created_at__date__gte=month_start,
        created_at__date__lt=next_month,
    )
    if existing.exists():
        return existing.first()

    season = compute_season_label(reference_date)
    due_day = 5
    due_date = date(reference_date.year, reference_date.month, due_day)

    monthly_label = getattr(ServiceType.MONTHLY, "label", "Ежемесячный платеж")
    service = FamilyService.objects.create(
        family=contract.profile.family,
        profile=contract.profile,
        contract=contract,
        name=f"{monthly_label} - {contract.profile.athlete.person.surname} {contract.profile.athlete.person.name} - сезон {season}",
        service_type=ServiceType.MONTHLY,
        amount=contract.base_fee,
        discount_type=DiscountType.NONE,
        discount_value=contract.discount_value,
        is_recurring=True,
        due_date=due_date,
    )
    return service
