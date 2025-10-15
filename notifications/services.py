from asgiref.sync import async_to_sync
from django.core.mail import send_mail
from telegram import Bot

from config.settings import BOT_TOKEN, DEFAULT_FROM_EMAIL


def deliver_notification(notification, users=None):
    users_qs = users if users is not None else notification.users.all()

    if notification.send_to_telegram and BOT_TOKEN:
        bot = Bot(token=BOT_TOKEN)
        for user in users_qs.exclude(tg_id__isnull=True):
            async_to_sync(bot.send_message)(
                chat_id=user.tg_id,
                text=notification.message,
            )

    if notification.send_to_email:
        recipients = [u.email for u in users_qs.exclude(email="")]
        if recipients:
            send_mail(
                notification.title,
                notification.message,
                DEFAULT_FROM_EMAIL,
                recipients,
            )
