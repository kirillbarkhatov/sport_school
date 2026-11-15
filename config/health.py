from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Проверка живости для healthcheck контейнера: процесс отвечает и БД доступна."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"status": "error", "db": str(exc)}, status=503)
    return JsonResponse({"status": "ok"})
