import matplotlib
import matplotlib.pyplot as plt
import pandas as pd
from googletrans import Translator
from datetime import timedelta, datetime
import pypdf
from multiprocessing.dummy import Pool as ThreadPool
from openpyxl import load_workbook
import io
import xlrd
import random
from PIL import Image, ImageDraw, ImageFont
from pdf2image import convert_from_bytes
from io import BytesIO
import calendar
from datetime import date
import re
from bs4 import BeautifulSoup
import requests
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from telebot import types
import pytz
import time
import threading
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import telebot
import logging
import schedule
import sqlite3

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database setup

# Thread-local storage for SQLite connections
thread_local = threading.local()


def get_db_connection():
    """Returns a thread-local SQLite connection."""
    if not hasattr(thread_local, "conn"):
        thread_local.conn = sqlite3.connect(
            f'{current_dir}/bot.db', check_same_thread=False)
    return thread_local.conn


def init_db():
    """Initializes the database schema."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (chat_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, username TEXT)''')
    conn.commit()


def get_or_create_user(chat_id, first_name, last_name, username):
    """Gets or creates a user in the database."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
    user = c.fetchone()
    if user is None:
        c.execute("INSERT INTO users (chat_id, first_name, last_name, username) VALUES (?, ?, ?, ?)",
                  (chat_id, first_name, last_name, username))
        conn.commit()
        return True
    return False


