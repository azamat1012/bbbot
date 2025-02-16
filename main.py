from urllib.parse import urljoin
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
import time
import os
import sys
import hashlib
CACHE_DIR = "pdf_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

last_activity_time = time.time()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

thread_local = threading.local()


def check_inactivity(bot):
    global last_activity_time
    inactivity_threshold = 3600

    while True:
        time_since_last_activity = time.time() - last_activity_time
        if time_since_last_activity > inactivity_threshold:
            logger.info("No activity detected. Restarting bot...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        time.sleep(60)


def get_db_connection():
    if not hasattr(thread_local, "conn"):
        thread_local.conn = sqlite3.connect(
            f'{current_dir}/bot.db', check_same_thread=False)
    return thread_local.conn


def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (chat_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, username TEXT)''')
    conn.commit()


def get_or_create_user(chat_id, first_name, last_name, username):
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


def create_first_keyboard():
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    button1 = types.KeyboardButton('Последние изменения')
    button2 = types.KeyboardButton('Немного вдохновения')
    button3 = types.KeyboardButton('Основное расписание')
    button4 = types.KeyboardButton("Перемены")
    button5 = types.KeyboardButton("Погода")
    keyboard.add(button1, button2, button3, button4, button5)
    return keyboard


def continue_keyboards():
    keyboard = types.InlineKeyboardMarkup()
    accept_button = InlineKeyboardButton("Okk", callback_data='Okk')
    keyboard.add(accept_button)
    return keyboard


def weather_keyboards():
    keyboard = types.InlineKeyboardMarkup()
    today_button = InlineKeyboardButton("Today!", callback_data='today')
    tomorrow_button = InlineKeyboardButton("Завтра", callback_data='tomorrow')
    keyboard.add(today_button, tomorrow_button)
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
    'Последние изменения': {},
    'Немного вдохновения': {},
    'Погода': {},
    'Погода Утро': {},
    'Погода Вечер': {},
}


def delete_previous_shifts(bot: telebot.TeleBot, shift_type: str):
    if shift_type in shift_messages:
        logger.info(f"Deleting previous {shift_type} messages...")
        for chat_id, message_ids in shift_messages[shift_type].items():
            logger.info(f"Chat ID: {chat_id}, Message IDs: {message_ids}")
            for message_id in message_ids:
                try:
                    logger.info(
                        f"Attempting to delete message ID: {message_id}")
                    bot.delete_message(chat_id, message_id)
                    logger.info(
                        f"Deleted {shift_type} message with ID: {message_id}")
                except Exception as e:
                    logger.error(f"Failed to delete message {message_id}: {e}")
        shift_messages[shift_type] = {}


def create_weather_image(weather_message: str) -> BytesIO:
    current_dir = "."
    image_path = f"{current_dir}/media/ping.jpg"
    image = Image.open(image_path)

    draw = ImageDraw.Draw(image)

    try:

        font_path = f"{current_dir}/fonts/DejaVuSans.ttf"
        font = ImageFont.truetype(font_path, 60)
    except IOError:
        logger.error("Failed to load font. Using default font.")
        font = ImageFont.load_default()

    text_position = (450, 70)
    text_color = (255, 255, 0)

    if "температура от" in weather_message:
        temp_range = weather_message.split("температура от ")[1].split("°C")[0]
        temp_range = temp_range.replace(" до ", "-") + "°C"
    elif "температура" in weather_message:
        temp_range = weather_message.split("температура ")[
            1].split("°C")[0] + "°C"
    else:
        temp_range = "N/A"

    draw.text(text_position,
              temp_range, font=font, fill=text_color)

    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    image_bytes.seek(0)

    return image_bytes


SCRAPINGBEE_API_KEY = 'UC2X12YO2MANX9GKURL0A6LAFVHBCWYT33BPNUXD7B6ON4IJHTRSZ47XM7KB1VI3K9X5RAK17VKG7IPO'


def get_shift_pdf_url_for_date(date_to_find, base_url="https://www.ects.ru/page281.htm"):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(base_url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'lxml')

        document_div = soup.find('div', class_='document')
        if not document_div:
            print("Document div not found on the page.")
            return None

        pdf_links = [urljoin(base_url, link['href']) for link in document_div.find_all(
            'a', href=True) if link['href'].endswith('.pdf')]
        if not pdf_links:
            print("No PDF links found in the document div.")
            return None

        month_mapping = {
            "january": "janvarja", "february": "fevralja", "march": "marta",
            "april": "aprelja", "may": "maja", "june": "ijunja",
            "july": "ijulja", "august": "avgusta", "september": "sentjabrja",
            "october": "oktjabrja", "november": "nojabrja", "december": "dekabrja"
        }

        def extract_date_from_filename(filename):
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

        latest_pdf_url = max(
            ((pdf, extract_date_from_filename(
                pdf.split('/')[-1])) for pdf in pdf_links),
            key=lambda x: x[1] if x[1] else date.min,
            default=(None, None)
        )[0]
        return latest_pdf_url

    except requests.RequestException as e:
        print(f"Error fetching website data: {e}")
        return None


def download_pdf(pdf_url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        }
        response = requests.get(pdf_url, headers=headers, timeout=10)
        response.raise_for_status()
        return BytesIO(response.content)
    except requests.RequestException as e:
        print(f"Error downloading PDF: {e}")
        return None


def pdf_to_image(pdf_content: BytesIO) -> list[BytesIO] | None:
    try:
        images = convert_from_bytes(pdf_content.getvalue(), dpi=150)

        if not images:
            print("Error: pdf2image could not convert PDF.")
            return None

        image_bytes_list = []
        for img in images:
            img_byte_arr = BytesIO()
            img.save(img_byte_arr, format='PNG', optimize=True, quality=85)
            img_byte_arr.seek(0)
            image_bytes_list.append(img_byte_arr)

        return image_bytes_list

    except Exception as e:
        print(f"Error converting PDF: {e}")
        return None


def get_cache_key(pdf_url):
    """Generate a unique cache key for the PDF URL."""
    return hashlib.md5(pdf_url.encode()).hexdigest()


def save_to_cache(pdf_url, images):
    """Save images to the cache."""
    cache_key = get_cache_key(pdf_url)
    cache_dir = os.path.join(CACHE_DIR, cache_key)
    os.makedirs(cache_dir, exist_ok=True)

    for i, image_bytes in enumerate(images):
        with open(os.path.join(cache_dir, f"page_{i + 1}.png"), "wb") as f:
            f.write(image_bytes.getvalue())


def load_from_cache(pdf_url):
    """Load images from the cache."""
    cache_key = get_cache_key(pdf_url)
    cache_dir = os.path.join(CACHE_DIR, cache_key)

    if not os.path.exists(cache_dir):
        return None

    images = []
    for file_name in sorted(os.listdir(cache_dir)):
        if file_name.endswith(".png"):
            with open(os.path.join(cache_dir, file_name), "rb") as f:
                images.append(BytesIO(f.read()))
    return images


def send_todays_shift(bot: telebot.TeleBot, chat_id: int, retry_count: int = 1):
    print("Fetching today's shift changes...")
    today = date.today()
    pdf_url = get_shift_pdf_url_for_date(today)

    if not pdf_url:
        if retry_count > 0:
            with open("git1.gif", 'rb') as gif_file:
                loading_message = bot.send_animation(
                    chat_id, gif_file, caption="пупупу..., ща")
            bot.delete_message(chat_id, loading_message.message_id)
            time.sleep(2)
            return send_todays_shift(bot, chat_id, retry_count - 1)
        else:
            with open("git2.gif", 'rb') as gif_file:
                bot.send_animation(chat_id, gif_file)
            return []

    try:
        pdf_content = download_pdf(pdf_url)
        if not pdf_content:
            bot.send_message(chat_id, "Не удалось загрузить PDF.")
            return []

        images = pdf_to_image(pdf_content)
        if not images:
            bot.send_message(
                chat_id, "Не удалось преобразовать график в изображение.")
            return []

        media_group = [telebot.types.InputMediaPhoto(
            image) for image in images]
        sent_messages = bot.send_media_group(chat_id, media_group)

        # Return message IDs for tracking
        return [msg.message_id for msg in sent_messages]

    except Exception as e:
        bot.send_message(chat_id, "Произошла ошибка при обработке графика.")
        print(f"Unexpected Error: {e}")
        return []


def prepare_image_for_telegram(image: Image.Image) -> telebot.types.InputMediaPhoto:
    chunk_io = BytesIO()
    image.save(chunk_io, format='PNG', optimize=True, quality=85)
    chunk_io.seek(0)
    return telebot.types.InputMediaPhoto(chunk_io)


load_dotenv()

current_dir = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(current_dir, "media")
RIJKSMUSEUM_API_KEY = "rgDy3FHZ"
RIJKSMUSEUM_API_URL = "https://www.rijksmuseum.nl/api/en/collection"


def get_random_artwork():
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
            return artwork["webImage"]["url"], f"{artwork['title']} by {artwork['principalOrFirstMaker']}"
        else:
            logger.error("No artworks found in the API response.")
            return None, None
    except requests.RequestException as e:
        logger.error(f"Error fetching artwork: {e}")
        return None, None


def translate_to_russian(text: str) -> str:
    translator = Translator()
    try:
        translated = translator.translate(text, src='en', dest='ru')
        return translated.text
    except Exception as e:
        logger.error(f"Ошибка при переводе текста: {e}")
        return text


def get_inspiring_quote():
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


def handle_start(bot: telebot.TeleBot):
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        global last_activity_time
        last_activity_time = time.time()
        chat_id = message.chat.id
        first_name = message.chat.first_name
        last_name = message.chat.last_name
        username = message.chat.username
        created = get_or_create_user(chat_id, first_name, last_name, username)
        print(f"START THE BOT-------> @{username}")

        if created:
            logger.info(
                f"START THE BOT-------> @{username}\nUser {chat_id} added to the database.")
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
        global last_activity_time
        last_activity_time = time.time()
        chat_id = call.message.chat.id
        user_action = call.data
        from_user = call.from_user

        if user_action == "reject":
            bot.send_message(chat_id, f"Не-а!")
            handle_start(bot)
        elif user_action == 'accept':
            with open(os.path.join(MEDIA_DIR, "git3.gif"), 'rb') as gif_file:
                loading_message = bot.send_animation(chat_id, gif_file)
            time.sleep(5)
            bot.send_message(chat_id, f"Меня зовут...Амм....")
            time.sleep(2)
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

        elif user_action == "today":
            delete_previous_shifts(bot, 'Погода')

            weather_message = get_weather("today")
            try:
                image_bytes = create_weather_image(weather_message)
            except Exception as e:
                logger.error(f"Failed to create weather image: {e}")
                bot.send_message(chat_id, weather_message)
                return

            try:
                sent_message = bot.send_photo(
                    chat_id,
                    photo=image_bytes,
                    caption=weather_message
                )

                if chat_id not in shift_messages['Погода']:
                    shift_messages['Погода'][chat_id] = []
                shift_messages['Погода'][chat_id].append(
                    sent_message.message_id)

            except Exception as e:
                logger.error(
                    f"Failed to send weather image to user {chat_id}: {e}")

        elif user_action == "tomorrow":
            delete_previous_shifts(bot, 'Погода')

            weather_message = get_weather("tomorrow")
            try:
                image_bytes = create_weather_image(weather_message)
            except Exception as e:
                logger.error(f"Failed to create weather image: {e}")
                bot.send_message(chat_id, weather_message)
                return

            try:
                sent_message = bot.send_photo(
                    chat_id,
                    photo=image_bytes,
                    caption=weather_message
                )

                if chat_id not in shift_messages['Погода']:
                    shift_messages['Погода'][chat_id] = []
                shift_messages['Погода'][chat_id].append(
                    sent_message.message_id)

            except Exception as e:
                logger.error(
                    f"Failed to send weather image to user {chat_id}: {e}")


def download_image(image_url):
    try:
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        return BytesIO(response.content)
    except requests.RequestException as e:
        logger.error(f"Ошибка загрузки изображения: {e}")
        return None


def handle_messages(bot: telebot.TeleBot):
    @bot.message_handler(func=lambda message: True)
    def handler_message(message):
        global last_activity_time
        last_activity_time = time.time()

        if message.text in ['Немного вдохновения', '/inspirations']:
            delete_previous_shifts(bot, 'Немного вдохновения')

            loading_message = bot.send_photo(
                message.chat.id,
                photo=open(os.path.join(MEDIA_DIR, "duck.png"), 'rb'),
                caption="Ищу что-то вдохновляющее..."
            )

            image_url, caption = get_random_artwork()
            caption_translated = translate_to_russian(caption)
            inspiring_quote = get_inspiring_quote()

            if image_url:
                try:
                    image_bytes = download_image(image_url)
                    sent_message = bot.send_photo(
                        message.chat.id,
                        photo=image_bytes,
                        caption=f"🏷️ '{caption_translated}'\n\n------------\n{inspiring_quote}",
                        has_spoiler=True
                    )

                    bot.delete_message(
                        message.chat.id, loading_message.message_id)
                    shift_messages['Немного вдохновения'].setdefault(
                        message.chat.id, []).append(sent_message.message_id)
                    logger.info(
                        f"Stored inspiration message ID: {sent_message.message_id}")

                except Exception as e:
                    logger.error(f"Ошибка при загрузке изображения: {e}")
                    bot.send_message(
                        message.chat.id,
                        f"Я не нашел изображений, но вот что я узнал:\n{inspiring_quote}"
                    )
            else:
                bot.delete_message(message.chat.id, loading_message.message_id)
                sent_message = bot.send_message(
                    message.chat.id,
                    f"Я не нашел изображение, но зато узнал, вот что:\n{inspiring_quote}"
                )
                shift_messages['Немного вдохновения'].setdefault(
                    message.chat.id, []).append(sent_message.message_id)
                logger.info(
                    f"Stored inspiration message ID: {sent_message.message_id}")

        elif message.text in ['Последние изменения', '/changes']:
            delete_previous_shifts(bot, 'Последние изменения')

            loading_message = bot.send_photo(
                message.chat.id,
                photo=open(f"{current_dir}/media/cat.jpg", 'rb'),
                caption="ОК, ищу изменения...", reply_markup=create_first_keyboard()
            )

            new_messages = send_todays_shift(bot, message.chat.id)

            try:
                bot.delete_message(message.chat.id, loading_message.message_id)
                logger.info(
                    f"Deleted cat image message with ID: {loading_message.message_id}")
            except Exception as e:
                logger.error(f"Failed to delete cat image message: {e}")

            # Store the new message IDs for tracking
            if new_messages:
                shift_messages['Последние изменения'][message.chat.id] = new_messages

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
            logger.info(f"Stored break message ID: {sent_message.message_id}")

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
            logger.info(
                f"Stored schedule message ID: {sent_message.message_id}")

        elif message.text == 'Погода' or message.text == '/weather':
            delete_previous_shifts(bot, 'Погода')
            sent_message = bot.send_photo(
                message.chat.id,
                photo=open(f"{current_dir}/media/weather.png", "rb"),
                caption="На сегодня? На завтра?",
                reply_markup=weather_keyboards()
            )
            if message.chat.id not in shift_messages['Погода']:
                shift_messages['Погода'][message.chat.id] = []
            shift_messages['Погода'][message.chat.id].append(
                sent_message.message_id)
            logger.info(
                f"Stored weather message ID: {sent_message.message_id}")


def get_weather(day: str) -> str:
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
            shift_type = "Погода Утро"
        elif forecast_type == "tomorrow":
            shift_type = "Погода Вечер"

        delete_previous_shifts(bot, shift_type)

        try:
            image_bytes = create_weather_image(weather_message)
        except Exception as e:
            logger.error(f"Failed to create weather image: {e}")
            sent_message = bot.send_message(chat_id, weather_message)
            if chat_id not in shift_messages[shift_type]:
                shift_messages[shift_type][chat_id] = []
            shift_messages[shift_type][chat_id].append(sent_message.message_id)
            continue

        try:
            if forecast_type == "today":
                sent_message = bot.send_photo(
                    chat_id,
                    photo=open(f"{current_dir}/media/cat2.png", 'rb'),
                    caption=weather_message
                )
            elif forecast_type == "tomorrow":
                sent_message = bot.send_photo(
                    chat_id,
                    photo=open(f"{current_dir}/media/cat3.png", 'rb'),
                    caption=weather_message
                )

            if chat_id not in shift_messages[shift_type]:
                shift_messages[shift_type][chat_id] = []
            shift_messages[shift_type][chat_id].append(sent_message.message_id)

        except Exception as e:
            logger.error(
                f"Failed to send weather update to user {chat_id}: {e}")


YEKAT_TIMEZONE = pytz.timezone("Asia/Yekaterinburg")


def get_yekaterinburg_time():
    return datetime.now(YEKAT_TIMEZONE).strftime("%H:%M")


def schedule_weather_updates(bot):
    global last_activity_time
    last_activity_time = time.time()
    logger.info(f"Current Yekaterinburg time: {get_yekaterinburg_time()}")

    schedule.every().day.at("08:00", "Asia/Yekaterinburg").do(
        send_weather, bot=bot, forecast_type="today"
    )
    schedule.every().day.at("20:55", "Asia/Yekaterinburg").do(
        send_weather, bot=bot, forecast_type="tomorrow"
    )

    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(10)

    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()


def keep_alive():
    while True:
        logger.info("Keep-alive: Bot is running...")
        time.sleep(3600)


def main():
    load_dotenv()
    token_tg = "7617045383:AAHP-t_NNyrt-qion9TFL71HegCJXwR_EZM"
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
    logger.info("Weather update scheduler started.")

    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    logger.info("Keep-alive thread started.")

    inactivity_checker_thread = threading.Thread(
        target=check_inactivity, args=(bot,), daemon=True)
    inactivity_checker_thread.start()
    logger.info("Inactivity checker thread started.")

    logger.info("Starting bot polling...")
    while True:
        try:
            bot.infinity_polling(none_stop=True, interval=1)
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            logger.info("Restarting bot in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    main()
