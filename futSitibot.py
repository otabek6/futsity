import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

TOKEN = "8604968516:AAEwkA9V6CIObQSt_hORK9MpOcBG5h8F0gs"  # замени на реальный

bot = Bot(token=TOKEN)
dp = Dispatcher()

# База данных
conn = sqlite3.connect("fudcity.db")
cursor = conn.cursor()
cursor.execute("""
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
conn.commit()

class ApplicationForm(StatesGroup):
    car_number = State()
    driver_name = State()
    time = State()

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "🚛 Привет! Я бот агрокластера «Фуд Сити».\n"
        "Я помогу оформить заявку на въезд, проверить статус и отвечу на частые вопросы.\n\n"
        "Команды:\n"
        "/new — новая заявка на въезд\n"
        "/status — проверить статус заявки\n"
        "/faq — часто задаваемые вопросы"
    )

@dp.message(Command("new"))
async def new_application(message: types.Message, state: FSMContext):
    await state.set_state(ApplicationForm.car_number)
    await message.answer("Введите номер автомобиля:")

@dp.message(ApplicationForm.car_number)
async def get_car_number(message: types.Message, state: FSMContext):
    await state.update_data(car_number=message.text)
    await state.set_state(ApplicationForm.driver_name)
    await message.answer("Введите ФИО водителя:")

@dp.message(ApplicationForm.driver_name)
async def get_driver_name(message: types.Message, state: FSMContext):
    await state.update_data(driver_name=message.text)
    await state.set_state(ApplicationForm.time)
    await message.answer("Введите желаемое время въезда (например, 15:30):")

@dp.message(ApplicationForm.time)
async def get_time(message: types.Message, state: FSMContext):
    data = await state.update_data(time=message.text)
    cursor.execute(
        "INSERT INTO applications (user_id, car_number, driver_name, time, status) VALUES (?, ?, ?, ?, ?)",
        (message.from_user.id, data['car_number'], data['driver_name'], data['time'], "новая")
    )
    conn.commit()
    app_id = cursor.lastrowid
    await message.answer(f"✅ Заявка №{app_id} принята! Статус можно проверить по команде /status")
    await state.clear()

@dp.message(Command("status"))
async def check_status(message: types.Message):
    await message.answer("Введите номер заявки:")

    @dp.message(F.text)
    async def get_app_id(msg: types.Message):
        try:
            app_id = int(msg.text)
            cursor.execute("SELECT * FROM applications WHERE id=?", (app_id,))
            row = cursor.fetchone()
            if row:
                await msg.answer(f"📋 Заявка №{row[0]}\nАвто: {row[2]}\nВодитель: {row[3]}\nВремя: {row[4]}\nСтатус: {row[5]}")
            else:
                await msg.answer("❌ Заявка не найдена.")
        except ValueError:
            await msg.answer("Введите числовой номер заявки.")

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

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())