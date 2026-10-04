# Тестовое задание «Голосование»

## Стек

- **Python 3**
- **Django** (Django REST Framework для API)

---

## Суть задачи

Сервис, в котором администратор создаёт голосования с набором персонажей (кандидатов), анонимные пользователи голосуют за понравившегося персонажа, а по завершении определяется победитель.

---

шы

## Роли

- **Администратор (суперпользователь)** - создаёт, редактирует и удаляет голосования и персонажей через стандартную Django-админку (`ModelAdmin`).
- **Неавторизованный пользователь** - может просматривать голосования и отдавать свой голос через API. Авторизация не требуется.

---

## Модели

### Голосование

| Поле               | Описание                                                                                     |
| ---------------------- | ---------------------------------------------------------------------------------------------------- |
| `title`              | Название голосования                                                              |
| `start_date`         | Дата и время начала                                                                  |
| `end_date`           | Дата и время окончания                                                            |
| `early_finish_votes` | Кол-во голосов для досрочного завершения (опционально) |
| `characters`         | Персонажи                                                                                   |

### Персонаж

| Поле       | Описание                  |
| -------------- | --------------------------------- |
| `last_name`  | Фамилия                    |
| `first_name` | Имя                            |
| `patronymic` | Отчество                  |
| `photo`      | Фотография              |
| `age`        | Возраст                    |
| `bio`        | Краткая биография |

---

## Бизнес-логика

Голосование считается **активным**, если одновременно выполнены условия:

- `start_date` < `now` ≤ `end_date`;
- **ни один** персонаж не набрал `early_finish_votes` (если это поле задано).

**Победитель** - персонаж, набравший наибольшее количество голосов к моменту завершения.

---

## REST API

Необходимо реализовать эндпоинты для:

- получения списка голосований (активных и завершённых);
- получения подробной информации о голосовании;
- получения участников голосования;
- получения победителей (для завершённых голосований);
- увеличения количества голосов.

---

## Bonus-задание

Выгрузка результатов голосования в **XLSX** прямо из стандартной Django-админки.

Требования:

1. **Права** - добавить отдельное право (`Permission`), разрешающее экспорт.
2. **Отложенная задача** - формирование XLSX-файла должно выполняться в фоне (Celery).
3. **Email-уведомление** - когда файл готов, текущему авторизованному пользователю уходит письмо со ссылкой/вложением.

Дополнительно (тоже бонус):

- Чистое оформление кода + подробный `README.md`.
- Тесты (`pytest` / `unittest`).

---

## Сдача

- Код публикуется на **GitHub** или **GitLab**.
- В репозитории: исходники + инструкция по запуску + (опционально) ссылка на развёрнутый проект.

# Реализация тестового задания

## Стек

| Слой                            | Технология                                    |
| ----------------------------------- | ------------------------------------------------------- |
| Язык                            | Python 3.12                                             |
| Веб-фреймворк           | Django 6.1                                              |
| API                                 | Django REST Framework + drf-spectacular                 |
| Асинхронные задачи | Celery + Redis                                          |
| БД                                | SQLite (WAL) (БД в ТЗ не указана)         |
| Пакетный менеджер   | `uv` (есть мозможность через pip) |
| Тесты                          | pytest + pytest-django                                  |
| Контейнеризация      | Docker + docker compose                                 |

## Запуск проекта

### Через Docker

```bash
# 1. Скопировать переменные окружения
cp .env.example .env
# (опционально) отредактировать .env под свои нужды

# 2. Собрать образы
docker compose build

# 3. Сгенерировать миграции (один раз)
docker compose run --rm web python manage.py makemigrations voting

# 4. Поднять весь стек: web, redis, celery_worker, celery_beat
docker compose up -d

# 5. Создать суперпользователя
docker compose exec web python manage.py createsuperuser

# 6. (опционально) Заполнить БД тестовыми данными
docker compose exec web python manage.py seed_data --flush
```

Логи воркера (там же видны письма при `EMAIL_BACKEND=console`):

```bash
docker compose logs -f celery_worker
```

Остановка:

```bash
docker compose down            # сохранить данные
docker compose down -v         # снести SQLite и media
```

### Локально (без Docker)

Требуется Python 3.12 и (опционально) `uv`. Redis для Celery - по желанию (без него задачи выполняются в eager-режиме или не выполняются вовсе, но API и админка работают).

```bash
# 1. Виртуальное окружение
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
.\.venv\Scripts\Activate.ps1       # Windows PowerShell

# 2. Зависимости (любой из вариантов)
uv pip install -r requirements.txt
# или
pip install -r requirements.txt

# 3. Переменные окружения
cp .env.example .env

# 4. Миграции
cd src
python manage.py makemigrations voting
python manage.py migrate

# 5. Суперпользователь и тестовые данные
python manage.py createsuperuser
python manage.py seed_data --flush

# 6. Запуск
python .\src\manage.py runserver
```

Опционально - реальный Celery-воркер (для проверки экспорта XLSX):

```bash
# Терминал 1 - Redis
docker run -d --name voting-redis -p 6379:6379 redis:7-alpine

# Терминал 2 - воркер
cd src
celery -A config worker -l info

# Терминал 3 - Django
python manage.py runserver
```

### Предзаполнить БД

```bash
# Docker
docker compose up -d
docker compose exec web python manage.py seed_data --flush
```

Что создаётся:

- суперпользователь `admin / admin`;
- 8 персонажей;
- 4 голосования в разных состояниях:
  - **активное** (можно голосовать прямо сейчас);
  - **завершённое по времени** (с готовыми победителями);
  - **завершённое досрочно** (порог `early_finish_votes = 3`);
  - **будущее** (ещё не началось);