def get_all_users():
    """Returns all users from the database."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM users")
    return [row[0] for row in c.fetchall()]
# Keyboard functions


def create_first_keyboard():
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    button1 = types.KeyboardButton('Последние изменения')
    button2 = types.KeyboardButton('Немного вдохновения')
    button3 = types.KeyboardButton('Основное расписание')
    button4 = types.KeyboardButton("Перемены")
    keyboard.add(button1, button2, button3, button4)
    return keyboard


def continue_keyboards():
    keyboard = types.InlineKeyboardMarkup()
    accept_button = InlineKeyboardButton("Okk", callback_data='Okk')
    keyboard.add(accept_button)
    return keyboard


def create_consent_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    accept_button = InlineKeyboardButton("Принять", callback_data='accept')
    reject_button = InlineKeyboardButton("Отклонить", callback_data='reject')
    keyboard.add(accept_button, reject_button)
    return keyboard


shift_messages = {
    'Основное расписание': {},
    'Перемены': {},
    'Последние изменения': {}
}


def delete_previous_shifts(bot: telebot.TeleBot, shift_type: str):
    """Deletes all previous shift messages of a specific type across all chats."""
    if shift_type in shift_messages:
        for chat_id, message_ids in shift_messages[shift_type].items():
            for message_id in message_ids:
                try:
                    bot.delete_message(chat_id, message_id)
                    logger.info(
                        f"Deleted {shift_type} message with ID: {message_id}")
                except Exception as e:
                    logger.error(f"Failed to delete message {message_id}: {e}")
        shift_messages[shift_type] = {}


def get_shift_pdf_url_for_date(date_to_find: date, base_url="https://www.ects.ru/page281.htm"):
    """Fetches the latest shift PDF URL from the website."""
    try:
        response = requests.get(base_url, timeout=5)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'lxml')

        document_div = soup.find('div', class_='document')
        if not document_div:
            return None

        pdf_links = [link['href'] for link in document_div.find_all(
            'a', href=True) if link['href'].endswith('.pdf')]
        if not pdf_links:
            return None

        month_mapping = {
            "january": "janvarja", "february": "fevralja", "march": "marta",
            "april": "aprelja", "may": "maja", "june": "ijunja",
            "july": "ijulja", "august": "avgusta", "September": "sentjabrja",
            "october": "oktjabrja", "november": "nojabrja", "december": "dekabrja"
        }

        def extract_date_from_filename(filename):
            """Extracts date from filenames like '15_janvarja_2024.pdf' or '15_janvarja_novoe.pdf'."""
            match = re.search(
                r"(\d{2})_([a-z]+)(?:_\d{4}|_[a-z]+)*\.pdf", filename, re.IGNORECASE)
            if not match:
                return None

            day, month_russian = int(match.group(1)), match.group(2).lower()
            month_english = next(
                (eng for eng, rus in month_mapping.items() if rus == month_russian), None)
            if not month_english:
                return None

            return date(date_to_find.year, list(calendar.month_name).index(month_english.capitalize()), day)

        latest_pdf_url = max(((pdf, extract_date_from_filename(pdf.split('/')[-1])) for pdf in pdf_links),
                             key=lambda x: x[1] if x[1] else date.min, default=(None, None))[0]
        return latest_pdf_url

    except requests.RequestException:
        logger.error("Ошибка при запросе к сайту.")
        return None


def pdf_to_image(pdf_content: BytesIO) -> BytesIO | None:
    """Converts PDF to image and returns BytesIO."""
    try:
        images = convert_from_bytes(pdf_content.getvalue(), dpi=150)

        if not images:
            logger.error("Ошибка: pdf2image не смог преобразовать PDF.")
            return None
        first_page = images[0]
        width, total_height = first_page.width, sum(
            img.height for img in images)

        if total_height == 0:
            logger.error("Ошибка: Высота изображения = 0.")
            return None

        combined_image = Image.new('RGB', (width, total_height))
        y_offset = 0

        for img in images:
            combined_image.paste(img, (0, y_offset))
            y_offset += img.height

        # Compress image
        img_byte_arr = BytesIO()
        combined_image.save(img_byte_arr, format='PNG',
                            optimize=True, quality=85)
        img_byte_arr.seek(0)

        return img_byte_arr

    except Exception as e:
        logger.error(f"Ошибка при конвертации PDF: {e}")
        return None

# Bot handlers


def split_image_into_chunks(image: Image.Image, max_chunks: int) -> list:
    """Splits an image into max_chunks equal parts."""
    width, height = image.size
    chunk_height = height // max_chunks
    return [image.crop((0, i * chunk_height, width, (i + 1) * chunk_height)) for i in range(max_chunks)]


def prepare_image_for_telegram(image: Image.Image) -> telebot.types.InputMediaPhoto:
    """Converts an image to a format suitable for Telegram."""
    chunk_io = BytesIO()
    image.save(chunk_io, format='PNG', optimize=True, quality=85)
    chunk_io.seek(0)
    return telebot.types.InputMediaPhoto(chunk_io)


def send_todays_shift(bot: telebot.TeleBot, chat_id: int):
    """Fetches today's shift, converts it to images, and sends them."""
    delete_previous_shifts(bot, 'Последние изменения')

    today = date.today()
    pdf_url = get_shift_pdf_url_for_date(today)

    if not pdf_url:
        bot.send_message(chat_id, "Сегодняшние изменения еще не доступны")
        return

    try:
        pdf_response = requests.get(pdf_url)
        pdf_response.raise_for_status()
        pdf_content = pdf_response.content

        combined_image_data = pdf_to_image(BytesIO(pdf_content))
        if not combined_image_data:
            bot.send_message(
                chat_id, "Не удалось преобразовать график в изображение.")
            return

        combined_image_data.seek(0)
        combined_image = Image.open(combined_image_data)

        reader = pypdf.PdfReader(BytesIO(pdf_content))
        num_pages = len(reader.pages)
        num_chunks = min(num_pages, 3)

        chunks = split_image_into_chunks(combined_image, num_chunks)

        with ThreadPool(len(chunks)) as pool:
            media_group = pool.map(prepare_image_for_telegram, chunks)

        sent_messages = bot.send_media_group(chat_id, media_group)
        if chat_id not in shift_messages['Последние изменения']:
            shift_messages['Последние изменения'][chat_id] = []
        shift_messages['Последние изменения'][chat_id].extend(
            [msg.message_id for msg in sent_messages])

        follow_up_message = bot.send_message(
            chat_id=chat_id,
            text="ПУ-ПУ-ПУ🙄",
            reply_markup=create_first_keyboard()
        )
        shift_messages['Последние изменения'][chat_id].append(
            follow_up_message.message_id)

    except requests.RequestException as req_err:
        bot.send_message(chat_id, "Ошибка при загрузке графика.")
        logger.error(f"Request Error: {req_err}")

    except pypdf.errors.PdfReadError as pdf_err:
        bot.send_message(chat_id, "Ошибка при обработке PDF.")
        logger.error(f"PDF Error: {pdf_err}")

    except Exception as e:
        bot.send_message(chat_id, "Произошла ошибка при обработке графика.")
        logger.error(f"Unexpected Error: {e}")


