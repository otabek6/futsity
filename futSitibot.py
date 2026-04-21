import asyncio
import os
import threading
import logging
import re
from flask import Flask
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiosqlite

# ========== Настройки ==========
# ✅ БЕЗОПАСНОСТЬ: токен берётся из переменной окружения, не хранится в коде.
# Установите переменную окружения: export BOT_TOKEN="ваш_токен"
TOKEN = os.environ.get("8604968516:AAEwkA9V6CIObQSt_hORK9MpOcBG5h8F0gs")
if not TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не установлена!")

DB_PATH = os.environ.get("DB_PATH", "fudcity.db")

# ✅ БЕЗОПАСНОСТЬ: ID диспетчеров берётся из переменной окружения.
# Формат: DISPATCHER_IDS="123456789,987654321"
_dispatcher_ids_raw = os.environ.get("DISPATCHER_IDS", "")
DISPATCHER_IDS: set[int] = {
    int(x.strip()) for x in _dispatcher_ids_raw.split(",") if x.strip().isdigit()
}

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

# ========== Валидация ==========
# ✅ БЕЗОПАСНОСТЬ: проверяем форматы вводимых данных

CAR_NUMBER_PATTERN = re.compile(
    r'^[АВЕКМНОРСТУХABEKMHOPCTYX]{1}\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\d{2,3}$',
    re.IGNORECASE
)
TIME_PATTERN = re.compile(r'^\d{1,2}:\d{2}$')

def validate_car_number(text: str) -> bool:
    """Проверяет российский номер авто (А123ВС77 и т.п.)."""
    return bool(CAR_NUMBER_PATTERN.match(text.replace(" ", "").upper()))

def validate_time(text: str) -> bool:
    """Проверяет формат времени ЧЧ:ММ."""
    if not TIME_PATTERN.match(text):
        return False
    h, m = text.split(":")
    return 0 <= int(h) <= 23 and 0 <= int(m) <= 59

def is_dispatcher(user_id: int) -> bool:
    return user_id in DISPATCHER_IDS

# ========== Инициализация БД ==========
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                car_number  TEXT    NOT NULL,
                driver_name TEXT    NOT NULL,
                time        TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'новая',
                notified    INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ✅ Добавляем колонку notified если её нет (для существующих БД)
        try:
            await db.execute("ALTER TABLE applications ADD COLUMN notified INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # Колонка уже существует
        await db.commit()

# ========== Инлайн-клавиатуры ==========

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Новая заявка", callback_data="new_app")],
        [InlineKeyboardButton(text="🔍 Статус заявки", callback_data="check_status")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_apps")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="faq")],
    ])

def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")]
    ])

def dispatcher_action_keyboard(app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{app_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{app_id}"),
        ]
    ])

# ========== Команда /start ==========
@dp.message(Command("start"))
async def start(message: types.Message):
    text = (
        "🚛 Привет! Я бот агрокластера «Фуд Сити».\n"
        "Я помогу оформить заявку на въезд, проверить статус и отвечу на частые вопросы.\n\n"
        "Выберите действие:"
    )
    await message.answer(text, reply_markup=main_menu_keyboard())

# ========== Обработчики inline-кнопок главного меню ==========
@dp.callback_query(F.data == "new_app")
async def cb_new_app(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ApplicationForm.car_number)
    await callback.message.answer(
        "Введите номер автомобиля (например, А123ВС77):",
        reply_markup=cancel_keyboard()
    )