- голоса в завершённых голосованиях - эндпоинт `/winners/` и XLSX-экспорт
  сразу отдают непустой результат.

**Команда идемпотентна:** можно запускать повторно, дубликатов не будет. Флаг `--flush` удаляет старые данные перед созданием.

### Запуск тестов

| Режим          | Команда                                                           | Что попадает в образ                                        |
| ------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| Production          | `docker compose build`                                                 | Только runtime (`uv sync --frozen --no-install-project`)             |
| Development / Tests | `docker compose -f docker-compose.yml -f docker-compose.dev.yml build` | Runtime + dev-группа (`uv sync --frozen --no-install-project --dev`) |

*Продовый образ намеренно **не содержит** `pytest` и прочих dev-инструментов. Всё, что связано с тестами, живёт в override-файле `docker-compose.dev.yml`.*

#### Через Docker

При первом запуске (или после изменения `pyproject.toml` / `uv.lock`) соберите
dev-образы:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml build
```

Запустить все тесты одной командой:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm tests
```

Передать аргументы в pytest (конкретный файл, `-k`, `--cov`):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
    run --rm tests pytest -v voting/tests/test_api.py

docker compose -f docker-compose.yml -f docker-compose.dev.yml \
    run --rm tests pytest --cov=voting --cov-report=term-missing
```

#### Локально (без Docker)

Нужен Python 3.12 и [`uv`](https://docs.astral.sh/uv/). Из **корня проекта**
(там, где `pyproject.toml`):

```bash
# 1. Установить зависимости, включая dev-группу.
#    uv создаст .venv в корне и поставит всё по uv.lock.
uv sync --dev

# 2. Запустить тесты через uv (venv активировать не нужно).
uv run pytest -v
```

Альтернатива — активировать venv вручную:

```bash
source .venv/bin/activate           # Linux/macOS
.\.venv\Scripts\Activate.ps1        # Windows PowerShell

pytest -v
```

Покрытие по файлам:

- `tests/test_models.py` — свойства `is_active`, `get_winners`, `cast_vote`,
  `build_fingerprint`;
- `tests/test_api.py` — все эндпоинты, фильтры, пагинация, throttling;
- `tests/test_tasks.py` — сборка XLSX, отправка письма, отсутствие файла на диске;
- `tests/test_admin.py` — экспорт из админки, проверка пермиссии, отсутствие email.

## REST API

Базовый префикс - `/api/`.

| Метод | URL                           | Что делает                                                                            |
| ---------- | ----------------------------- | ---------------------------------------------------------------------------------------------- |
| `GET`    | `/votings/`                 | Список голосований (`?status=active\|finished`, `?page=`, `?page_size=`) |
| `GET`    | `/votings/{id}/`            | Детали голосования с участниками                                  |
| `GET`    | `/votings/{id}/characters/` | Участники, отсортированы по голосам и ФИО                   |
| `GET`    | `/votings/{id}/winners/`    | Победители (только для завершённых)                              |
| `POST`   | `/votings/{id}/vote/`       | Проголосовать`{"character_id": 1}`                                              |
| `GET`    | `/schema/`                  | OpenAPI-схема                                                                             |
| `GET`    | `/docs/`                    | Swagger UI                                                                                     |
| `GET`    | `/redoc/`                   | ReDoc                                                                                          |

Полная документация с примерами и кодами ответов - в `/api/docs/`.

## Дополнительно реализовано

### Защита от накрутки голосов

Реализовано три уровня:

1. **Отпечаток голосующего** - `sha256(IP + User-Agent + X-Client-Id)`. Клиент может присылать заголовок `X-Client-Id` со случайным идентификатором, сохранённым в `localStorage`. Это позволяет отличить двух пользователей за одним NAT.
2. **Уникальный constraint** `UNIQUE(voting, voter_fingerprint)` в БД - второй голос с тем же отпечатком невозможен даже при параллельных запросах.
3. **Throttling** `30/hour` на `POST /vote/` - защита от перебора с одного IP.

### Конкурентная запись голосов

Дополнительно `select_for_update` в `cast_vote` сериализует конкурентные голоса (полноценно работает на PostgreSQL, на SQLite - no-op, там защита держится на constraint).

Помимо обязательных пунктов из задания, в проекте есть:

### Документация API

- **OpenAPI 3.0 через drf-spectacular** - Swagger UI (`/api/docs/`), ReDoc (`/api/redoc/`), готовая схема (`/api/schema/`). Все эндпоинты описаны через `@extend_schema_view` на уровне класса: теги, summary, description, параметры, коды ответов, примеры ошибок.

### Seed-команда

- `python manage.py seed_data` - заполняет БД готовым сценарием: суперпользователь, персонажи, 8 голосований в разных состояниях, голоса. Идемпотентна,  поддерживает `--flush`.

### Отправка почты

Поддерживаются два режима — переключаются переменными окружения в `.env`.

#### Mailpit

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=mailpit
EMAIL_PORT=1025
EMAIL_USE_TLS=False
EMAIL_USE_SSL=False
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_TIMEOUT=10
DEFAULT_FROM_EMAIL=noreply@voting.local
```

Запуск:

```bash
docker compose --profile mailpit up -d
```

Web-интерфейс: http://localhost:8025 — там видны все письма, вложения и MIME.

#### Яндекс (боевой SMTP)

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.yandex.ru
EMAIL_PORT=465
EMAIL_USE_SSL=True
EMAIL_USE_TLS=False
EMAIL_HOST_USER=your@yandex.ru
EMAIL_HOST_PASSWORD=app-password-here
EMAIL_TIMEOUT=10
DEFAULT_FROM_EMAIL=your@yandex.ru
```
