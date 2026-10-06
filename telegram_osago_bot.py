"""
Telegram-бот: проверка ОСАГО на nsis.ru, сценарий на кнопках.

Сценарий для пользователя:
  1. /start -> приветствие + кнопка "Проверить ОСАГО"
  2. Нажал кнопку -> бот просит ввести VIN или госномер
  3. Ввёл -> бот открывает форму на nsis.ru
     - если есть капча (цифры на картинке) -> бот присылает фото,
       просит ввести цифры
     - если капчи нет -> сразу результат
  4. Ввёл капчу -> бот отправляет форму, присылает результат
  5. Под результатом -> кнопка "Проверить ещё раз", сценарий начинается заново

ОГРАНИЧЕНИЕ: слайдер-капчу ("сдвиньте ползунок") этот бот не проходит —
такое нельзя решить, просто прислав текст, а headless-браузер без
экрана физически не может подвинуть ползунок. Если увидите такое в
логах — этот конкретный случай нужно проверить руками, не через бота.

Установка зависимостей:
    pip install -r requirements.txt

Переменные окружения:
    TELEGRAM_BOT_TOKEN — токен бота от @BotFather
    ALLOWED_CHAT_ID    — ваш chat_id, чтобы боту не писали посторонние
"""

import os
import tempfile
import logging

import requests

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("osago-bot")

NSIS_CHECK_URL = "https://nsis.ru/products/osago/check/"
CAPTCHA_SCREENSHOT_PATH = os.path.join(tempfile.gettempdir(), "captcha.png")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])

# Состояние диалога на каждый chat_id.
# state: "idle" | "awaiting_vehicle" | "awaiting_captcha"
# driver/wait заполняются, когда открыт браузер и ждём капчу
SESSIONS: dict[int, dict] = {}

CHECK_BUTTON = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🚗 Проверить ОСАГО", callback_data="check_osago")]]
)

# Клавиатура под результатом проверки: проверить ещё раз или вернуться в меню
RESULT_KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("🚗 Проверить ещё раз", callback_data="check_osago")],
        [InlineKeyboardButton("🏠 В меню", callback_data="main_menu")],
    ]
)

GREETING_TEXT = (
    "Привет! Я проверяю ОСАГО на nsis.ru.\n\n"
    "Нажмите кнопку ниже, чтобы начать проверку."
)


# Путь к chromedriver ищем в интернете только ОДИН раз за всё время
# работы бота (при первом запуске), а не при каждой проверке —
# это экономит пару секунд на каждый /check.
_CACHED_DRIVER_PATH = None


def get_driver_path() -> str:
    global _CACHED_DRIVER_PATH
    if _CACHED_DRIVER_PATH is None:
        _CACHED_DRIVER_PATH = ChromeDriverManager().install()
    return _CACHED_DRIVER_PATH


def create_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--mute-audio")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    # Картинки саму капчу мы скачиваем отдельно напрямую (через requests),
    # а не смотрим на то, что отрисовал браузер — значит браузеру вообще
    # не нужно грузить картинки со страницы (баннеры, логотипы и т.п.)
    options.add_experimental_option(
        "prefs", {"profile.managed_default_content_settings.images": 2}
    )
    # Не ждём полной загрузки страницы (все картинки/шрифты/счётчики) —
    # нам достаточно момента, когда готов DOM и можно искать элементы
    options.page_load_strategy = "eager"

    service = Service(get_driver_path())
    return webdriver.Chrome(service=service, options=options)


def is_allowed(chat_id: int) -> bool:
    return chat_id == ALLOWED_CHAT_ID


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    log.info(f"Получена команда /start от chat_id={chat_id} (разрешён: {ALLOWED_CHAT_ID})")
    if not is_allowed(chat_id):
        return
    SESSIONS[chat_id] = {"state": "idle"}
    await update.message.reply_text(GREETING_TEXT, reply_markup=CHECK_BUTTON)


async def check_button_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    log.info(f"Нажата кнопка от chat_id={chat_id} (разрешён: {ALLOWED_CHAT_ID})")
    if not is_allowed(chat_id):
        return

    await query.answer()  # убираем "часики" на кнопке в интерфейсе Telegram

    SESSIONS[chat_id] = {"state": "awaiting_vehicle"}
    await query.message.reply_text("Введите VIN или госномер для проверки:")


async def main_menu_button_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    log.info(f"Нажата кнопка 'В меню' от chat_id={chat_id} (разрешён: {ALLOWED_CHAT_ID})")
    if not is_allowed(chat_id):
        return

    await query.answer()

    # На всякий случай закрываем браузер, если он был открыт (ждали капчу)
    session = SESSIONS.get(chat_id, {})
    driver = session.get("driver")
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass

    SESSIONS[chat_id] = {"state": "idle"}
    await query.message.reply_text(GREETING_TEXT, reply_markup=CHECK_BUTTON)


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    log.info(f"Получено сообщение от chat_id={chat_id} (разрешён: {ALLOWED_CHAT_ID})")
    if not is_allowed(chat_id):
        return

    session = SESSIONS.get(chat_id, {"state": "idle"})
    state = session.get("state", "idle")

    if state == "awaiting_vehicle":
        await handle_vehicle_input(update, context, session)
    elif state == "awaiting_captcha":
        await handle_captcha_input(update, context, session)
    else:
        # Бот ничего не ждёт от пользователя -> подсказываем нажать кнопку
        await update.message.reply_text(
            "Нажмите кнопку, чтобы начать проверку:", reply_markup=CHECK_BUTTON
        )


