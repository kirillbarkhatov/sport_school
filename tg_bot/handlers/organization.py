"""Организация в боте (R3-1).

- Перед всеми обработчиками (группа ORGANIZATION_GROUP) определяется организация пользователя и выставляется
  контекст: выбранная и всё ещё допустимая → единственное активное членство → организация по умолчанию
  (посетитель). Контекст ставится на каждое обновление заново, поэтому не переходит к другому пользователю и
  не остаётся после смены организации.
- Несколько активных членств без выбора: бот предлагает выбрать и не выполняет команду (ApplicationHandlerStop).
- /start org_<slug> или /start <slug> — выбор организации; без членства — приглашение вступить на сайте
  (членство автоматически не создаётся). /org и кнопки org:<id> — смена организации.
"""

from __future__ import annotations

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from organizations.auth import (
    active_memberships,
    can_select,
    organization_by_slug,
    resolve_bot_organization,
    user_by_telegram,
)
from organizations.context import NO_ORGANIZATION, set_current_organization

ORGANIZATION_GROUP = -100
ORG_KEY = "organization_id"
ORG_CALLBACK_PREFIX = "org:"


def _user_data(context):
    data = getattr(context, "user_data", None)
    return data if isinstance(data, dict) else None


def _is_selection_update(update: Update) -> bool:
    query = update.callback_query
    if query is not None and (query.data or "").startswith(ORG_CALLBACK_PREFIX):
        return True
    message = update.effective_message
    text = (getattr(message, "text", None) or "").strip()
    return text.startswith("/org") or text.startswith("/start")


def _choice_markup(memberships) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(m.organization.name, callback_data=f"{ORG_CALLBACK_PREFIX}{m.organization_id}")]
            for m in memberships
        ]
    )


async def set_update_organization(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    data = _user_data(context)
    selected = data.get(ORG_KEY) if data else None

    def resolve():
        user = user_by_telegram(tg_user.id) if tg_user else None
        organization = resolve_bot_organization(user, selected)
        memberships = list(active_memberships(user)) if (user is not None and organization is None) else []
        return organization, memberships

    organization, memberships = await sync_to_async(resolve, thread_sensitive=True)()
    if data is not None and selected and (organization is None or organization.pk != selected):
        data.pop(ORG_KEY, None)  # выбор больше не допустим (членство приостановлено, организация закрыта)
    if organization is not None:
        set_current_organization(organization)
        return
    set_current_organization(NO_ORGANIZATION)
    if _is_selection_update(update):
        return
    message = update.effective_message
    if message is not None:
        await message.reply_text("Выберите организацию, в которой работать:", reply_markup=_choice_markup(memberships))
    raise ApplicationHandlerStop


async def select_organization(update: Update, context: ContextTypes.DEFAULT_TYPE, slug: str) -> bool:
    """/start org_<slug>: True, если payload — организация (обработано здесь)."""
    organization = await sync_to_async(organization_by_slug, thread_sensitive=True)(slug)
    if organization is None:
        return False
    tg_user = update.effective_user

    def check():
        user = user_by_telegram(tg_user.id)
        return user, (user is not None and can_select(user, organization))

    user, allowed = await sync_to_async(check, thread_sensitive=True)()
    message = update.effective_message
    data = _user_data(context)
    if allowed:
        if data is not None:
            data[ORG_KEY] = organization.pk
        set_current_organization(organization)
        await message.reply_text(f"Организация: {organization.name}. Команды бота работают в ней.")
        return True
    # посетитель: членство не создаётся, вступление — через сайт организации
    join_url = f"{settings.SITE_BASE_URL.rstrip('/')}/login_page/"
    await message.reply_text(
        f"Вы ещё не участник «{organization.name}». Чтобы вступить, войдите на сайт и нажмите «Вступить».",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Вступить на сайте", url=join_url)]]),
    )
    return True


def parse_org_payload(raw: str | None) -> str | None:
    if not raw:
        return None
    if raw.startswith("org_"):
        return raw[4:]
    return raw if raw.replace("-", "").isalnum() and len(raw) <= 64 else None


async def org_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user

    def load():
        user = user_by_telegram(tg_user.id)
        return list(active_memberships(user)) if user else []

    memberships = await sync_to_async(load, thread_sensitive=True)()
    if not memberships:
        await update.effective_message.reply_text("У вас нет активных членств в организациях.")
        return
    await update.effective_message.reply_text("Выберите организацию:", reply_markup=_choice_markup(memberships))


async def org_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    raw = (query.data or "")[len(ORG_CALLBACK_PREFIX) :]
    tg_user = update.effective_user

    def check():
        from organizations.models import Organization

        organization = Organization.objects.filter(pk=int(raw)).first() if raw.isdigit() else None
        user = user_by_telegram(tg_user.id)
        return organization, (user is not None and can_select(user, organization))

    organization, allowed = await sync_to_async(check, thread_sensitive=True)()
    if not allowed:
        await query.answer("Эта организация недоступна.", show_alert=True)
        return
    data = _user_data(context)
    if data is not None:
        data[ORG_KEY] = organization.pk
    set_current_organization(organization)
    await query.answer()
    await query.edit_message_text(f"Организация: {organization.name}. Команды бота работают в ней.")
