# Расписание через cron-job.org (вариант B)

Репозиторий остаётся **Private**.  
Ключи Finviz / Finnhub / Telegram остаются в **GitHub Secrets**.  
Время задаём точно по **America/New_York**.

Нужно сделать 2 вещи:
1. Создать токен GitHub (PAT)
2. Создать 5 заданий на [cron-job.org](https://cron-job.org)

---

## Часть 1. Обновите workflow на GitHub

1. Откройте репозиторий `sepa-signal-bot`
2. Замените файл `.github/workflows/sepa.yml` содержимым с вашего ПК  
   (`SEPA Siglal\.github\workflows\sepa.yml`)
3. Также обновите `main.py`, если ещё не самый новый
4. **Commit changes**

Проверка: **Actions → SEPA Bot → Run workflow → morning** — в Telegram должно прийти сообщение.

---

## Часть 2. Создайте токен GitHub (PAT)

Токен нужен, чтобы cron-job.org мог «нажать Run workflow» за вас.

1. Откройте: [https://github.com/settings/tokens](https://github.com/settings/tokens)
2. **Generate new token** → **Generate new token (classic)**
3. Note (название): `sepa-cron`
4. Expiration: например **90 days** (потом нужно будет обновить) или **No expiration**
5. Галочки (scopes):
   - ✅ **`repo`** (весь блок)
   - ✅ **`workflow`**
6. Внизу **Generate token**
7. **Скопируйте токен сразу** (вида `ghp_....`) и сохраните в блокнот  
   Повторно GitHub его не покажет.

⚠ Этот токен **нельзя** класть в код репозитория. Он будет только в cron-job.org.

---

## Часть 3. Регистрация на cron-job.org

1. Откройте [https://cron-job.org](https://cron-job.org)
2. Зарегистрируйтесь / войдите (можно через email)
3. Подтвердите почту, если попросят

---

## Часть 4. Общий шаблон одного задания

Создайте **5 отдельных Cronjobs**.  
Ниже — что у всех общее, потом таблица отличий.

### Общие поля для каждого задания

| Поле | Значение |
|---|---|
| Title | смотрите таблицу ниже |
| Address (URL) | `https://api.github.com/repos/Leo1255serg/sepa-signal-bot/actions/workflows/sepa.yml/dispatches` |
| Schedule | по таблице + timezone **America/New_York** |
| Request method | **POST** |
| Enable job | ✅ включено |

### Заголовки (Headers) — одинаковые для всех 5

Добавьте 3 заголовка:

1. Name: `Accept`  
   Value: `application/vnd.github+json`

2. Name: `Authorization`  
   Value: `Bearer ВАШ_ТОКЕН`  
   Пример: `Bearer ghp_xxxxxxxx`  
   (слово `Bearer`, пробел, потом токен)

3. Name: `Content-Type`  
   Value: `application/json`

### Тело запроса (Request body) — разное

Включите отправку body (JSON) и вставьте одну из строк ниже.

---

## Часть 5. Пять заданий (что именно создать)

### 1) Утро — новые идеи

- **Title:** `SEPA morning`
- **Body:**
```json
{"ref":"main","inputs":{"mode":"morning"}}
```
- **Schedule:** каждый будний день в **10:00**
- **Timezone:** `America/New_York`
- Дни: Mon–Fri (или «weekdays»)

### 2) День — сверка 13:00

- **Title:** `SEPA midday`
- **Body:**
```json
{"ref":"main","inputs":{"mode":"midday"}}
```
- **Schedule:** будни **13:00**
- **Timezone:** `America/New_York`

### 3) Перед закрытием — сверка 15:30

- **Title:** `SEPA close_check`
- **Body:**
```json
{"ref":"main","inputs":{"mode":"close_check"}}
```
- **Schedule:** будни **15:30**
- **Timezone:** `America/New_York`

### 4) Пятница — недельный отчёт

- **Title:** `SEPA weekly`
- **Body:**
```json
{"ref":"main","inputs":{"mode":"weekly"}}
```
- **Schedule:** только **Friday 16:05**
- **Timezone:** `America/New_York`

### 5) 1-е число — месячный отчёт

- **Title:** `SEPA monthly`
- **Body:**
```json
{"ref":"main","inputs":{"mode":"monthly"}}
```
- **Schedule:** день **1** каждого месяца в **09:00**
- **Timezone:** `America/New_York`

---

## Часть 6. Как выбрать время в интерфейсе cron-job.org

Интерфейс может чуть отличаться, но логика такая:

1. **Cronjobs → CREATE CRONJOB**
2. URL и method POST
3. Вкладка **Schedule**:
   - Execution timezone = **America/New_York**
   - либо «every day at …» + снимите субботу/воскресенье  
   - либо custom cron, примеры:

| Задание | Cron (если просят выражение) |
|---|---|
| morning | `0 10 * * 1-5` |
| midday | `0 13 * * 1-5` |
| close_check | `30 15 * * 1-5` |
| weekly | `5 16 * * 5` |
| monthly | `0 9 1 * *` |

4. Вкладка **Advanced** / **Headers** — 3 заголовка выше  
5. Body — JSON из таблицы  
6. **CREATE** / **SAVE**

Повторите для всех 5 заданий.

---

## Часть 7. Проверка, что всё работает

### Быстрый тест одного задания

1. В cron-job.org откройте `SEPA midday`
2. Нажмите **Run now** / **Execute now** (если есть)
3. Через ~30–60 сек:
   - в GitHub **Actions** появится новый run
   - в Telegram придёт сверка

Если кнопки «Run now» нет — временно поставьте время через 2–3 минуты от текущего NY-времени, дождитесь, потом верните расписание.

### Если ошибка 401 / 403

- токен скопирован не полностью
- забыли слово `Bearer ` перед токеном
- не стоят галочки `repo` и `workflow`

### Если ошибка 404

- неверный URL (логин/`sepa-signal-bot`/`sepa.yml`)
- workflow файл ещё не закоммичен в ветку `main`

### Если 204 / Success, но нет TG

- откройте лог Actions → job `run`
- проверьте Secrets бота

---

## Что больше не нужно

- Широкие окна типа 15:20–16:50 — **не нужны**
- Встроенный GitHub `schedule` — **отключён** (он как раз глючил на Free+Private)

Дальше бот должен стартовать в **ровно** заданное NY-время через cron-job.org.

---

## Безопасность

1. PAT храните только в cron-job.org  
2. Не публикуйте PAT в чат / скрины  
3. Если токен утёк — удалите его в GitHub Settings → Tokens и создайте новый  
4. Раз в срок Expiration — обновите токен в заголовке Authorization у всех 5 jobs
