from __future__ import annotations

from celery import shared_task

from members.models import PersonDedupJob
from members.services import run_dedup_job


@shared_task
def run_person_dedup_job_task(job_id: int) -> int:
    job = PersonDedupJob.objects.get(pk=job_id)
    run_dedup_job(job)
    return job_id

