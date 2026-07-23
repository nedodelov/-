import asyncio
import logging
import time
import traceback
import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup


BOT_TOKEN = "8683358675:AAFKDvTMEVQjiIczE_Q7AJ0f07NxAfd5pWQ"
MANAGER_IDS = [1768487973, 1607756200]
PRICE_LINK = "https://telegra.ph/anonchapa-07-08"
TIP_AMOUNTS = [10, 20, 50, 100, 200, 500, 1000, 5000, 10000]
DB_PATH = "orders.db"
ORDER_COOLDOWN = 3600

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

break_mode = False
banned_users = set()
last_order_time = {}


class Database:
    @staticmethod
    async def init():
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    problem TEXT,
                    help_needed TEXT,
                    amount INTEGER,
                    timestamp INTEGER,
                    status TEXT DEFAULT 'pending'
                )
            ''')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_status ON orders(status)')
            await db.commit()
            logger.info("База данных инициализирована")

    @staticmethod
    async def add_order(user_id, username, full_name, problem, help_needed, amount):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                'INSERT INTO orders (user_id, username, full_name, problem, help_needed, amount, timestamp, status) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (user_id, username, full_name, problem, help_needed, amount, int(time.time()), 'pending')
            )
            await db.commit()
            return cur.lastrowid

    @staticmethod
    async def get_next_pending():
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                'SELECT id, user_id, username, full_name, problem, help_needed, amount '
                'FROM orders WHERE status = "pending" ORDER BY id LIMIT 1'
            )
            return await cur.fetchone()

    @staticmethod
    async def set_status(order_id, status):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                'UPDATE orders SET status = ? WHERE id = ? AND status = "pending"',
                (status, order_id)
            )
            await db.commit()
            return cur.rowcount > 0

    @staticmethod
    async def get_order(order_id):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                'SELECT id, user_id, username, full_name, problem, help_needed, amount, status '
                'FROM orders WHERE id = ?',
                (order_id,)
            )
            return await cur.fetchone()

    @staticmethod
    async def pending_count():
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute('SELECT COUNT(*) FROM orders WHERE status = "pending"')
            row = await cur.fetchone()
            return row[0] if row else 0

    @staticmethod
    async def all_pending_ids():
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute('SELECT id FROM orders WHERE status = "pending" ORDER BY id')
            rows = await cur.fetchall()
            return [str(r[0]) for r in rows]


def main_kb(user_id):
    kb = [
        [KeyboardButton(text="📝 Оставить заявку")],
        [KeyboardButton(text="📋 Прайс-лист")],
        [KeyboardButton(text="💸 Чаевые")]
    ]
    if user_id in MANAGER_IDS:
        kb.append([KeyboardButton(text="🔧 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⛔ Перерыв")],
        [KeyboardButton(text="✅ Убрать перерыв")],
        [KeyboardButton(text="📩 Следующая заявка")],
        [KeyboardButton(text="🚫 Забанить пользователя")],
        [KeyboardButton(text="✅ Разбанить пользователя")],
        [KeyboardButton(text="📋 Список забаненных")],
        [KeyboardButton(text="💰 Выплатить nedodelov за оплату")],
        [KeyboardButton(text="◀️ Назад в меню")]
    ],
    resize_keyboard=True
)


class OrderForm(StatesGroup):
    problem = State()
    help = State()
    amount = State()

class ReplyForm(StatesGroup):
    wait_reply = State()

class BanForm(StatesGroup):
    ban_id = State()
    unban_id = State()


def format_order(order):
    order_id, user_id, username, full_name, problem, help_needed, amount = order
    uname = f"@{username}" if username else "Не указан"
    return (
        f"🌟 НОВАЯ ЗАЯВКА #{order_id} 🌟\n\n"
        f"👤 Имя: {full_name}\n"
        f"🆔 Telegram ID: {user_id}\n"
        f"📛 Username: {uname}\n"
        f"❓ Проблема: {problem}\n"
        f"🛠 Чем помочь: {help_needed}\n"
        f"💰 Готов заплатить: {amount}₽"
    )


async def show_next(message: types.Message):
    try:
        if message.from_user.id not in MANAGER_IDS:
            return

        if break_mode:
            await message.answer("⛔ Перерыв. Отключите перерыв.")
            return

        count = await Database.pending_count()
        if count == 0:
            await message.answer("📭 Нет новых заявок.")
            return

        order = await Database.get_next_pending()
        if not order:
            await message.answer("❌ Ошибка получения заявки.")
            return

        order_id = order[0]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_{order_id}")],
            [InlineKeyboardButton(text="❌ Отказать", callback_data=f"reject_{order_id}")],
            [InlineKeyboardButton(text="⏩ Следующая", callback_data=f"skip_{order_id}")]
        ])
        await message.answer(
            format_order(order),
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"show_next ошибка: {e}\n{traceback.format_exc()}")
        await message.answer("❌ Ошибка при показе заявки. Проверьте логи.")

# ========== ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def start_cmd(msg: types.Message):
    await msg.answer("👋 Привет! Используйте кнопки снизу.", reply_markup=main_kb(msg.from_user.id))

@dp.message(F.text == "📝 Оставить заявку")
async def order_start(msg: types.Message, state: FSMContext):
    try:
        uid = msg.from_user.id
        if uid in banned_users:
            await msg.answer("🚫 Вы забанены.", reply_markup=main_kb(uid))
            return
        if uid not in MANAGER_IDS:
            now = time.time()
            if uid in last_order_time and (now - last_order_time[uid]) < ORDER_COOLDOWN:
                rem = int(ORDER_COOLDOWN - (now - last_order_time[uid]))
                h = rem // 3600
                m = (rem % 3600) // 60
                await msg.answer(f"⏳ Ждите {h} ч {m} мин.", reply_markup=main_kb(uid))
                return
        await state.set_state(OrderForm.problem)
        await msg.answer("📝 Опишите вашу проблему:", reply_markup=main_kb(uid))
    except Exception as e:
        logger.error(f"order_start ошибка: {e}\n{traceback.format_exc()}")
        await msg.answer("❌ Ошибка, попробуйте позже.")

@dp.message(OrderForm.problem, F.text)
async def process_problem(msg: types.Message, state: FSMContext):
    await state.update_data(problem=msg.text)
    await state.set_state(OrderForm.help)
    await msg.answer("✏️ Чем вам помочь?", reply_markup=main_kb(msg.from_user.id))

@dp.message(OrderForm.help, F.text)
async def process_help(msg: types.Message, state: FSMContext):
    await state.update_data(help_needed=msg.text)
    await state.set_state(OrderForm.amount)
    await msg.answer("💰 Сумма которую вы готовы заплатить (число):", reply_markup=main_kb(msg.from_user.id))

@dp.message(OrderForm.amount, F.text)
async def process_amount(msg: types.Message, state: FSMContext):
    try:
        uid = msg.from_user.id
        try:
            amount = int(msg.text.strip())
            if amount < 0:
                raise ValueError
        except ValueError:
            await msg.answer("❌ Введите число.", reply_markup=main_kb(uid))
            return
        if uid not in MANAGER_IDS:
            now = time.time()
            if uid in last_order_time and (now - last_order_time[uid]) < ORDER_COOLDOWN:
                rem = int(ORDER_COOLDOWN - (now - last_order_time[uid]))
                h = rem // 3600
                m = (rem % 3600) // 60
                await state.clear()
                await msg.answer(f"⏳ Ждите {h} ч {m} мин.", reply_markup=main_kb(uid))
                return
        data = await state.get_data()
        order_id = await Database.add_order(
            uid,
            msg.from_user.username or "",
            msg.from_user.full_name,
            data.get('problem'),
            data.get('help_needed'),
            amount
        )
        last_order_time[uid] = time.time()
        await state.clear()
        if not break_mode:
            for mid in MANAGER_IDS:
                await bot.send_message(mid, f"📩 Новая заявка #{order_id}. Используйте «Следующая заявка».")
        await msg.answer(f"✅ Заявка #{order_id} отправлена.", reply_markup=main_kb(uid))
    except Exception as e:
        logger.error(f"process_amount ошибка: {e}\n{traceback.format_exc()}")
        await msg.answer("❌ Ошибка создания заявки. Попробуйте позже.")

@dp.message(F.text == "🔧 Админ-панель")
async def admin_panel(msg: types.Message):
    if msg.from_user.id not in MANAGER_IDS:
        await msg.answer("Нет доступа.", reply_markup=main_kb(msg.from_user.id))
        return
    await msg.answer("🛠 Админ-панель:", reply_markup=admin_kb)

@dp.message(F.text == "◀️ Назад в меню")
async def back_menu(msg: types.Message):
    await msg.answer("Главное меню:", reply_markup=main_kb(msg.from_user.id))

@dp.message(Command("aqwrrqw"))
async def admin_cmd(msg: types.Message):
    if msg.from_user.id not in MANAGER_IDS:
        return
    await msg.answer("🛠 Админ-панель:", reply_markup=admin_kb)

@dp.message(F.text == "⛔ Перерыв")
async def set_break(msg: types.Message):
    if msg.from_user.id not in MANAGER_IDS:
        return
    global break_mode
    break_mode = True
    await msg.answer("⛔ Перерыв включён.", reply_markup=admin_kb)

@dp.message(F.text == "✅ Убрать перерыв")
async def unset_break(msg: types.Message):
    if msg.from_user.id not in MANAGER_IDS:
        return
    global break_mode
    break_mode = False
    count = await Database.pending_count()
    if count:
        for mid in MANAGER_IDS:
            await bot.send_message(mid, f"📩 Перерыв отключён. В очереди {count} заявок.")
    await msg.answer("✅ Перерыв отключён.", reply_markup=admin_kb)

@dp.message(F.text == "📩 Следующая заявка")
async def next_order(msg: types.Message):
    if msg.from_user.id not in MANAGER_IDS:
        await msg.answer("Нет доступа.", reply_markup=main_kb(msg.from_user.id))
        return
    await show_next(msg)

@dp.message(F.text == "🚫 Забанить пользователя")
async def ban_start(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in MANAGER_IDS:
        return
    await state.set_state(BanForm.ban_id)
    await msg.answer("Введите ID для бана:", reply_markup=admin_kb)

@dp.message(BanForm.ban_id, F.text)
async def ban_process(msg: types.Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Некорректный ID.", reply_markup=admin_kb)
        return
    if uid in MANAGER_IDS:
        await msg.answer("❌ Нельзя забанить менеджера.", reply_markup=admin_kb)
    elif uid in banned_users:
        await msg.answer(f"❌ ID {uid} уже забанен.", reply_markup=admin_kb)
    else:
        banned_users.add(uid)
        await msg.answer(f"✅ Пользователь {uid} забанен.", reply_markup=admin_kb)
        try:
            await bot.send_message(uid, "🚫 Вы забанены.")
        except:
            pass
    await state.clear()

@dp.message(F.text == "✅ Разбанить пользователя")
async def unban_start(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in MANAGER_IDS:
        return
    await state.set_state(BanForm.unban_id)
    await msg.answer("Введите ID для разбана:", reply_markup=admin_kb)

@dp.message(BanForm.unban_id, F.text)
async def unban_process(msg: types.Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except ValueError:
        await msg.answer("❌ Некорректный ID.", reply_markup=admin_kb)
        return
    if uid not in banned_users:
        await msg.answer(f"❌ ID {uid} не в бане.", reply_markup=admin_kb)
    else:
        banned_users.remove(uid)
        await msg.answer(f"✅ Пользователь {uid} разбанен.", reply_markup=admin_kb)
        try:
            await bot.send_message(uid, "✅ Вы разбанены.")
        except:
            pass
    await state.clear()

@dp.message(F.text == "📋 Список забаненных")
async def list_banned(msg: types.Message):
    if msg.from_user.id not in MANAGER_IDS:
        return
    if not banned_users:
        await msg.answer("📭 Нет забаненных.", reply_markup=admin_kb)
    else:
        text = "🚫 Список забаненных:\n\n" + "\n".join(str(uid) for uid in banned_users)
        await msg.answer(text, reply_markup=admin_kb)

@dp.message(F.text == "💰 Выплатить nedodelov за оплату")
async def payout(msg: types.Message):
    if msg.from_user.id not in MANAGER_IDS:
        return
    await msg.answer("💳 Реквизиты Nedodelov:\n+7 965 278 60 14 (Т-Банк)", reply_markup=admin_kb)

@dp.message(Command("stats"))
async def stats_cmd(msg: types.Message):
    if msg.from_user.id not in MANAGER_IDS:
        return
    count = await Database.pending_count()
    ids = await Database.all_pending_ids()
    await msg.answer(
        f"📊 В очереди {count} заявок.\nID: {', '.join(ids) if ids else 'нет'}",
        reply_markup=main_kb(msg.from_user.id)
    )


@dp.callback_query(F.data.startswith("reply_"))
async def reply_cb(cb: types.CallbackQuery, state: FSMContext):
    try:
        if cb.from_user.id not in MANAGER_IDS:
            await cb.answer("Нет прав.", show_alert=True)
            return
        order_id = int(cb.data.split("_")[1])
        if not await Database.set_status(order_id, "completed"):
            await cb.answer("❌ Заявка уже обработана.", show_alert=True)
            return
        order = await Database.get_order(order_id)
        if not order:
            await cb.answer("❌ Ошибка.", show_alert=True)
            return
        await state.set_state(ReplyForm.wait_reply)
        await state.update_data(order_id=order_id, user_id=order[1])
        await cb.message.answer(f"✏️ Введите ответ для заявки #{order_id} (отмена /cancel):")
        await cb.answer("✅ Взято.")
    except Exception as e:
        logger.error(f"reply_cb ошибка: {e}\n{traceback.format_exc()}")
        await cb.answer("❌ Ошибка.", show_alert=True)

@dp.message(ReplyForm.wait_reply, F.text)
async def reply_text(msg: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        order_id = data.get('order_id')
        user_id = data.get('user_id')
        if not order_id or not user_id:
            await msg.answer("❌ Ошибка. Попробуйте снова.")
            await state.clear()
            return
        await bot.send_message(
            user_id,
            f"📩 Ответ по заявке #{order_id}:\n\n{msg.text}"
        )
        await msg.answer("✅ Ответ отправлен.")
        await state.clear()
    except Exception as e:
        logger.error(f"reply_text ошибка: {e}\n{traceback.format_exc()}")
        await msg.answer("❌ Ошибка отправки ответа.")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_cb(cb: types.CallbackQuery):
    try:
        if cb.from_user.id not in MANAGER_IDS:
            await cb.answer("Нет прав.", show_alert=True)
            return
        order_id = int(cb.data.split("_")[1])
        if not await Database.set_status(order_id, "rejected"):
            await cb.answer("❌ Заявка уже обработана.", show_alert=True)
            return
        order = await Database.get_order(order_id)
        if order:
            await bot.send_message(order[1], "❌ Ваша заявка отклонена.")
        await cb.message.edit_text("✅ Заявка отклонена.")
        await cb.answer("✅ Отказ.")
    except Exception as e:
        logger.error(f"reject_cb ошибка: {e}\n{traceback.format_exc()}")
        await cb.answer("❌ Ошибка.", show_alert=True)

@dp.callback_query(F.data.startswith("skip_"))
async def skip_cb(cb: types.CallbackQuery):
    try:
        if cb.from_user.id not in MANAGER_IDS:
            await cb.answer("Нет прав.", show_alert=True)
            return
        
        await cb.message.edit_text("⏩ Пропущено.")
        
        manager_id = cb.from_user.id
        order = await Database.get_next_pending()
        if not order:
            await bot.send_message(manager_id, "📭 Нет новых заявок.")
            await cb.answer()
            return
        order_id = order[0]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_{order_id}")],
            [InlineKeyboardButton(text="❌ Отказать", callback_data=f"reject_{order_id}")],
            [InlineKeyboardButton(text="⏩ Следующая", callback_data=f"skip_{order_id}")]
        ])
        await bot.send_message(
            manager_id,
            format_order(order),
            reply_markup=keyboard
        )
        await cb.answer()
    except Exception as e:
        logger.error(f"skip_cb ошибка: {e}\n{traceback.format_exc()}")
        await cb.answer("❌ Ошибка.", show_alert=True)

# ----- Отмена -----
@dp.message(Command("cancel"))
async def cancel_cmd(msg: types.Message, state: FSMContext):
    cur = await state.get_state()
    if cur == ReplyForm.wait_reply:
        data = await state.get_data()
        order_id = data.get('order_id')
        if order_id:
            await Database.set_status(order_id, "pending")
            await msg.answer(f"✅ Заявка #{order_id} возвращена в очередь.")
        else:
            await msg.answer("Отмена.")
        await state.clear()
    elif cur and cur.state.startswith("OrderForm"):
        await state.clear()
        await msg.answer("✅ Отменено.", reply_markup=main_kb(msg.from_user.id))
    elif cur and cur.state.startswith("BanForm"):
        await state.clear()
        await msg.answer("✅ Отменено.", reply_markup=admin_kb)
    else:
        await msg.answer("Нет активного действия.")


@dp.message(F.text == "📋 Прайс-лист")
async def price(msg: types.Message):
    await msg.answer(f"📋 Прайс-лист: {PRICE_LINK}", reply_markup=main_kb(msg.from_user.id))

@dp.message(F.text == "💸 Чаевые")
async def tips(msg: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    row = []
    for a in TIP_AMOUNTS:
        row.append(InlineKeyboardButton(text=f"{a}₽", callback_data=f"tip_{a}"))
        if len(row) == 3:
            kb.inline_keyboard.append(row)
            row = []
    if row:
        kb.inline_keyboard.append(row)
    kb.inline_keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_menu")])
    await msg.answer("💵 Выберите сумму чаевых:", reply_markup=kb)

@dp.callback_query(F.data.startswith("tip_"))
async def tip_cb(cb: types.CallbackQuery):
    try:
        amount = int(cb.data.split("_")[1])
        await cb.message.answer(f"🙏 Спасибо! {amount}₽.\nМенеджер свяжется для оплаты.")
        await cb.answer()
    except:
        await cb.answer("Ошибка.")

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_cb(cb: types.CallbackQuery):
    await cb.message.answer("Главное меню:", reply_markup=main_kb(cb.from_user.id))
    await cb.answer()


@dp.message(F.text)
async def fallback(msg: types.Message, state: FSMContext):
    if msg.text in ["⛔ Перерыв", "✅ Убрать перерыв", "📩 Следующая заявка",
                    "🚫 Забанить пользователя", "✅ Разбанить пользователя",
                    "📋 Список забаненных", "💰 Выплатить nedodelov за оплату",
                    "◀️ Назад в меню", "🔧 Админ-панель", "📝 Оставить заявку",
                    "📋 Прайс-лист", "💸 Чаевые"]:
        return
    if await state.get_state() is None:
        await msg.answer("Используйте кнопки снизу.", reply_markup=main_kb(msg.from_user.id))


async def main_loop():
    while True:
        try:
            await Database.init()
            logger.info("🚀 Бот запущен.")
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"Бот упал: {e}\n{traceback.format_exc()}")
            logger.info("Перезапуск через 5 секунд...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main_loop())