def handle_start(bot: telebot.TeleBot):
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        chat_id = message.chat.id
        first_name = message.chat.first_name
        last_name = message.chat.last_name
        username = message.chat.username
        created = get_or_create_user(chat_id, first_name, last_name, username)
        if created:
            logger.info(f"User {chat_id} added to the database.")
        else:
            logger.info(f"User {chat_id} already exists in the database.")

        bot.send_message(
            chat_id,
            "Для продолжения работы с твоим песональным ботом необходимо принять тот факт, что ты самая классная девочка!!!",
            reply_markup=create_consent_keyboard()
        )


def handle_callbacks(bot: telebot.TeleBot):
    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        chat_id = call.message.chat.id
        user_action = call.data
        from_user = call.from_user
        if user_action == "reject":
            bot.send_message(chat_id, f"Не-а!")
            handle_start(bot)
        elif user_action == 'accept':
            bot.send_message(
                chat_id, f"Спасибо!\nДавай, знакомиться, чуть ближе)")
            time.sleep(5)
            bot.send_message(chat_id, f"Меня зовут...Амм....")
            time.sleep(5)
            bot.send_message(chat_id, f"Меня пока никак не зовут. Если хочешь можешь дать мне имя в настройках )",
                             reply_markup=continue_keyboards())

        elif user_action == "Okk":
            bot.send_message(
                chat_id, f"Тебя зовут {from_user.first_name} -- мега разнообразный человек. Я и уму не приложу, как можно сочетать в себе столько талантов одновременно. Но ты как-то умудряешься!!")
            time.sleep(7)
            bot.send_message(
                chat_id, f"Увы, {from_user.first_name}, я не настолько талантлив😔")
            time.sleep(3)
            bot.send_message(
                chat_id, f"Но моя жизнь не настолько бессмысленна, как тебе может показаться -  меня создали ради одной единственной поистине благородной цели-....")
            time.sleep(5)
            bot.send_message(
                chat_id, f"помочь тебе сэкономить время на поиски расписания и актуальных изменений")
            time.sleep(5)
            bot.send_message(chat_id, f"Да, тебе сейчас забавно это слышать, но что если я скажу, пока ты откроешь браузер, напишешь в поисковике слово ектс, откроешь официальный сайт - найдешь нужную вкладку - посмотришь расписание и так 6 раз каждую неделю....")
            time.sleep(10)
            bot.send_message(
                chat_id, f"Это всего-то секунд 20, но умножь это на 2 учебных года ")
            time.sleep(4)
            bot.send_message(chat_id, f"это 128 часов!")
            time.sleep(2)
            bot.send_message(chat_id, f"128")
            time.sleep(2)
            bot.send_message(chat_id, f"часов.")
            time.sleep(5)
            bot.send_message(chat_id, f"Поэтому, мое существование и служение Тебе - истина первой инстанции! Я постараюсь сэкономить это время минимум в два раза.\nТолько включи уведомления\n Спамить не буду! Обещаю. Только самое важное\nЯ готов!🫡", reply_markup=create_first_keyboard())

# Main bot logic


load_dotenv()
current_dir = os.path.dirname(__file__)
RIJKSMUSEUM_API_KEY = "rgDy3FHZ"
RIJKSMUSEUM_API_URL = "https://www.rijksmuseum.nl/api/en/collection"


