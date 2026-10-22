import asyncio, json, os, logging, time
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("telegram_bot")

BOT_TOKEN = '8897765303:AAHZ-VfRAiSHshbmneLuCbbdl5exCPH1Xo4'
BASE_DIR = '/root/brutv2'
LAST_CHAT_FILE = os.path.join(BASE_DIR, 'last_chat_id.txt')
MONITOR_TASK_FILE = os.path.join(BASE_DIR, 'monitor_auth_task.json')
BRUT_TASK_FILE = os.path.join(BASE_DIR, 'brut_task.json')
STOP_FLAG_FILE = os.path.join(BASE_DIR, 'stop_brut.flag')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def save_chat_id(chat_id):
    with open(LAST_CHAT_FILE, 'w') as f:
        f.write(str(chat_id))

@dp.message(Command('start'))
async def cmd_start(message: types.Message):
    save_chat_id(message.chat.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Авторизовать монитор', callback_data='auth_monitor')],
        [InlineKeyboardButton(text='Брут', callback_data='brut')]
    ])
    await message.answer("Выберите действие:", reply_markup=kb)

@dp.callback_query()
async def callback_handler(callback: types.CallbackQuery):
    save_chat_id(callback.message.chat.id)
    if callback.data == 'auth_monitor':
        await callback.message.answer("Отправьте строку: номер:пароль")
    elif callback.data == 'brut':
        await callback.message.answer("Пришлите файл .txt с картами")
    await callback.answer()

@dp.message(Command('c'))
async def cmd_stop(message: types.Message):
    save_chat_id(message.chat.id)
    with open(STOP_FLAG_FILE, 'w') as f:
        f.write('stop')
    await message.answer("⏹ Команда остановки отправлена")

@dp.message()
async def handle_messages(message: types.Message):
    save_chat_id(message.chat.id)
    # Если текст с номером:пароль
    if message.text and ':' in message.text and not message.document:
        phone, password = message.text.split(':', 1)
        task = {'phone': phone.strip(), 'password': password.strip(), 'chat_id': message.chat.id}
        with open(MONITOR_TASK_FILE, 'w') as f:
            json.dump(task, f)
        await message.answer("✅ Задание на авторизацию монитора отправлено")
    # Если файл
    elif message.document:
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        file_path = os.path.join(UPLOADS_DIR, f'{message.chat.id}_{int(time.time())}.txt')
        await bot.download_file(file.file_path, file_path)
        task = {'chat_id': message.chat.id, 'cards_file': file_path}
        with open(BRUT_TASK_FILE, 'w') as f:
            json.dump(task, f)
        await message.answer("✅ Задание на брут отправлено")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())