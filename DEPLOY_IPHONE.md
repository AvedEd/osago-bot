# Разворачиваем бота с iPhone: Oracle Cloud (VPS) + GitHub + Termius

Всё делается в двух приложениях: **Safari** (для Oracle Cloud и GitHub)
и **Termius** (для команд на сервере). Компьютер не нужен.

---

## Шаг 1. Заливаем код бота на GitHub

1. Откройте github.com в Safari, войдите в свой аккаунт.
2. Нажмите **+** (вверху) → **New repository**.
   - Имя: например `osago-bot`
   - Видимость: **Private** (рекомендуется — репозиторий свой, посторонним
     файлы не нужны; но даже Public подойдёт, если не будете класть туда
     токен бота — токен пойдёт отдельно, переменной окружения на сервере,
     не в код)
   - Create repository
3. В свежесозданном репозитории: **Add file → Upload files**.
4. Из приложения «Файлы» на iPhone выберите `telegram_osago_bot.py` и
   `requirements.txt` (те, что я сделал в этом чате — сохраните их себе
   в Файлы, если ещё не сохранили).
5. Внизу **Commit changes** — прямо в main-ветку.

Теперь код лежит по адресу вида
`https://github.com/ВАШ_ЛОГИН/osago-bot`.

---

## Шаг 2. Создаём бесплатный VPS в Oracle Cloud

1. В Safari откройте https://www.oracle.com/cloud/free/ → **Start for free**.
2. Зарегистрируйтесь (нужен email и телефон; попросят привязать карту
   для верификации личности — с неё ничего не спишут, пока сами не
   перейдёте на платный тариф, чего делать не будем).
3. После входа в консоль: значок ≡ (меню) → **Compute → Instances**
   → **Create instance**.
4. Настройки инстанса:
   - **Image**: Canonical Ubuntu 22.04 (или 24.04)
   - **Shape**: нажмите «Change shape» → выберите вкладку **Ampere**
     → `VM.Standard.A1.Flex` (Always Free, до 4 OCPU / 24GB RAM —
     этого с запасом хватит на headless Chrome).
     Если система пишет «Out of capacity» — попробуйте другой регион
     при регистрации или возьмите вариант `VM.Standard.E2.1.Micro`
     (AMD, тоже Always Free, но всего 1GB RAM — тогда в шаге 4 обязательно
     сделайте своп, инструкция ниже).
   - **Networking**: оставьте автоматическое создание VCN, убедитесь что
     стоит галочка **Assign a public IPv4 address**.
   - **Add SSH keys**: выберите **Generate a key pair for me**, затем
     нажмите **Save private key** — файл `.key` скачается в Safari →
     сохраните в приложение «Файлы» (папку запомните, она понадобится
     в Termius).
5. **Create**. Через 1–2 минуты статус станет **Running** — на странице
   инстанса запишите **Public IP Address**.

---

## Шаг 3. Подключаемся через Termius

1. Откройте Termius → **Keychain** (внизу) → **+** → **Import from file**
   → выберите скачанный `.key` из Файлов. Дайте ключу имя, например
   `oracle-osago`.
2. **Hosts** → **+** (New Host):
   - Address: ваш Public IP из Oracle
   - Username: `ubuntu`
   - Key: выберите `oracle-osago`, который импортировали
   - Save → тапните по хосту, чтобы подключиться.

Если всё верно — окажетесь в терминале Ubuntu на своём VPS.

---

## Шаг 4. Настройка сервера (команды вводите в Termius)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git chromium-browser
```

**Если у вас Micro-инстанс (1GB RAM)** — добавьте своп, иначе Chrome
может падать по нехватке памяти:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Клонируем код с GitHub:

```bash
git clone https://github.com/ВАШ_ЛОГИН/osago-bot.git
cd osago-bot
```

Если репозиторий **Private**, git спросит логин/пароль — вместо пароля
GitHub требует **Personal Access Token**: Safari → github.com →
аватар → **Settings → Developer settings → Personal access tokens →
Generate new token (classic)**, права `repo`, скопируйте токен и
вставьте его вместо пароля при клонировании (логин — ваш обычный).

Виртуальное окружение и зависимости:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Шаг 5. Telegram-бот: токен и chat_id

1. В Telegram: **@BotFather** → `/newbot` → получите токен.
2. Напишите что-нибудь своему новому боту, затем в Safari откройте
   `https://api.telegram.org/bot<ВАШ_ТОКЕН>/getUpdates` и найдите
   `"chat":{"id": ЧИСЛО` — это ваш `chat_id`.

Тестовый запуск (в Termius, всё ещё в папке `osago-bot`):

```bash
export TELEGRAM_BOT_TOKEN="ваш_токен"
export ALLOWED_CHAT_ID="ваш_chat_id"
python telegram_osago_bot.py
```

Напишите боту в Telegram `/check А111АА197` — если дошли скриншот
капчи и после вашего ответа результат, всё настроено верно.

> Селекторы полей в скрипте (`input[name='vehicleId']`,
> `img.captcha-image`, `.osago-check-result`) — примерные, их нужно
> свериться с реальной страницей nsis.ru через «Поделиться → Запросить
> версию для ПК» в Safari на iPhone, затем инструменты разработчика,
> либо через десктопный браузер при случае.

Остановите тест: `Ctrl+C`.

---

## Шаг 6. Постоянная работа через systemd

```bash
sudo tee /etc/systemd/system/osago-bot.service > /dev/null <<'EOF'
[Unit]
Description=OSAGO check Telegram bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/osago-bot
Environment="TELEGRAM_BOT_TOKEN=ваш_токен"
Environment="ALLOWED_CHAT_ID=ваш_chat_id"
ExecStart=/home/ubuntu/osago-bot/venv/bin/python /home/ubuntu/osago-bot/telegram_osago_bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable osago-bot
sudo systemctl start osago-bot
```

Проверка:

```bash
sudo systemctl status osago-bot
sudo journalctl -u osago-bot -f     # логи в реальном времени
```

Теперь бот работает постоянно и переживёт перезагрузку VPS.

---

## Шаг 7. Обновление кода в будущем

Поправили что-то в `telegram_osago_bot.py` на GitHub (прямо в браузере,
через «Edit» на странице файла) → на сервере:

```bash
cd ~/osago-bot
git pull
sudo systemctl restart osago-bot
```

---

## Базовая безопасность

- `sudo ufw allow OpenSSH && sudo ufw enable` — входящие порты боту не
  нужны, он сам стучится наружу.
- Не кладите токен бота в код/GitHub — только через `Environment=` в
  systemd, как выше.
- Держите систему обновлённой: время от времени `sudo apt update &&
  sudo apt upgrade -y`.