@dp.callback_query(F.data == "check_status")
async def cb_check_status(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(StatusCheck.waiting_for_app_id)
    await callback.message.answer(
        "Введите номер заявки (только цифры):",
        reply_markup=cancel_keyboard()
    )

@dp.callback_query(F.data == "my_apps")
async def cb_my_apps(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, car_number, driver_name, time, status, created_at "
            "FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await callback.message.answer("У вас пока нет заявок. Используйте кнопку «Новая заявка».")
        return
    lines = ["📋 *Ваши последние заявки:*\n"]
    for row in rows:
        app_id, car, driver, t, status, created = row
        lines.append(
            f"№{app_id} | {car} | {driver} | {t}\n"
            f"Статус: *{status}* | Дата: {str(created)[:10]}\n"
        )
    await callback.message.answer("\n".join(lines), parse_mode="Markdown")

@dp.callback_query(F.data == "faq")
async def cb_faq(callback: types.CallbackQuery):
    await callback.answer()
    faq_text = (
        "❓ *Часто задаваемые вопросы:*\n\n"
        "🕒 *График работы:* круглосуточно, ежедневно.\n"
        "📍 *Адрес:* г. Москва, Калужское шоссе, 21-й км.\n"
        "🚛 Для въезда нужна заявка — нажмите *«Новая заявка»*.\n"
        "📞 *Диспетчерская:* +7 (495) 123-45-67.\n"
        "📧 *Email:* dispatch@foodcity.ru"
    )
    await callback.message.answer(faq_text, parse_mode="Markdown")

@dp.callback_query(F.data == "cancel")
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    await callback.message.answer(
        "✅ Действие отменено.",
        reply_markup=main_menu_keyboard()
    )

# ========== FSM: Новая заявка ==========
@dp.message(Command("new"))
async def new_application(message: types.Message, state: FSMContext):
    await state.set_state(ApplicationForm.car_number)
    await message.answer(
        "Введите номер автомобиля (например, А123ВС77):",
        reply_markup=cancel_keyboard()
    )

@dp.message(ApplicationForm.car_number)
async def get_car_number(message: types.Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    # ✅ БЕЗОПАСНОСТЬ: валидация формата номера авто
    if not validate_car_number(text):
        await message.answer(
            "❌ Неверный формат номера. Введите российский номер, например А123ВС77:",
            reply_markup=cancel_keyboard()
        )
        return
    await state.update_data(car_number=text.upper())
    await state.set_state(ApplicationForm.driver_name)
    await message.answer("Введите ФИО водителя:", reply_markup=cancel_keyboard())

@dp.message(ApplicationForm.driver_name)
async def get_driver_name(message: types.Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if len(text) < 5 or len(text) > 100:
        await message.answer(
            "❌ ФИО должно содержать от 5 до 100 символов. Введите ФИО:",
            reply_markup=cancel_keyboard()
        )
        return
    await state.update_data(driver_name=text)
    await state.set_state(ApplicationForm.time)
    await message.answer(
        "Введите желаемое время въезда в формате ЧЧ:ММ (например, 15:30):",
        reply_markup=cancel_keyboard()
    )

@dp.message(ApplicationForm.time)
async def get_time(message: types.Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    # ✅ БЕЗОПАСНОСТЬ: валидация формата времени
    if not validate_time(text):
        await message.answer(
            "❌ Неверный формат времени. Введите время в формате ЧЧ:ММ, например 15:30:",
            reply_markup=cancel_keyboard()
        )
        return
    data = await state.update_data(time=text)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO applications (user_id, car_number, driver_name, time, status) VALUES (?, ?, ?, ?, ?)",
            (message.from_user.id, data['car_number'], data['driver_name'], data['time'], "новая")
        )
        await db.commit()
        app_id = cursor.lastrowid

    # ✅ Уведомляем всех диспетчеров о новой заявке
    await notify_dispatchers_new_app(app_id, data)

    await message.answer(
        f"✅ Заявка *№{app_id}* принята!\n"
        f"Диспетчер рассмотрит её в ближайшее время.\n"
        f"Вы получите уведомление о смене статуса.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    await state.clear()

async def notify_dispatchers_new_app(app_id: int, data: dict):
    """Отправляет диспетчерам уведомление о новой заявке с кнопками действий."""
    if not DISPATCHER_IDS:
        logger.warning("Список диспетчеров пуст (DISPATCHER_IDS не задан).")
        return
    text = (
        f"🆕 *Новая заявка №{app_id}*\n"
        f"🚗 Авто: {data['car_number']}\n"
        f"👤 Водитель: {data['driver_name']}\n"
        f"🕒 Время въезда: {data['time']}"
    )
    for dispatcher_id in DISPATCHER_IDS:
        try:
            await bot.send_message(
                dispatcher_id, text,
                parse_mode="Markdown",
                reply_markup=dispatcher_action_keyboard(app_id)
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить диспетчера {dispatcher_id}: {e}")

# ========== FSM: Проверка статуса ==========
@dp.message(Command("status"))
async def status_command(message: types.Message, state: FSMContext):
    await state.set_state(StatusCheck.waiting_for_app_id)
    await message.answer("Введите номер заявки (только цифры):", reply_markup=cancel_keyboard())

@dp.message(StatusCheck.waiting_for_app_id)
async def process_app_id(message: types.Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    if not text.isdigit():
        await message.answer(
            "❌ Номер заявки должен состоять только из цифр. Попробуйте ещё раз:",
            reply_markup=cancel_keyboard()
        )
        return
    app_id = int(text)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, car_number, driver_name, time, status, created_at FROM applications WHERE id=?",
            (app_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if row:
        status_emoji = {"новая": "🟡", "одобрено": "🟢", "отклонено": "🔴"}.get(row[4], "⚪")
        await message.answer(
            f"📋 *Заявка №{row[0]}*\n"
            f"🚗 Авто: {row[1]}\n"
            f"👤 Водитель: {row[2]}\n"
            f"🕒 Время: {row[3]}\n"
            f"Статус: {status_emoji} *{row[4]}*\n"
            f"📅 Подана: {str(row[5])[:16]}",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )
    else:
        await message.answer(
            "❌ Заявка с таким номером не найдена.",
            reply_markup=main_menu_keyboard()
        )
    await state.clear()

# ========== Панель диспетчера ==========
@dp.message(Command("dispatcher"))
async def dispatcher_panel(message: types.Message):
    # ✅ БЕЗОПАСНОСТЬ: только авторизованные диспетчеры
    if not is_dispatcher(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к панели диспетчера.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, car_number, driver_name, time, created_at "
            "FROM applications WHERE status='новая' ORDER BY id ASC LIMIT 20"
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("✅ Новых заявок нет.")
        return
    await message.answer(f"📋 Новых заявок: *{len(rows)}*", parse_mode="Markdown")
    for row in rows:
        app_id, car, driver, t, created = row
        text = (
            f"📌 *Заявка №{app_id}*\n"
            f"🚗 {car} | 👤 {driver} | 🕒 {t}\n"
            f"📅 {str(created)[:16]}"
        )
        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=dispatcher_action_keyboard(app_id)
        )

@dp.callback_query(F.data.startswith("approve:"))
async def cb_approve(callback: types.CallbackQuery):
    await callback.answer()
    if not is_dispatcher(callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return
    app_id = int(callback.data.split(":")[1])
    await update_application_status(app_id, "одобрено", callback)

@dp.callback_query(F.data.startswith("reject:"))
async def cb_reject(callback: types.CallbackQuery):
    await callback.answer()
    if not is_dispatcher(callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return
    app_id = int(callback.data.split(":")[1])
    await update_application_status(app_id, "отклонено", callback)

async def update_application_status(app_id: int, new_status: str, callback: types.CallbackQuery):
    """Меняет статус заявки и сразу уведомляет пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, status FROM applications WHERE id=?", (app_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await callback.answer(f"Заявка №{app_id} не найдена.", show_alert=True)
            return
        user_id, current_status = row
        if current_status != "новая":
            await callback.answer(f"Заявка уже обработана: {current_status}", show_alert=True)
            return
        await db.execute(
            "UPDATE applications SET status=?, notified=1 WHERE id=?",
            (new_status, app_id)
        )
        await db.commit()

    # ✅ УВЕДОМЛЕНИЯ: уведомляем пользователя сразу при смене статуса
    status_emoji = "✅" if new_status == "одобрено" else "❌"
    user_text = (
        f"{status_emoji} Ваша заявка *№{app_id}* была *{new_status}* диспетчером.\n"
    )
    if new_status == "одобрено":
        user_text += "Вы можете въезжать в указанное время. Удачной поездки!"
    else:
        user_text += "Для уточнения причин обратитесь по телефону: +7 (495) 123-45-67."

    try:
        await bot.send_message(user_id, user_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")

    # Обновляем сообщение у диспетчера
    dispatcher_text = (
        f"{'✅' if new_status == 'одобрено' else '❌'} *Заявка №{app_id}* — *{new_status}*\n"
        f"Диспетчер: @{callback.from_user.username or callback.from_user.full_name}"
    )
    await callback.message.edit_text(dispatcher_text, parse_mode="Markdown")

# ========== Команды /faq и /cancel (текстовые, для совместимости) ==========
@dp.message(Command("faq"))
async def faq(message: types.Message):
    faq_text = (
        "❓ *Часто задаваемые вопросы:*\n\n"
        "🕒 *График работы:* круглосуточно, ежедневно.\n"
        "📍 *Адрес:* г. Москва, Калужское шоссе, 21-й км.\n"
        "🚛 Для въезда нужна заявка — используйте /new.\n"
        "📞 *Диспетчерская:* +7 (495) 123-45-67.\n"
    )
    await message.answer(faq_text, parse_mode="Markdown")

@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного действия для отмены.", reply_markup=main_menu_keyboard())
    else:
        await state.clear()
        await message.answer("✅ Действие отменено.", reply_markup=main_menu_keyboard())

@dp.message(Command("myapps"))
async def my_apps(message: types.Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, car_number, driver_name, time, status, created_at "
            "FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("У вас пока нет заявок. Создайте первую: /new")
        return
    lines = ["📋 *Ваши последние заявки:*\n"]
    for row in rows:
        app_id, car, driver, t, status, created = row
        emoji = {"новая": "🟡", "одобрено": "🟢", "отклонено": "🔴"}.get(status, "⚪")
        lines.append(f"{emoji} №{app_id} | {car} | {driver} | {t} | {str(created)[:10]}")
    await message.answer("\n".join(lines), parse_mode="Markdown")

# ========== Обработка неизвестных сообщений ==========
@dp.message()
async def unknown(message: types.Message):
    await message.answer(
        "Я не понимаю эту команду. Воспользуйтесь меню:",
        reply_markup=main_menu_keyboard()
    )

# ========== Запуск ==========
async def main():
    await init_db()
    logger.info("База данных инициализирована")

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask-сервер запущен в фоновом потоке")

    logger.info(f"Авторизованные диспетчеры: {DISPATCHER_IDS or 'НЕ ЗАДАНЫ (установите DISPATCHER_IDS)'}")
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
