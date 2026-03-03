from __future__ import annotations

from datetime import datetime
from typing import Optional

from asgiref.sync import sync_to_async
from django.db import transaction
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.models import TelegramParticipant
from members.models import PersonMergeRedirect
from school.models import Athlete, Club, Family, FamilyMember, Person
from users.models import User, UserPersonLink, UserPersonLinkStatus
from users.telegram_identity import parse_telegram_reference, telegram_url_from_username

ONBOARDING_STATE_KEY = "group_onboarding_state"
ONBOARDING_SKIP_KEY = "group_onboarding_skip"
TARGET_GROUP_CHAT_IDS = {-1002102842870, -1003506899595, -1003528047322}
PARENT_RELATIONS = {"mother", "father", "grandmother", "grandfather", "guardian", "representative"}


def _parse_full_name(value: str) -> tuple[str, str, str]:
    cleaned = " ".join((value or "").strip().split())
    parts = cleaned.split(" ")
    if len(parts) < 2:
        return "", "", ""
    surname = parts[0].strip()
    name = parts[1].strip()
    middlename = parts[2].strip() if len(parts) > 2 else ""
    return surname, name, middlename


def _parse_birth_date(value: str) -> Optional[datetime.date]:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _onboarding_gender_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Мужской", callback_data=f"onboard:{prefix}:male")],
            [InlineKeyboardButton("Женский", callback_data=f"onboard:{prefix}:female")],
        ]
    )


def _onboarding_role_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Я спортсмен", callback_data="onboard:role:athlete")],
            [InlineKeyboardButton("Я родитель", callback_data="onboard:role:parent")],
        ]
    )


async def _load_user(tg_id: int) -> User | None:
    return await sync_to_async(
        User.objects.select_related("link", "person").filter(tg_id=tg_id).first,
        thread_sensitive=True,
    )()


async def get_target_group_titles_for_user(tg_id: int | None) -> list[str]:
    if not tg_id:
        return []

    def _query() -> list[str]:
        rows = (
            TelegramParticipant.objects
            .select_related("chat")
            .filter(user_id=tg_id, chat__chat_id__in=TARGET_GROUP_CHAT_IDS)
        )
        titles = {
            row.chat.title or row.chat.username or str(row.chat.chat_id)
            for row in rows
        }
        return sorted(titles)

    return await sync_to_async(_query, thread_sensitive=True)()


async def maybe_start_group_onboarding(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user: User,
    is_privileged: bool,
    can_auto_bind: bool,
) -> bool:
    if is_privileged or can_auto_bind:
        return False
    if context.user_data.get(ONBOARDING_SKIP_KEY):
        return False
    if context.user_data.get(ONBOARDING_STATE_KEY):
        await update.effective_message.reply_text(
            "Анкета ещё не завершена. Продолжите заполнение по шагам или отправьте /cancel."
        )
        return True

    titles = await get_target_group_titles_for_user(user.tg_id)
    if not titles:
        return False

    context.user_data[ONBOARDING_STATE_KEY] = {
        "step": "user_name",
        "group_titles": titles,
        "data": {},
    }

    title_list = ", ".join(titles)
    await update.effective_message.reply_text(
        "Мы видим, что вы состоите в группе/ах "
        f"{title_list}. Нам нужно чуть больше данных.\n\n"
        "Введите ваши Фамилию и Имя (одной строкой)."
    )
    return True


def _set_step(context: ContextTypes.DEFAULT_TYPE, step: str) -> dict:
    state = context.user_data.get(ONBOARDING_STATE_KEY) or {}
    state["step"] = step
    context.user_data[ONBOARDING_STATE_KEY] = state
    return state


def _drop_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(ONBOARDING_STATE_KEY, None)


@transaction.atomic
def _approve_user_with_person(user: User, person: Person, *, reason: str) -> None:
    updates: list[str] = []
    if user.person_id != person.id:
        user.person = person
        updates.append("person")
    if not user.is_approved:
        user.is_approved = True
        updates.append("is_approved")
    if updates:
        user.save(update_fields=updates)

    person_updates: list[str] = []
    if user.tg_id and person.telegram_id != user.tg_id:
        person.telegram_id = user.tg_id
        person_updates.append("telegram_id")
    if user.tg_username:
        desired_url = telegram_url_from_username(user.tg_username)
        current_username, _, _ = parse_telegram_reference(person.telegram)
        desired_username, _, _ = parse_telegram_reference(desired_url)
        if not current_username or current_username != desired_username:
            person.telegram = desired_url
            person_updates.append("telegram")
    if person_updates:
        person.save(update_fields=person_updates)

    link, _ = UserPersonLink.objects.get_or_create(user=user)
    link.suggested_person = person
    reasons = set(link.matched_reasons or [])
    reasons.add(reason)
    link.matched_reasons = sorted(reasons)
    link.apply_decision(
        UserPersonLinkStatus.APPROVED,
        note="auto approved via group onboarding",
    )
    link.save(
        update_fields=[
            "suggested_person",
            "matched_reasons",
            "status",
            "decided_by",
            "decided_at",
            "decision_note",
            "updated_at",
        ]
    )


