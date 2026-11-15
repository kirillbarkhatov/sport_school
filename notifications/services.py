from asgiref.sync import async_to_sync
from django.core.mail import send_mail
from telegram import Bot

from config.settings import BOT_TOKEN, DEFAULT_FROM_EMAIL
from organizations.externals import mailing_recipients


def deliver_notification(notification, users=None):
    users_qs = users if users is not None else notification.users.all()
    # R2-5: клубные рассылки не уходят внешним и выпускникам
    users_qs = mailing_recipients(users_qs)

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
