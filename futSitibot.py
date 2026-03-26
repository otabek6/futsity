import asyncio
import os
import threading
import logging
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import aiosqlite

# ========== Настройки ==========
TOKEN = "8604968516:AAEwkA9V6CIObQSt_hORK9MpOcBG5h8F0gs"  # Замените на реальный токен
DB_PATH = "fudcity.db"

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ========== Flask для Render ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Бот работает", 200

def run_flask():
    port = int(os.environ.get('PORT', 8000))
    flask_app.run(host='0.0.0.0', port=port)

# ========== Состояния FSM ==========
class ApplicationForm(StatesGroup):
    car_number = State()
    driver_name = State()
    time = State()

class StatusCheck(StatesGroup):
    waiting_for_app_id = State()

# ========== Инициализация БД ==========
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                car_number TEXT,
                driver_name TEXT,
                time TEXT,
                status TEXT DEFAULT 'новая',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# ========== Команда /start ==========
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🚛 Привет! Я бот агрокластера «Фуд Сити».\n"
        "Я помогу оформить заявку на въезд, проверить статус и отвечу на частые вопросы.\n\n"
        "Команды:\n"
        "/new — новая заявка на въезд\n"
        "/status — проверить статус заявки\n"
        "/faq — часто задаваемые вопросы\n"
        "/cancel — отменить текущее действие"
    )

# ========== Команда /new (подача заявки) ==========
@dp.message(Command("new"))
async def new_application(message: types.Message, state: FSMContext):
    await state.set_state(ApplicationForm.car_number)
    await message.answer("Введите номер автомобиля:")

@dp.message(ApplicationForm.car_number)
async def get_car_number(message: types.Message, state: FSMContext):
    if not message.text.strip():
        await message.answer("Номер автомобиля не может быть пустым. Введите номер:")
        return
    await state.update_data(car_number=message.text.strip())
    await state.set_state(ApplicationForm.driver_name)
    await message.answer("Введите ФИО водителя:")

@dp.message(ApplicationForm.driver_name)
async def get_driver_name(message: types.Message, state: FSMContext):
    if not message.text.strip():
        await message.answer("ФИО не может быть пустым. Введите ФИО:")
        return
    await state.update_data(driver_name=message.text.strip())
    await state.set_state(ApplicationForm.time)
    await message.answer("Введите желаемое время въезда (например, 15:30):")

@dp.message(ApplicationForm.time)
async def get_time(message: types.Message, state: FSMContext):
    if not message.text.strip():
        await message.answer("Время не может быть пустым. Введите время:")
        return
    data = await state.update_data(time=message.text.strip())
    # Сохраняем в БД
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO applications (user_id, car_number, driver_name, time, status) VALUES (?, ?, ?, ?, ?)",
            (message.from_user.id, data['car_number'], data['driver_name'], data['time'], "новая")
        )
        await db.commit()
        app_id = cursor.lastrowid
    await message.answer(
        f"✅ Заявка №{app_id} принята! Статус можно проверить по команде /status.\n"
        f"В ближайшее время диспетчер рассмотрит вашу заявку."
    )
    await state.clear()

# ========== Команда /status (проверка статуса) ==========
@dp.message(Command("status"))
async def status_command(message: types.Message, state: FSMContext):
    await state.set_state(StatusCheck.waiting_for_app_id)
    await message.answer("Введите номер заявки (только цифры):")

@dp.message(StatusCheck.waiting_for_app_id)
async def process_app_id(message: types.Message, state: FSMContext):
    try:
        app_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Номер заявки должен состоять только из цифр. Попробуйте ещё раз:")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM applications WHERE id=?", (app_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                await message.answer(
                    f"📋 Заявка №{row[0]}\n"
                    f"Авто: {row[2]}\n"
                    f"Водитель: {row[3]}\n"
                    f"Время: {row[4]}\n"
                    f"Статус: {row[5]}"
                )
            else:
                await message.answer("❌ Заявка с таким номером не найдена.")
    await state.clear()

# ========== Команда /faq ==========
@dp.message(Command("faq"))
async def faq(message: types.Message):
    faq_text = """
❓ Часто задаваемые вопросы:

🕒 График работы: круглосуточно, ежедневно.
📍 Адрес: г. Москва, Калужское шоссе, 21-й км.
🚛 Для въезда нужна заявка (команда /new).
📞 Контактный телефон диспетчерской: +7 (495) 123-45-67.
"""
    await message.answer(faq_text)

# ========== Команда /cancel ==========
@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного действия для отмены.")
    else:
        await state.clear()
        await message.answer("✅ Действие отменено. Для новой заявки используйте /new.")

# ========== Обработка неизвестных команд ==========
@dp.message()
async def unknown(message: types.Message):
    await message.answer(
        "Я не понимаю эту команду. Доступные команды:\n"
        "/start — приветствие\n"
        "/new — новая заявка\n"
        "/status — проверка статуса\n"
        "/faq — вопросы\n"
        "/cancel — отмена"
    )

# ========== Уведомления (заглушка) ==========
# Здесь можно добавить логику, которая будет проверять изменение статуса заявок
# и отправлять уведомления пользователям. Для примера сделаем простую проверку
# раз в минуту (в реальном проекте лучше использовать webhook или брокер сообщений).
async def check_status_updates():
    """Фоновая задача: проверяет изменения статуса и отправляет уведомления."""
    last_checked = None  # можно хранить последний ID или время
    while True:
        await asyncio.sleep(60)  # проверяем каждую минуту
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Получаем заявки, у которых статус изменился на "одобрено" или "отклонено"
                # Для простоты: возьмём все заявки со статусом "одобрено", которые ещё не уведомляли.
                # В реальной системе нужно поле "notified".
                # Здесь для примера просто выведем в лог.
                async with db.execute("SELECT id, user_id, status FROM applications WHERE status IN ('одобрено', 'отклонено')") as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        app_id, user_id, status = row
                        # Отправляем уведомление пользователю
                        try:
                            await bot.send_message(user_id, f"🔄 Статус вашей заявки №{app_id} изменён на: {status}")
                            logger.info(f"Уведомление отправлено пользователю {user_id} по заявке {app_id}")
                        except Exception as e:
                            logger.error(f"Не удалось отправить уведомление: {e}")
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче уведомлений: {e}")

# ========== Запуск ==========
async def main():
    # Инициализация БД
    await init_db()
    logger.info("База данных инициализирована")

    # Запускаем Flask в отдельном потоке (для Render)
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask-сервер запущен в фоновом потоке")

    # Запускаем фоновую задачу для уведомлений
    asyncio.create_task(check_status_updates())

    # Запускаем поллинг бота
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