def _resolve_person_for_athlete_payload(
    *,
    surname: str,
    name: str,
    middlename: str,
    gender: str,
    date_of_birth,
    club_name: str,
) -> tuple[str, Person]:
    redirected_sources = PersonMergeRedirect.objects.filter(is_active=True).values_list("source_person_id", flat=True)
    qs = Person.objects.exclude(id__in=redirected_sources).filter(
        surname__iexact=surname,
        name__iexact=name,
        gender=gender,
    )
    if middlename:
        qs = qs.filter(middlename__iexact=middlename)
    if date_of_birth:
        qs = qs.filter(date_of_birth=date_of_birth)

    if club_name:
        club = Club.objects.filter(name__iexact=club_name).first() or Club.objects.filter(name__icontains=club_name).first()
        if club:
            qs = qs.filter(club=club)

    count = qs.count()
    if count == 1:
        person = qs.first()
        return "matched", person
    if count > 1:
        raise ValueError("ambiguous")

    club = None
    if club_name:
        club = Club.objects.filter(name__iexact=club_name).first() or Club.objects.filter(name__icontains=club_name).first()
    person = Person.objects.create(
        surname=surname,
        name=name,
        middlename=middlename or "",
        gender=gender,
        date_of_birth=date_of_birth,
        club=club,
    )
    return "created", person


def _resolve_parent_person(
    *,
    user_surname: str,
    user_name: str,
    user_gender: str,
    athlete_surname: str,
    athlete_name: str,
    athlete_middlename: str,
    athlete_gender: str,
    athlete_birth_date,
) -> tuple[str, Optional[Person]]:
    redirected_sources = PersonMergeRedirect.objects.filter(is_active=True).values_list("source_person_id", flat=True)
    athlete_qs = (
        Person.objects.exclude(id__in=redirected_sources)
        .filter(
            athlete__isnull=False,
            surname__iexact=athlete_surname,
            name__iexact=athlete_name,
            gender=athlete_gender,
        )
    )
    if athlete_middlename:
        athlete_qs = athlete_qs.filter(middlename__iexact=athlete_middlename)
    if athlete_birth_date:
        athlete_qs = athlete_qs.filter(date_of_birth=athlete_birth_date)

    athlete_count = athlete_qs.count()
    if athlete_count > 1:
        raise ValueError("ambiguous")

    if athlete_count == 0:
        athlete_person = Person.objects.create(
            surname=athlete_surname,
            name=athlete_name,
            middlename=athlete_middlename or "",
            gender=athlete_gender,
            date_of_birth=athlete_birth_date,
        )
        Athlete.objects.create(person=athlete_person, level="unknown")
        parent_person = Person.objects.create(
            surname=user_surname,
            name=user_name,
            gender=user_gender,
        )
        family = Family.objects.create(family_name=user_surname or athlete_surname, contact_person=parent_person)
        FamilyMember.objects.create(family=family, person=parent_person, relation="representative")
        relation = "son" if athlete_gender == "male" else "daughter"
        FamilyMember.objects.create(family=family, person=athlete_person, relation=relation)
        return "created", parent_person

    athlete_person = athlete_qs.first()
    family_ids = FamilyMember.objects.filter(person=athlete_person).values_list("family_id", flat=True)
    parent_candidates = (
        Person.objects.exclude(id__in=redirected_sources)
        .filter(familymember__family_id__in=family_ids, familymember__relation__in=PARENT_RELATIONS)
        .exclude(id=athlete_person.id)
        .distinct()
    )
    count = parent_candidates.count()
    if count == 1:
        return "matched", parent_candidates.first()

    own_match_qs = parent_candidates.filter(
        surname__iexact=user_surname,
        name__iexact=user_name,
        gender=user_gender,
    )
    if own_match_qs.count() == 1:
        return "matched", own_match_qs.first()

    if count > 1:
        raise ValueError("ambiguous")

    parent_person = Person.objects.create(
        surname=user_surname,
        name=user_name,
        gender=user_gender,
    )
    family = Family.objects.filter(id__in=family_ids).first()
    if family:
        FamilyMember.objects.get_or_create(
            family=family,
            person=parent_person,
            defaults={"relation": "representative"},
        )
    return "created", parent_person


