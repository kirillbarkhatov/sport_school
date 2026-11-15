-- Обезличивание контактов в копии прода на staging. Имена и спортивные данные остаются для проверок.
BEGIN;
UPDATE users_user SET
    email = CASE WHEN email IS NULL THEN NULL ELSE 'user' || id || '@staging.invalid' END,
    phone = NULL, tg_id = NULL, tg_username = NULL, token = NULL;
UPDATE school_person SET email = NULL, phone = NULL, telegram = NULL, telegram_id = NULL;
UPDATE whatsapp_whatsappchatmember SET phone = '', push_name = '';
UPDATE bot_telegramparticipant SET username = '' WHERE username IS NOT NULL;
COMMIT;