async def handle_vehicle_input(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict):
    chat_id = update.effective_chat.id
    query_value = update.message.text.strip()

    await update.message.reply_text(f"Ищу «{query_value}»...")

    driver = create_driver()
    driver.get(NSIS_CHECK_URL)
    wait = WebDriverWait(driver, 20)

    # Пытаемся сразу закрыть баннер cookies/дисклеймер, если он есть —
    # чтобы он не перекрывал другие элементы на следующих шагах.
    # Если баннера нет или кнопка называется иначе — просто пропускаем.
    try:
        accept_button = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Принять')]"))
        )
        driver.execute_script("arguments[0].click();", accept_button)
    except Exception:
        pass

    try:
        # --- ТОЧКА, КОТОРУЮ НУЖНО СВЕРИТЬ С РЕАЛЬНОЙ РАЗМЕТКОЙ ---
        input_field = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='licenseplate']"))
        )
        input_field.clear()
        input_field.send_keys(query_value)

        try:
            captcha_container = driver.find_element(By.CSS_SELECTOR, "div.nsiscaptcha__img")
        except Exception:
            captcha_container = None

        if captcha_container is not None:
            # Картинку мы всё равно скачиваем отдельно напрямую по её src
            # (не через отрисовку в браузере, которую мы как раз отключили
            # для скорости) — поэтому ждать реальной загрузки картинки не
            # нужно, достаточно дождаться, пока у <img> появится сам src
            try:
                captcha_img = wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.nsiscaptcha__img img"))
                )
                wait.until(lambda d: captcha_img.get_attribute("src"))
            except Exception:
                captcha_img = None
        else:
            captcha_img = None

        if captcha_img is None:
            # капчи нет — сразу отправляем форму и завершаем сценарий
            result_text = submit_and_read_result(driver, wait)
            driver.quit()
            SESSIONS[chat_id] = {"state": "idle"}
            await send_result(update, result_text)
            return

        # капча есть — скачиваем файл картинки НАПРЯМУЮ по её src, используя
        # те же куки, что и у браузера (иначе сервер сайта её не отдаст).
        # Это надёжнее скриншотов — получаем оригинальный файл, а не то,
        # что нарисовал headless-браузер.
        captcha_src = captcha_img.get_attribute("src")
        if captcha_src.startswith("/"):
            page_origin = driver.execute_script("return window.location.origin")
            captcha_src = page_origin + captcha_src

        browser_cookies = driver.get_cookies()
        http_session = requests.Session()
        for c in browser_cookies:
            http_session.cookies.set(c["name"], c["value"])

        response = http_session.get(
            captcha_src,
            headers={
                "User-Agent": driver.execute_script("return navigator.userAgent"),
                "Referer": NSIS_CHECK_URL,
            },
            timeout=15,
        )
        response.raise_for_status()

        with open(CAPTCHA_SCREENSHOT_PATH, "wb") as f:
            f.write(response.content)

        SESSIONS[chat_id] = {"state": "awaiting_captcha", "driver": driver, "wait": wait}

        with open(CAPTCHA_SCREENSHOT_PATH, "rb") as f:
            await update.message.reply_photo(
                f, caption="Введите цифры с картинки ответным сообщением"
            )
    except Exception as e:
        driver.quit()
        SESSIONS[chat_id] = {"state": "idle"}
        log.exception("Ошибка при заполнении формы")
        await send_result(update, f"Не получилось открыть форму: {e}")


async def handle_captcha_input(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict):
    chat_id = update.effective_chat.id
    captcha_text = update.message.text.strip()

    if not captcha_text.isdigit():
        await update.message.reply_text(
            "Капча состоит только из цифр. Введите ещё раз, посмотрев на картинку выше."
        )
        return  # состояние не меняем, ждём повторный ввод

    driver, wait = session["driver"], session["wait"]

    try:
        # --- ТОЧКА, КОТОРУЮ НУЖНО СВЕРИТЬ С РЕАЛЬНОЙ РАЗМЕТКОЙ ---
        captcha_input = driver.find_element(By.CSS_SELECTOR, "input[name='captcha']")
        captcha_input.clear()
        captcha_input.send_keys(captcha_text)

        result_text = submit_and_read_result(driver, wait)
        await send_result(update, result_text)
    except Exception as e:
        log.exception("Ошибка при отправке формы")
        await send_result(update, f"Не получилось: {e}")
    finally:
        driver.quit()
        SESSIONS[chat_id] = {"state": "idle"}


async def send_result(update: Update, result_text: str):
    """Присылает результат и кнопки: проверить ещё раз или вернуться в меню."""
    await update.message.reply_text(result_text, reply_markup=RESULT_KEYBOARD)


def submit_and_read_result(driver, wait) -> str:
    submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
    # Обычный .click() может быть перехвачен баннером cookies/дисклеймером,
    # который визуально лежит поверх кнопки — кликаем через JS, это надёжнее
    driver.execute_script("arguments[0].click();", submit_button)

    try:
        # Результат — это один или несколько блоков dl.dataList__list
        # (может быть несколько полисов на один автомобиль)
        result_lists = wait.until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "dl.dataList__list"))
        )
        parts = [el.text.strip() for el in result_lists if el.text.strip()]
        if not parts:
            raise Exception("Пустой результат")
        return "\n\n— — —\n\n".join(parts)
    except Exception:
        return "Результат не получен — возможно, неверная капча или изменилась вёрстка страницы."


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(check_button_pressed, pattern="^check_osago$"))
    app.add_handler(CallbackQueryHandler(main_menu_button_pressed, pattern="^main_menu$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    log.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
