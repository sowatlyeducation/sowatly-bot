import asyncio
from datetime import datetime, date
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State

from dotenv import load_dotenv

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# -----------------------------
# Load .env
# -----------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003393989918"))
CHAT_ID = int(os.getenv("CHAT_ID", "-1003432639493"))
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "Sowatly_TG_Subs")
SHEET_NAME = os.getenv("SHEET_NAME", "Лист1")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "credentials.json")
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "@makkk0657")
CHECK_INTERVAL_MIN = int(os.getenv("CHECK_INTERVAL_MIN", "5"))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# -----------------------------
# Google Sheets
# -----------------------------
def init_gspread():
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
    client = gspread.authorize(creds)
    sh = client.open(SPREADSHEET_NAME)
    return sh.worksheet(SHEET_NAME)

worksheet = init_gspread()

def find_row_by_telegram_id(tg_id: int):
    col = worksheet.col_values(1)
    for idx, val in enumerate(col, start=1):
        if val.strip() == str(tg_id):
            return idx
    return None

def add_user_record(tg_id, username, full_name):
    worksheet.append_row([tg_id, username or "", full_name or "", "no", ""])

def read_user(row):
    vals = worksheet.row_values(row)
    vals += [""] * 5
    return {
        "telegram_id": vals[0],
        "username": vals[1],
        "full_name": vals[2],
        "paid": vals[3].lower(),
        "expiry_date": vals[4],
    }

def update_user_fields(row, fields: dict):
    current = worksheet.row_values(row)
    current += [""] * 5
    mapping = {"telegram_id":0, "username":1, "full_name":2, "paid":3, "expiry_date":4}
    for k,v in fields.items():
        if k in mapping:
            current[mapping[k]] = str(v)
    worksheet.update(f"A{row}:E{row}", [current])

def parse_date_or_none(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except Exception:
        return None

# -----------------------------
# FSM States
# -----------------------------
class StartStates(StatesGroup):
    waiting_for_full_name = State()

# -----------------------------
# /start handler
# -----------------------------
@dp.message(Command(commands=["start"]))
async def cmd_start(message: Message, state: FSMContext):
    tg_id = message.from_user.id
    row = find_row_by_telegram_id(tg_id)
    
    if row:
        user = read_user(row)
        if user["paid"] == "yes":
            expiry = parse_date_or_none(user.get("expiry_date"))
            if expiry and expiry >= date.today():
                await send_invite_links(tg_id)
                await message.answer(f"Вы уже оплатили. Ссылка на чат и канал отправлена. Подписка до {expiry.isoformat()}.")
            else:
                await message.answer("Срок подписки истёк. Свяжитесь с администратором для продления.")
            return
        else:
            await message.answer(f"Вы зарегистрированы, но оплата не подтверждена. Свяжитесь с администратором: {ADMIN_CONTACT}")
            return

    await message.answer("Здравствуйте! Пожалуйста, напишите своё имя и фамилию:")
    await state.set_state(StartStates.waiting_for_full_name)

# -----------------------------
# Обработка ввода имени и фамилии
# -----------------------------
@dp.message(StartStates.waiting_for_full_name)
async def process_full_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    tg_id = message.from_user.id
    username = message.from_user.username or ""
    
    add_user_record(tg_id, username, full_name)
    
    await message.answer(
        f"Спасибо, {full_name}!\n"
        f"Чтобы получить доступ, оплатите у администратора.\n"
        f"Контакты администратора: {ADMIN_CONTACT}\n"
        "После оплаты напишите /check."
    )
    await state.clear()

# -----------------------------
# /check handler
# -----------------------------
@dp.message(Command(commands=["check"]))
async def cmd_check(message: Message):
    tg_id = message.from_user.id
    row = find_row_by_telegram_id(tg_id)
    if not row:
        await message.answer("Вы не зарегистрированы. Напишите /start для начала.")
        return

    user = read_user(row)
    if user["paid"] != "yes":
        await message.answer(f"Оплата не подтверждена. Свяжитесь с администратором: {ADMIN_CONTACT}")
        return

    expiry = parse_date_or_none(user.get("expiry_date"))
    if not expiry:
        await message.answer("Дата окончания подписки не установлена. Свяжитесь с администратором.")
        return

    if expiry < date.today():
        # удаляем пользователя
        try:
            await bot.ban_chat_member(CHAT_ID, tg_id)
            await asyncio.sleep(1)
            await bot.unban_chat_member(CHAT_ID, tg_id)
            await bot.ban_chat_member(CHANNEL_ID, tg_id)
            await asyncio.sleep(1)
            await bot.unban_chat_member(CHANNEL_ID, tg_id)
        except Exception as e:
            logging.error(f"Ошибка при удалении пользователя {tg_id}: {e}")
        await message.answer("Срок подписки истёк. Свяжитесь с администратором для продления.")
        return

    await send_invite_links(tg_id)
    await message.answer(f"Ваша подписка активна до {expiry.isoformat()}. Ссылки на чат и канал отправлены вам лично.")

# -----------------------------
# Функция для отправки одноразовых ссылок
# -----------------------------
async def send_invite_links(tg_id: int):
    try:
        chat_link = await bot.create_chat_invite_link(CHAT_ID, member_limit=1)
        channel_link = await bot.create_chat_invite_link(CHANNEL_ID, member_limit=1)
        await bot.send_message(tg_id, f"Ссылка на чат: {chat_link.invite_link}")
        await bot.send_message(tg_id, f"Ссылка на канал: {channel_link.invite_link}")
    except Exception as e:
        logging.error(f"Ошибка при отправке ссылки {tg_id}: {e}")

# -----------------------------
# Фоновый воркер для удаления просроченных подписок
# -----------------------------
async def subscription_watcher():
    while True:
        all_values = worksheet.get_all_records()
        for idx, row in enumerate(all_values, start=2):
            tg_id_str = str(row.get("telegram_id", "")).strip()
            if not tg_id_str:
                continue
            try:
                tg_id = int(tg_id_str)
            except ValueError:
                continue

            paid = row.get("paid", "").lower()
            expiry_str = row.get("expiry_date", "")
            expiry = parse_date_or_none(expiry_str)

            if paid == "yes" and expiry and expiry < date.today():
                try:
                    await bot.ban_chat_member(CHAT_ID, tg_id)
                    await asyncio.sleep(1)
                    await bot.unban_chat_member(CHAT_ID, tg_id)
                    await bot.ban_chat_member(CHANNEL_ID, tg_id)
                    await asyncio.sleep(1)
                    await bot.unban_chat_member(CHANNEL_ID, tg_id)
                except Exception as e:
                    logging.error(f"Ошибка при удалении пользователя {tg_id}: {e}")
        await asyncio.sleep(CHECK_INTERVAL_MIN * 60)

# -----------------------------
# Запуск бота
# -----------------------------
async def main():
    asyncio.create_task(subscription_watcher())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