async def _finalize_athlete_onboarding(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
) -> None:
    data = state.get("data") or {}
    user = await _load_user(update.effective_user.id)
    if not user:
        _drop_state(context)
        await update.effective_message.reply_text("Не удалось найти ваш профиль. Выполните /start повторно.")
        return

    def _commit():
        status, person = _resolve_person_for_athlete_payload(
            surname=data["user_surname"],
            name=data["user_name"],
            middlename=data.get("athlete_middlename", ""),
            gender=data["user_gender"],
            date_of_birth=data.get("athlete_birth_date"),
            club_name=data.get("athlete_club", ""),
        )
        athlete, created = Athlete.objects.get_or_create(
            person=person,
            defaults={"level": "unknown", "rank": data.get("athlete_rank") or ""},
        )
        if not created and data.get("athlete_rank") and not athlete.rank:
            athlete.rank = data["athlete_rank"]
            athlete.save(update_fields=["rank"])
        _approve_user_with_person(user, person, reason="group_onboarding")
        return status, person

    try:
        status, person = await sync_to_async(_commit, thread_sensitive=True)()
    except ValueError:
        _drop_state(context)
        context.user_data[ONBOARDING_SKIP_KEY] = True
        await update.effective_message.reply_text(
            "Нашлось несколько похожих персон. Продолжим по стандартному сценарию подтверждения."
        )
        from tg_bot.handlers.auth import start as start_command

        await start_command(update, context)
        return

    _drop_state(context)
    context.user_data[ONBOARDING_SKIP_KEY] = True
    if status == "matched":
        await update.effective_message.reply_text(f"Профиль найден и привязан: {person}.")
    else:
        await update.effective_message.reply_text(f"Создали новый профиль и привязали его: {person}.")
    from tg_bot.handlers.auth import start as start_command

    await start_command(update, context)


async def _finalize_parent_onboarding(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state: dict,
) -> None:
    data = state.get("data") or {}
    user = await _load_user(update.effective_user.id)
    if not user:
        _drop_state(context)
        await update.effective_message.reply_text("Не удалось найти ваш профиль. Выполните /start повторно.")
        return

    def _commit():
        status, person = _resolve_parent_person(
            user_surname=data["user_surname"],
            user_name=data["user_name"],
            user_gender=data["user_gender"],
            athlete_surname=data["parent_athlete_surname"],
            athlete_name=data["parent_athlete_name"],
            athlete_middlename=data.get("parent_athlete_middlename", ""),
            athlete_gender=data["parent_athlete_gender"],
            athlete_birth_date=data["parent_athlete_birth_date"],
        )
        if not person:
            raise ValueError("ambiguous")
        _approve_user_with_person(user, person, reason="group_onboarding")
        return status, person

    try:
        status, person = await sync_to_async(_commit, thread_sensitive=True)()
    except ValueError:
        _drop_state(context)
        context.user_data[ONBOARDING_SKIP_KEY] = True
        await update.effective_message.reply_text(
            "Не удалось однозначно привязать по введённым данным. Продолжим по стандартному сценарию подтверждения."
        )
        from tg_bot.handlers.auth import start as start_command

        await start_command(update, context)
        return

    _drop_state(context)
    context.user_data[ONBOARDING_SKIP_KEY] = True
    if status == "matched":
        await update.effective_message.reply_text(f"Профиль найден и привязан: {person}.")
    else:
        await update.effective_message.reply_text(f"Создали новый профиль и привязали его: {person}.")
    from tg_bot.handlers.auth import start as start_command

    await start_command(update, context)