def get_random_artwork():
    """Fetches a random artwork from the Rijksmuseum API."""
    params = {
        "key": RIJKSMUSEUM_API_KEY,
        "type": "painting",
        "imgonly": True,
        "ps": 100,
        "s": "relevance",
        "toppieces": True
    }
    try:
        response = requests.get(RIJKSMUSEUM_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("artObjects"):
            artwork = random.choice(data["artObjects"])
            image_url = artwork["webImage"]["url"]
            title = artwork["title"]
            artist = artwork["principalOrFirstMaker"]
            return image_url, f"{title} by {artist}"
        else:
            logger.error("No artworks found in the API response.")
            return None, None
    except requests.RequestException as e:
        logger.error(f"Error fetching artwork from Rijksmuseum API: {e}")
        return None, None


def translate_to_russian(text: str) -> str:
    """Переводит текст с английского на русский с использованием Google Translate API."""
    translator = Translator()
    try:
        translated = translator.translate(text, src='en', dest='ru')
        return translated.text
    except Exception as e:
        logger.error(f"Ошибка при переводе текста: {e}")
        return text


def get_inspiring_quote():
    """Fetch an inspiring quote in Russian from the Forismatic API."""
    url = "http://api.forismatic.com/api/1.0/"
    params = {
        "method": "getQuote",
        "format": "json",
        "lang": "ru"
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        return f"{data['quoteText']}\n— {data['quoteAuthor']}"
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе цитаты: {e}")
        return "Сегодняшнее вдохновение недоступно. Попробуйте позже."


def handle_messages(bot: telebot.TeleBot):
    @bot.message_handler(func=lambda message: True)
    def handler_message(message):
        if message.text == 'Немного вдохновения' or message.text == '/inspirations':
            loading_message = bot.send_photo(
                message.chat.id,
                photo=open(f"{current_dir}/media/duck.png", 'rb'),
                caption="Ищу что-то вдохновляющее...")

            image_url, caption = get_random_artwork()

            caption_translated = translate_to_russian(str(caption))
            inspiring_quote = get_inspiring_quote()
            if image_url:
                try:
                    image_response = requests.get(image_url, timeout=10)
                    image_response.raise_for_status()
                    image_bytes = BytesIO(image_response.content)

                    bot.send_photo(
                        message.chat.id,
                        photo=image_bytes,
                        caption=f"🏷️ '{caption_translated}'\n\n------------\n{inspiring_quote}",
                        has_spoiler=True
                    )
                    bot.delete_message(
                        message.chat.id, loading_message.message_id)
                except requests.RequestException as e:
                    logger.error(f"Ошибка при загрузке изображения: {e}")
                    bot.send_message(
                        message.chat.id, f"Я не нашел изображений\nНО!\nМожет эти слова тебя вдохновят?\n{inspiring_quote}")
            else:
                bot.delete_message(
                    message.chat.id, loading_message.message_id)
                bot.send_message(
                    message.chat.id, f"Damn, я не нашел изображение, но зато узнал, вот что: \n{inspiring_quote}")

        elif message.text == 'Последние изменения' or message.text == '/changes':
            sent_message = bot.send_photo(
                message.chat.id,
                photo=open(f"{current_dir}/media/cat.jpg", 'rb'),
                caption="ОК, ищу изменения...", reply_markup=create_first_keyboard()
            )

            send_todays_shift(bot, message.chat.id)

            try:
                bot.delete_message(message.chat.id, sent_message.message_id)
                logger.info(
                    f"Deleted cat image message with ID: {sent_message.message_id}")
            except Exception as e:
                logger.error(f"Failed to delete cat image message: {e}")

        elif message.text == 'Основное расписание' or message.text == '/schedule':
            delete_previous_shifts(bot, 'Основное расписание')
            sent_message = bot.send_photo(
                message.chat.id,
                photo=open(f"{current_dir}/media/shift.png", 'rb'),
                caption=f"\n", reply_markup=create_first_keyboard()
            )
            if message.chat.id not in shift_messages['Основное расписание']:
                shift_messages['Основное расписание'][message.chat.id] = []
            shift_messages['Основное расписание'][message.chat.id].append(
                sent_message.message_id)

        elif message.text == 'Перемены' or message.text == '/breaks':
            delete_previous_shifts(bot, 'Перемены')

            sent_message = bot.send_photo(
                message.chat.id,
                photo=open(f"{current_dir}/media/shift2.png", 'rb'),
                caption=f"\n", reply_markup=create_first_keyboard()
            )
            if message.chat.id not in shift_messages['Перемены']:
                shift_messages['Перемены'][message.chat.id] = []
            shift_messages['Перемены'][message.chat.id].append(
                sent_message.message_id)


def get_weather(day: str) -> str:
    """Fetch weather data for Yekaterinburg from WeatherAPI."""
    base_url = "http://api.weatherapi.com/v1/forecast.json"
    params = {
        "key": "23e2e18225f64393b23132659240510",
        "q": "56.8389,60.6057",
        "days": 2,
        "lang": "ru"
    }

    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        data = response.json()

        if day == "today":
            weather = data["current"]
            description = weather["condition"]["text"]
            temp = weather["temp_c"]
            return f"🌤️ ------- Сегодня в Екатеринбурге: {description}, температура {temp}°C."

        elif day == "tomorrow":
            weather = data["forecast"]["forecastday"][1]["day"]
            description = weather["condition"]["text"]
            temp_min = weather["mintemp_c"]
            temp_max = weather["maxtemp_c"]
            return f"🌤️ Завтра в Екатеринбурге: {description}, температура от {temp_min}°C до {temp_max}°C."

    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе погоды: {e}")
        return "Не удалось получить данные о погоде."


def send_weather(bot, forecast_type):
    """Sends weather updates to all users."""
    logger.info(f"Executing send_weather for {forecast_type} forecast!")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM users")
    users = c.fetchall()

    if not users:
        logger.warning("No users found to send weather updates.")
        return

    for user in users:
        chat_id = user[0]
        weather_message = get_weather(forecast_type)
        if forecast_type == "today":
            try:
                bot.send_photo(
                    chat_id,
                    photo=open(f"{current_dir}/media/cat2.png", 'rb'),
                    caption=f"{weather_message}\n\nПожалуйста, Даш, оденься по погоде\n🫰🏻"
                )
            except Exception as e:
                logger.error(
                    f"Failed to send weather update to user {chat_id}: {e}")
        else:
            try:
                bot.send_photo(
                    chat_id,
                    photo=open(f"{current_dir}/media/cat3.png", 'rb'),
                    caption=f"{weather_message}\n\n🌗"
                )
            except Exception as e:
                logger.error(
                    f"Failed to send weather update to user {chat_id}: {e}")

    logger.info(f"Sent {forecast_type} weather update to {len(users)} users.")


YEKAT_TIMEZONE = pytz.timezone("Asia/Yekaterinburg")


def get_yekaterinburg_time():
    return datetime.now(YEKAT_TIMEZONE).strftime("%H:%M")


def schedule_weather_updates(bot):
    """Schedules weather updates at 8 AM and 8 PM in Yekaterinburg time."""
    logger.info(f"Current Yekaterinburg time: {get_yekaterinburg_time()}")

    schedule.every().day.at("08:00", "Asia/Yekaterinburg").do(send_weather,
                                                              bot=bot, forecast_type="today")
    schedule.every().day.at("00:23", "Asia/Yekaterinburg").do(send_weather,
                                                              bot=bot, forecast_type="tomorrow")

    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(10)

    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()


def main():
    load_dotenv()
    token_tg = "7962658875:AAEyvJCCPRbemdPNignuMn3S0lUHTctMLCU"
    print(token_tg)
    print(RIJKSMUSEUM_API_KEY)
    if not token_tg:
        logger.error("Error: TG_BOT_TOKEN not found in .env")
        sys.exit(1)

    init_db()
    logger.info("Database initialized.")

    bot = telebot.TeleBot(token_tg)
    logger.info("Bot instance created.")

    handle_start(bot)
    handle_callbacks(bot)
    handle_messages(bot)

    schedule_weather_updates(bot)
    logger.info("Handlers registered.")

    logger.info("Starting bot polling...")
    while True:
        try:
            bot.polling(none_stop=True, interval=1)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
