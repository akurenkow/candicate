# Telegram Bot (SYS-17)

Telegram-интерфейс для кандидатов и HR. Регистрация пользователей, режимы candidate/hr, загрузка резюме, RAG-запросы, match-vacancy. Эскалация — отправка сообщений в телеграм-чат администратора и кандидата по owner_id.

## Роли

| Роль | Возможности |
|------|-------------|
| candidate | Загрузка резюме, генерация hash-токена, RAG-вопросы |
| recruiter | Связь с кандидатом по токену, match с вакансией |
| admin | Переключение режима (candidate ↔ hr), все операции обеих ролей |

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Регистрация, выбор языка, главное меню |
| `/lang` | Смена языка (ru/en) |
| `/update` | Загрузка резюме (.txt) |
| `/hash` | Генерация токена для связи с HR (только candidate) |
| `/link [токен]` | Связать HR с кандидатом |
| `/match` | Сопоставление резюме с вакансией (HR; нужен /link) |
| `/switch` | Переключение режима candidate/hr (только admin) |
| `/help` | Справка по роли |

## Состояния (state machine)

- `UNREGISTERED` — не зарегистрирован
- `WAITING_HASH` — ожидание ввода hash для регистрации
- `WAITING_LINK_TOKEN` — HR вводит токен кандидата
- `RESUME_PENDING` — ожидание файла резюме
- `WAITING_VACANCY_TEXT` — ожидание текста вакансии для match
- `ACTIVE` — активный пользователь

## Интеграции

- **API Gateway**: `documents/upload`, `match-vacancy`, enqueue RAG
- **PostgreSQL**: users, hashes (через db.py)

## Переменные окружения

- `TELEGRAM_BOT_TOKEN` — токен бота
- `API_GATEWAY_URL` — базовый URL API Gateway
- `ALLOWED_UPLOAD_USER_IDS` — telegram_id через запятую, кому разрешена загрузка (и авто-роль candidate)