async def handle_onboarding_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    data = (query.data or "").strip()
    if not data.startswith("onboard:"):
        return False

    state = context.user_data.get(ONBOARDING_STATE_KEY)
    if not state:
        await query.answer("Анкета уже завершена. Нажмите /start.")
        return True

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""
    payload = state.setdefault("data", {})

    if action == "user_gender":
        if value not in {"male", "female"}:
            await query.answer("Некорректный выбор", show_alert=True)
            return True
        payload["user_gender"] = value
        _set_step(context, "role")
        await query.answer("Пол сохранён")
        await query.message.reply_text("Выберите роль:", reply_markup=_onboarding_role_keyboard())
        return True

    if action == "role":
        if value not in {"athlete", "parent"}:
            await query.answer("Некорректный выбор", show_alert=True)
            return True
        payload["role"] = value
        await query.answer("Роль сохранена")
        if value == "athlete":
            _set_step(context, "athlete_middlename")
            await query.message.reply_text("Введите отчество (или '-' если нет).")
        else:
            _set_step(context, "parent_athlete_name")
            await query.message.reply_text("Введите ФИО спортсмена (минимум Фамилия Имя).")
        return True

    if action == "parent_athlete_gender":
        if value not in {"male", "female"}:
            await query.answer("Некорректный выбор", show_alert=True)
            return True
        payload["parent_athlete_gender"] = value
        _set_step(context, "parent_athlete_birth_date")
        await query.answer("Пол сохранён")
        await query.message.reply_text("Введите дату рождения спортсмена в формате ДД.ММ.ГГГГ.")
        return True

    await query.answer("Неизвестное действие", show_alert=True)
    return True


async def handle_onboarding_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get(ONBOARDING_STATE_KEY)
    if not state:
        return False

    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Пожалуйста, отправьте текстовое сообщение.")
        return True

    if text.lower() in {"/cancel", "отмена", "cancel"}:
        _drop_state(context)
        context.user_data[ONBOARDING_SKIP_KEY] = True
        await update.effective_message.reply_text("Анкета отменена. Продолжаем стандартный сценарий.")
        from tg_bot.handlers.auth import start as start_command

        await start_command(update, context)
        return True

    step = state.get("step")
    data = state.setdefault("data", {})

    if step == "user_name":
        surname, name, _ = _parse_full_name(text)
        if not surname or not name:
            await update.effective_message.reply_text("Введите Фамилию и Имя одной строкой.")
            return True
        data["user_surname"] = surname
        data["user_name"] = name
        _set_step(context, "user_gender")
        await update.effective_message.reply_text("Укажите ваш пол:", reply_markup=_onboarding_gender_keyboard("user_gender"))
        return True

    if step == "athlete_middlename":
        data["athlete_middlename"] = "" if text == "-" else text
        _set_step(context, "athlete_birth_date")
        await update.effective_message.reply_text("Введите дату рождения в формате ДД.ММ.ГГГГ.")
        return True

    if step == "athlete_birth_date":
        dob = _parse_birth_date(text)
        if not dob:
            await update.effective_message.reply_text("Не удалось распознать дату. Формат: ДД.ММ.ГГГГ.")
            return True
        data["athlete_birth_date"] = dob
        _set_step(context, "athlete_rank")
        await update.effective_message.reply_text("Введите разряд (или '-' если нет).")
        return True

    if step == "athlete_rank":
        data["athlete_rank"] = "" if text == "-" else text
        _set_step(context, "athlete_club")
        await update.effective_message.reply_text("Введите клуб (или '-' если не важно).")
        return True

    if step == "athlete_club":
        data["athlete_club"] = "" if text == "-" else text
        await _finalize_athlete_onboarding(update, context, state)
        return True

    if step == "parent_athlete_name":
        surname, name, middlename = _parse_full_name(text)
        if not surname or not name:
            await update.effective_message.reply_text("Введите минимум Фамилию и Имя спортсмена.")
            return True
        data["parent_athlete_surname"] = surname
        data["parent_athlete_name"] = name
        data["parent_athlete_middlename"] = middlename
        _set_step(context, "parent_athlete_gender")
        await update.effective_message.reply_text(
            "Укажите пол спортсмена:",
            reply_markup=_onboarding_gender_keyboard("parent_athlete_gender"),
        )
        return True

    if step == "parent_athlete_birth_date":
        dob = _parse_birth_date(text)
        if not dob:
            await update.effective_message.reply_text("Не удалось распознать дату. Формат: ДД.ММ.ГГГГ.")
            return True
        data["parent_athlete_birth_date"] = dob
        await _finalize_parent_onboarding(update, context, state)
        return True

    await update.effective_message.reply_text("Продолжите заполнение анкеты по шагам.")
    return True


async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get(ONBOARDING_STATE_KEY):
        return False
    _drop_state(context)
    context.user_data[ONBOARDING_SKIP_KEY] = True
    await update.effective_message.reply_text("Анкета отменена.")
    return True
