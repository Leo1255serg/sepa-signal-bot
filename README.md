# SEPA Signal Bot (paper trading)

Бот для бумажного портфеля **$100 000**.  
Работает на **бесплатном GitHub Actions** по расписанию Нью-Йорка и шлёт рекомендации в Telegram.

> Это не реальная торговля. Бот только считает позиции и пишет отчёты.

---

## Что делает бот

| Время (America/New_York) | Действие |
|---|---|
| Пн–Пт **10:00** | Сверка старых позиций (стоп / тейк / макс. **14 дней**) → новые входы → сообщение в TG по каждой идее |
| Пн–Пт **13:00** | Сверка стоп / тейк |
| Пн–Пт **15:30** | Сверка за 30 минут до закрытия рынка |
| **Пятница ~16:05** | Excel-отчёт за неделю (открытые, закрытые, почему, PnL, winrate) |
| **1-е число, 09:00** | Excel-отчёт за прошлый месяц |
| Выходные и праздники NYSE | Молчит (кроме месячного отчёта 1-го числа) |

Параметры стратегии (как в исходном коде):

- капитал `$100 000`, риск `0.5%` на сделку, R:R `2`
- до `12` сигналов в день, макс. `30%` капитала в открытых позициях
- цена от `$10`, объём от `1 000 000`
- исключены Big Ten: AAPL, MSFT, GOOGL, AMZN, TSLA, META, NVDA, BRK-B, JPM, WMT
- long и short
- фильтр по **50-дневной MA** из Finviz  
  *(в Elite CSV есть SMA50 в % к цене; отдельной колонки EMA50 в export нет — используем SMA50 как MA-фильтр)*

---

## Важно про ключи

**Никогда не публикуйте** токены Finviz / Finnhub / Telegram в коде.  
Они хранятся только в **GitHub Secrets** (или в локальном `.env`, который в git не попадает).

Старый файл `SEPA боевой.txt` с ключами в тексте **нельзя** загружать на GitHub — он в `.gitignore`.

---

## Пошаговая установка (с нуля)

### 1) Аккаунт GitHub

1. Откройте [https://github.com](https://github.com) и зарегистрируйтесь / войдите.
2. Установите [GitHub Desktop](https://desktop.github.com/) (проще всего для новичка) **или** используйте сайт GitHub.

### 2) Создайте ПРИВАТНЫЙ репозиторий

1. На GitHub: **New repository**
2. Имя, например: `sepa-signal-bot`
3. Обязательно выберите **Private**
4. **Не** ставьте галочки “Add README / .gitignore” (у нас уже есть файлы)
5. Создайте репозиторий

### 3) Загрузите этот проект

**Вариант A — GitHub Desktop (рекомендуется):**

1. File → Add Local Repository → укажите папку `SEPA Siglal`
2. Если спросит — Publish repository → снимите “Keep this code private” **нельзя снимать**: оставьте private
3. Publish

**Вариант B — через сайт:**

1. В пустом private-репозитории: **uploading an existing file**
2. Перетащите все файлы проекта **кроме** `SEPA боевой.txt` и `.env`
3. Commit changes

Нужные файлы:

- `main.py`
- `requirements.txt`
- `README.md`
- `.gitignore`
- `.env.example`
- `.github/workflows/sepa.yml`
- `data/` (папка со `state.json` и `.gitkeep`)

### 4) Добавьте Secrets

1. Репозиторий → **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret** — создайте 4 секрета:

| Name | Value |
|---|---|
| `FINVIZ_API_TOKEN` | ваш Finviz Elite token |
| `FINNHUB_API_TOKEN` | ваш Finnhub token |
| `TELEGRAM_TOKEN` | токен бота от @BotFather |
| `TELEGRAM_CHAT_ID` | `-1002805161735` (ваш чат) |

### 5) Первый ручной запуск (проверка)

1. Вкладка **Actions**
2. Слева выберите workflow **SEPA Bot**
3. **Run workflow** → mode = `morning` (или `auto`)
4. Дождитесь зелёной галочки
5. Проверьте Telegram

Если Actions не видно: Settings → Actions → General → разрешите Actions.

### 6) Дальше работает само

GitHub сам запускает workflow по cron.  
Внутри Python смотрит точное время **America/New_York** и решает, какое задание выполнять.  
Портфель (`data/*.xlsx`, `data/state.json`) сохраняется обратно в репозиторий после каждого запуска.

---

## Локальный запуск (по желанию)

```bash
pip install -r requirements.txt
copy .env.example .env
# заполните ключи в .env
python main.py --mode morning
```

С вашей сети в РФ для Telegram может понадобиться VPN.  
На GitHub Actions VPN не нужен — сообщения уходят с серверов GitHub.

---

## Режимы `main.py`

```bash
python main.py --mode auto         # сам выбирает задачу по NY-времени
python main.py --mode morning      # 10:00 логика
python main.py --mode midday       # 13:00 сверка
python main.py --mode close_check  # 15:30 сверка
python main.py --mode weekly       # пятничный Excel
python main.py --mode monthly      # месячный Excel
```

---

## Файлы данных

| Файл | Назначение |
|---|---|
| `data/portfolio_log.xlsx` | открытые и закрытые позиции |
| `data/signals_log.xlsx` | история сигналов |
| `data/weekly_report.xlsx` | недельный отчёт |
| `data/monthly_report.xlsx` | месячный отчёт |
| `data/state.json` | какие задания уже выполнялись сегодня |

Старт после выкладки — **с нуля** (пустой портфель).

---

## Бесплатный лимит GitHub

На бесплатном аккаунте для **private** репозиториев обычно есть минуты Actions в месяц.  
Этот бот запускается коротко и несколько раз в торговый день — для такой нагрузки обычно хватает.

Если вдруг Actions остановится из‑за лимита — подождите сброса лимита в следующем месяце или временно запускайте вручную через **Run workflow**.

---

## Безопасность

1. Репозиторий только **Private**
2. Ключи только в **Secrets**
3. Не коммитьте `SEPA боевой.txt` и `.env`
4. Если ключи уже светились в старом файле — лучше **перевыпустить** токены (Finviz / Finnhub / Telegram) и прописать новые в Secrets
