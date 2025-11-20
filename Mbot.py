import logging
import sqlite3
from datetime import datetime, time, timedelta
import asyncio
from telegram import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Update
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler
)
from telegram.ext import filters
import re

# Настройка логирования - отключаем лишние логи
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
ADD_PRODUCT, ADD_DATE = range(2)
EDIT_NAME, EDIT_DATE = range(2, 4)


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('warranty_bot.db', check_same_thread=False)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            warranty_date TEXT NOT NULL,
            category TEXT,
            store TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    return conn


# Главное меню
def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📦 Добавить товар"), KeyboardButton("📋 Мои товары")]
    ], resize_keyboard=True)


# Меню отмены (для состояний добавления/редактирования)
def cancel_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("↩️ Отмена")]
    ], resize_keyboard=True)


# Функция для преобразования даты с коротким годом
def parse_date_with_short_year(date_text):
    # Проверяем формат ДД.ММ.ГГ или ДД.ММ.ГГГГ
    if re.match(r'^\d{1,2}\.\d{1,2}\.\d{2}$', date_text):
        # Преобразуем год из двух цифр в четыре
        parts = date_text.split('.')
        day = parts[0].zfill(2)
        month = parts[1].zfill(2)
        year_short = parts[2]
        year_full = f"20{year_short}"  # Предполагаем 21 век
        return f"{day}.{month}.{year_full}"
    elif re.match(r'^\d{1,2}\.\d{1,2}\.\d{4}$', date_text):
        parts = date_text.split('.')
        day = parts[0].zfill(2)
        month = parts[1].zfill(2)
        year_full = parts[2]
        return f"{day}.{month}.{year_full}"
    else:
        return None

# Функция для отправки ежедневных напоминаний
async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Запуск ежедневной проверки напоминаний...")

    conn = context.bot_data['db_connection']
    cursor = conn.cursor()

    today = datetime.now().date()

    # Находим все активные товары (гарантия еще не истекла)
    cursor.execute('''
        SELECT DISTINCT user_id, product_name, warranty_date 
        FROM products 
        WHERE warranty_date >= ?
    ''', (today.strftime('%Y-%m-%d'),))

    products = cursor.fetchall()

    reminders_sent = 0

    for user_id, product_name, warranty_date_str in products:
        warranty_date = datetime.strptime(warranty_date_str, '%Y-%m-%d').date()
        days_left = (warranty_date - today).days

        # ✅ ПРАВИЛЬНО: уведомления ТОЛЬКО за 30, 14, 7, 1, 0 дней
        if days_left in [30, 14, 7, 1, 0]:
            if days_left == 0:
                message = f"⚠️ *СРОЧНО!* Гарантия на '{product_name}' истекает сегодня!"
            elif days_left == 1:
                message = f"🔔 Завтра истекает гарантия на '{product_name}'"
            elif days_left == 7:
                message = f"📢 Неделя осталась! Гарантия на '{product_name}' истекает через 7 дней"
            elif days_left == 14:
                message = f"📅 Напоминание: до окончания гарантии на '{product_name}' осталось 14 дней"
            elif days_left == 30:
                message = f"📅 Напоминание: до окончания гарантии на '{product_name}' остался 1 месяц"

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='Markdown'
                )
                reminders_sent += 1
                logger.info(
                    f"Отправлено напоминание пользователю {user_id} для товара {product_name} (осталось {days_left} дней)")

                # Небольшая задержка между сообщениями чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

    logger.info(f"Ежедневная проверка завершена. Отправлено напоминаний: {reminders_sent}")

# Старт бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.message.from_user
    welcome_text = f"""
*Ну здарова, аферист!*

Так и быть, я помогу тебе не просрать копейку за товар, который еще можно вернуть.
Напоминать буду за 30, 14, 7, 1 день и в день окончания

*Выберите действие в меню ниже* 👇
    """

    await update.message.reply_text(welcome_text, reply_markup=main_menu(), parse_mode='Markdown')


# Начало добавления товара
async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "*📝 Введите название товара:*",
        reply_markup=cancel_menu(),
        parse_mode='Markdown'
    )
    return ADD_PRODUCT


# Получение названия товара
async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    product_name = update.message.text

    if product_name == "↩️ Отмена":
        return await cancel_add(update, context)

    # Проверяем, не является ли ввод командой бота
    if product_name in ["📦 Добавить товар", "📋 Мои товары"]:
        await update.message.reply_text(
            "❌ *Нельзя использовать команды бота в качестве названия товара!*\n\nДавай другое:",
            reply_markup=cancel_menu(),
            parse_mode='Markdown'
        )
        return ADD_PRODUCT

    context.user_data['new_product'] = {'name': product_name}

    await update.message.reply_text(
        "*📅 Введите дату окончания гарантии в формате ДД.ММ.ГГ:*\n\n*Например: 30.12.25*",
        reply_markup=cancel_menu(),
        parse_mode='Markdown'
    )
    return ADD_DATE


# Получение даты и сохранение товара
async def add_product_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_text = update.message.text

    if date_text == "↩️ Отмена":
        return await cancel_add(update, context)

    # Преобразуем дату с коротким годом в полный формат
    normalized_date = parse_date_with_short_year(date_text)

    if not normalized_date:
        await update.message.reply_text(
            "❌ *Неверный формат даты! Используйте ДД.ММ.ГГГГ или ДД.ММ.ГГ*\n\nПопробуйте еще раз:",
            reply_markup=cancel_menu(),
            parse_mode='Markdown'
        )
        return ADD_DATE

    # Проверка формата даты
    if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', normalized_date):
        await update.message.reply_text(
            "❌ *Неверный формат даты! Используйте ДД.ММ.ГГГГ или ДД.ММ.ГГ*\n\nПопробуйте еще раз:",
            reply_markup=cancel_menu(),
            parse_mode='Markdown'
        )
        return ADD_DATE

    try:
        warranty_date = datetime.strptime(normalized_date, '%d.%m.%Y').date()
        today = datetime.now().date()

        if warranty_date <= today:
            await update.message.reply_text(
                "❌ *Дата должна быть в будущем!*\n\nВведите корректную дату:",
                reply_markup=cancel_menu(),
                parse_mode='Markdown'
            )
            return ADD_DATE

    except ValueError:
        await update.message.reply_text(
            "❌ *Неверная дата! Проверьте правильность ввода.*\n\nПопробуйте еще раз:",
            reply_markup=cancel_menu(),
            parse_mode='Markdown'
        )
        return ADD_DATE

    # Сохранение в базу данных
    conn = context.bot_data['db_connection']
    cursor = conn.cursor()

    product_name = context.user_data['new_product']['name']
    cursor.execute(
        'INSERT INTO products (user_id, product_name, warranty_date) VALUES (?, ?, ?)',
        (update.message.from_user.id, product_name, warranty_date.strftime('%Y-%m-%d'))
    )
    conn.commit()

    # Очистка временных данных
    context.user_data.pop('new_product', None)

    # Расчет дней до окончания
    days_left = (warranty_date - today).days

    await update.message.reply_text(
        f"✅ *Товар успешно добавлен!*\n\n"
        f"📦 *Название:* {product_name}\n"
        f"📅 *Гарантия до:* {warranty_date.strftime('%d.%m.%Y')}\n"
        f"⏳ *Осталось дней:* {days_left}\n\n"
        f"*Не ссы, я напомню об окончании гарантии заранее!*",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )

    return ConversationHandler.END


# Отмена добавления товара
async def cancel_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop('new_product', None)
    await update.message.reply_text(
        "❌ *Добавление товара отменено.*",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )
    return ConversationHandler.END


# Показать все товары пользователя с кнопками управления
async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    conn = context.bot_data['db_connection']
    cursor = conn.cursor()

    cursor.execute(
        'SELECT id, product_name, warranty_date FROM products WHERE user_id = ? ORDER BY warranty_date',
        (user_id,)
    )
    products = cursor.fetchall()

    if not products:
        await update.message.reply_text(
            "*📭 У вас пока нет добавленных товаров.*\n\n*Нажмите* \"📦 Добавить товар\"*, чтобы добавить первый товар.*",
            reply_markup=main_menu(),
            parse_mode='Markdown'
        )
        return

    today = datetime.now().date()
    message = "*📋 Ваши товары:*\n\n"

    # Создаем клавиатуру с товарами
    keyboard = []

    for product in products:
        product_id, product_name, warranty_date_str = product
        warranty_date = datetime.strptime(warranty_date_str, '%Y-%m-%d').date()
        days_left = (warranty_date - today).days

        # Убрали статус "Активна" - показываем только предупреждения
        if days_left < 0:
            status = "❌ Просрочено"
        elif days_left == 0:
            status = "⚠️ Заканчивается сегодня"
        elif days_left <= 7:
            status = "🔥 Срочно"
        elif days_left <= 30:
            status = "⚠️ Скоро закончится"
        else:
            status = None

        # Добавляем информацию о товаре в сообщение
        message += f"📦 *{product_name}*\n"
        message += f"📅 *До:* {warranty_date.strftime('%d.%m.%Y')}\n"
        message += f"⏳ *Осталось:* {days_left} дней\n"
        if status:
            message += f"📊 *{status}*\n"
        message += "\n"

        # Добавляем кнопку редактирования для каждого товара
        display_name = product_name[:30] + "..." if len(product_name) > 30 else product_name
        keyboard.append([
            InlineKeyboardButton(f"✏️ {display_name}", callback_data=f"edit_{product_id}")
        ])

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


# Обработка выбора товара для редактирования
async def edit_product_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    product_id = query.data.split('_')[1]
    context.user_data['editing_product_id'] = product_id

    # Получаем информацию о товаре
    conn = context.bot_data['db_connection']
    cursor = conn.cursor()
    cursor.execute(
        'SELECT product_name, warranty_date FROM products WHERE id = ?',
        (product_id,)
    )
    product = cursor.fetchone()

    if product:
        product_name, warranty_date = product
        formatted_date = datetime.strptime(warranty_date, '%Y-%m-%d').strftime('%d.%m.%Y')
        today = datetime.now().date()
        warranty_date_obj = datetime.strptime(warranty_date, '%Y-%m-%d').date()
        days_left = (warranty_date_obj - today).days

        # Клавиатура управления товаром
        keyboard = [
            [InlineKeyboardButton("✏️ Изменить название", callback_data="edit_name")],
            [InlineKeyboardButton("📅 Изменить дату гарантии", callback_data="edit_date")],
            [InlineKeyboardButton("🗑️ Удалить товар", callback_data="delete_product")],
            [InlineKeyboardButton("↩️ Назад к списку", callback_data="back_to_list")]
        ]

        # Убрали статус "Активна"
        status = None
        if days_left < 0:
            status = "❌ Просрочено"
        elif days_left <= 7:
            status = "🔥 Срочно"
        elif days_left <= 30:
            status = "⚠️ Скоро закончится"

        status_text = f"📊 *Статус:* {status}\n" if status else ""

        await query.edit_message_text(
            f"*✏️ Управление товаром:*\n\n"
            f"📦 *{product_name}*\n"
            f"📅 *Гарантия до:* {formatted_date}\n"
            f"⏳ *Осталось дней:* {days_left}\n"
            f"{status_text}\n"
            f"*Выберите действие:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("❌ *Товар не найден.*", parse_mode='Markdown')


# Обработка выбора действия в меню управления товаром
async def edit_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_list":
        await show_products_from_callback(update, context)
        return

    if query.data == "delete_product":
        # Переходим к подтверждению удаления
        product_id = context.user_data.get('editing_product_id')

        if product_id:
            conn = context.bot_data['db_connection']
            cursor = conn.cursor()
            cursor.execute(
                'SELECT product_name FROM products WHERE id = ?',
                (product_id,)
            )
            result = cursor.fetchone()

            if result:
                product_name = result[0]

                # Клавиатура подтверждения удаления
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Да, удалить", callback_data="confirm_delete"),
                        InlineKeyboardButton("❌ Нет, отмена", callback_data="cancel_delete")
                    ]
                ]

                await query.edit_message_text(
                    f"*🗑️ Подтверждение удаления*\n\n"
                    f"*Вы уверены, что хотите удалить товар?*\n\n"
                    f"📦 *{product_name}*\n\n",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
        else:
            await query.edit_message_text("❌ *Ошибка: товар не найден.*", parse_mode='Markdown')

    elif query.data == "edit_name":
        # Редактируем текущее сообщение, убирая инлайн-клавиатуру
        await query.edit_message_text(
            "*✏️ Введите новое название товара:*",
            reply_markup=None,  # Убираем инлайн-клавиатуру для текстового ввода
            parse_mode='Markdown'
        )
        return EDIT_NAME

    elif query.data == "edit_date":
        # Редактируем текущее сообщение, убирая инлайн-клавиатуру
        await query.edit_message_text(
            "*📅 Введите новую дату окончания гарантии (ДД.ММ.ГГ):*",
            reply_markup=None,  # Убираем инлайн-клавиатуру для текстового ввода
            parse_mode='Markdown'
        )
        return EDIT_DATE
# Обработка отмены удаления
async def cancel_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    # Просто возвращаемся к меню управления товаром
    product_id = context.user_data.get('editing_product_id')

    if product_id:
        # Получаем актуальную информацию о товаре из БД
        conn = context.bot_data['db_connection']
        cursor = conn.cursor()
        cursor.execute(
            'SELECT product_name, warranty_date FROM products WHERE id = ?',
            (product_id,)
        )
        product = cursor.fetchone()

        if product:
            product_name, warranty_date = product
            formatted_date = datetime.strptime(warranty_date, '%Y-%m-%d').strftime('%d.%m.%Y')
            today = datetime.now().date()
            warranty_date_obj = datetime.strptime(warranty_date, '%Y-%m-%d').date()
            days_left = (warranty_date_obj - today).days

            # Клавиатура управления товаром
            keyboard = [
                [InlineKeyboardButton("✏️ Изменить название", callback_data="edit_name")],
                [InlineKeyboardButton("📅 Изменить дату гарантии", callback_data="edit_date")],
                [InlineKeyboardButton("🗑️ Удалить товар", callback_data="delete_product")],
                [InlineKeyboardButton("↩️ Назад к списку", callback_data="back_to_list")]
            ]

            # Статус товара
            status = None
            if days_left < 0:
                status = "❌ Просрочено"
            elif days_left <= 7:
                status = "🔥 Срочно"
            elif days_left <= 30:
                status = "⚠️ Скоро закончится"

            status_text = f"📊 *Статус:* {status}\n" if status else ""

            await query.edit_message_text(
                f"*✏️ Управление товаром:*\n\n"
                f"📦 *{product_name}*\n"
                f"📅 *Гарантия до:* {formatted_date}\n"
                f"⏳ *Осталось дней:* {days_left}\n"
                f"{status_text}\n"
                f"*Выберите действие:*",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            # Если товар вдруг не найден (очень редкий случай)
            await query.edit_message_text(
                "❌ *Товар не найден в базе данных.*",
                parse_mode='Markdown'
            )
    else:
        await query.edit_message_text(
            "❌ *Не удалось найти идентификатор товара.*",
            parse_mode='Markdown'
        )

# Обработка подтверждения удаления
async def confirm_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    product_id = context.user_data.get('editing_product_id')

    if product_id:
        conn = context.bot_data['db_connection']
        cursor = conn.cursor()

        # Получаем информацию о товаре перед удалением
        cursor.execute(
            'SELECT product_name FROM products WHERE id = ?',
            (product_id,)
        )
        result = cursor.fetchone()

        if result:
            product_name = result[0]

            # Удаляем товар
            cursor.execute('DELETE FROM products WHERE id = ?', (product_id,))
            conn.commit()

            # Удаляем сообщение с инлайн-клавиатурой и отправляем новое сообщение
            await query.delete_message()
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=f"✅ *Товар успешно удален!*\n\n📦 *{product_name}*\n\n*Больше не отслеживается.*",
                reply_markup=main_menu(),
                parse_mode='Markdown'
            )

            # Очищаем данные о редактировании
            context.user_data.pop('editing_product_id', None)
        else:
            # Если товар не найден, показываем сообщение об ошибке
            await query.edit_message_text(
                "❌ *Товар не найден в базе данных.*",
                reply_markup=None,
                parse_mode='Markdown'
            )
    else:
        await query.edit_message_text(
            "❌ *Ошибка: товар не найден.*",
            reply_markup=None,
            parse_mode='Markdown'
        )

# Обработка изменения названия
async def edit_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Это текстовое сообщение с новым названием
    new_name = update.message.text

    # Проверяем, не является ли ввод командой отмены
    if new_name == "↩️ Отмена":
        await update.message.reply_text(
            "❌ *Изменение названия отменено.*",
            reply_markup=main_menu(),
            parse_mode='Markdown'
        )
        context.user_data.pop('editing_product_id', None)
        return ConversationHandler.END

    # Проверяем, не является ли ввод командой бота
    if new_name in ["📦 Добавить товар", "📋 Мои товары"]:
        await update.message.reply_text(
            "❌ *Нельзя использовать команды бота в качестве названия товара!*\n\nВведите другое название:",
            reply_markup=cancel_menu(),
            parse_mode='Markdown'
        )
        return EDIT_NAME

    product_id = context.user_data.get('editing_product_id')

    if not product_id:
        await update.message.reply_text(
            "❌ *Ошибка: товар не найден.*",
            reply_markup=main_menu(),
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    conn = context.bot_data['db_connection']
    cursor = conn.cursor()

    cursor.execute(
        'UPDATE products SET product_name = ? WHERE id = ?',
        (new_name, product_id)
    )
    conn.commit()

    # Отправляем новое сообщение с обычной клавиатурой
    await update.message.reply_text(
        f"✅ *Название товара успешно изменено на:* {new_name}",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )

    context.user_data.pop('editing_product_id', None)
    return ConversationHandler.END

# Обработка изменения даты
async def edit_product_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Это текстовое сообщение с новой датой
    date_text = update.message.text

    # Проверяем, не является ли ввод командой отмены
    if date_text == "↩️ Отмена":
        await update.message.reply_text(
            "❌ *Изменение даты отменено.*",
            reply_markup=main_menu(),
            parse_mode='Markdown'
        )
        context.user_data.pop('editing_product_id', None)
        return ConversationHandler.END

    product_id = context.user_data.get('editing_product_id')

    if not product_id:
        await update.message.reply_text(
            "❌ *Ошибка: товар не найден.*",
            reply_markup=main_menu(),
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    # Преобразуем дату с коротким годом в полный формат
    normalized_date = parse_date_with_short_year(date_text)

    if not normalized_date:
        await update.message.reply_text(
            "❌ *Неверный формат даты! Используйте ДД.ММ.ГГГГ или ДД.ММ.ГГ*\n\nПопробуйте еще раз:",
            reply_markup=cancel_menu(),
            parse_mode='Markdown'
        )
        return EDIT_DATE

    # Проверка формата даты
    if not re.match(r'^\d{2}\.\d{2}\.\d{4}$', normalized_date):
        await update.message.reply_text(
            "❌ *Неверный формат даты! Используйте ДД.ММ.ГГГГ или ДД.ММ.ГГ*\n\nПопробуйте еще раз:",
            reply_markup=cancel_menu(),
            parse_mode='Markdown'
        )
        return EDIT_DATE

    try:
        warranty_date = datetime.strptime(normalized_date, '%d.%m.%Y').date()
        today = datetime.now().date()

        if warranty_date <= today:
            await update.message.reply_text(
                "❌ *Дата должна быть в будущем!*\n\nВведите корректную дату:",
                reply_markup=cancel_menu(),
                parse_mode='Markdown'
            )
            return EDIT_DATE

    except ValueError:
        await update.message.reply_text(
            "❌ *Неверная дата! Проверьте правильность ввода.*\n\nПопробуйте еще раз:",
            reply_markup=cancel_menu(),
            parse_mode='Markdown'
        )
        return EDIT_DATE

    conn = context.bot_data['db_connection']
    cursor = conn.cursor()

    cursor.execute(
        'UPDATE products SET warranty_date = ? WHERE id = ?',
        (warranty_date.strftime('%Y-%m-%d'), product_id)
    )
    conn.commit()

    # Отправляем новое сообщение с обычной клавиатурой
    await update.message.reply_text(
        f"✅ *Дата гарантии успешно изменена на:* {warranty_date.strftime('%d.%m.%Y')}",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )

    context.user_data.pop('editing_product_id', None)
    return ConversationHandler.END
# Показать товары из callback (для кнопки "Назад")

# Функция отмены редактирования для ConversationHandler
async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop('editing_product_id', None)
    await update.message.reply_text(
        "❌ *Редактирование отменено.*",
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def show_products_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    conn = context.bot_data['db_connection']
    cursor = conn.cursor()

    cursor.execute(
        'SELECT id, product_name, warranty_date FROM products WHERE user_id = ? ORDER BY warranty_date',
        (user_id,)
    )
    products = cursor.fetchall()

    if not products:
        await query.edit_message_text(
            "*📭 У вас пока нет добавленных товаров.*\n\n*Нажмите* \"📦 Добавить товар\"*, чтобы добавить первый товар.*",
            reply_markup=main_menu(),
            parse_mode='Markdown'
        )
        return

    today = datetime.now().date()
    message = "*📋 Ваши товары:*\n\n"

    keyboard = []

    for product in products:
        product_id, product_name, warranty_date_str = product
        warranty_date = datetime.strptime(warranty_date_str, '%Y-%m-%d').date()
        days_left = (warranty_date - today).days

        if days_left < 0:
            status = "❌ Просрочено"
        elif days_left == 0:
            status = "⚠️ Заканчивается сегодня"
        elif days_left <= 7:
            status = "🔥 Срочно"
        elif days_left <= 30:
            status = "⚠️ Скоро закончится"
        else:
            status = None

        message += f"📦 *{product_name}*\n"
        message += f"📅 *До:* {warranty_date.strftime('%d.%m.%Y')}\n"
        message += f"⏳ *Осталось:* {days_left} дней\n"
        if status:
            message += f"📊 *{status}*\n"
        message += "\n"

        display_name = product_name[:30] + "..." if len(product_name) > 30 else product_name
        keyboard.append([
            InlineKeyboardButton(f"✏️ {display_name}", callback_data=f"edit_{product_id}")
        ])

    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


# Обработка текстовых сообщений (главное меню)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text

    if text == "📦 Добавить товар":
        await add_product_start(update, context)
    elif text == "📋 Мои товары":
        await show_products(update, context)
    else:
        await update.message.reply_text(
            "Используйте кнопки меню для навигации",
            reply_markup=main_menu()
        )


# Основная функция
def main() -> None:
    # Создаем Application с правильной инициализацией
    application = (
        Application.builder()
        .token("8576950098:AAEae5qOnqtWCoIFgpWA43ILZfjK7EktmNU")  # ЗАМЕНИТЕ НА ВАШ ТОКЕН
        .build()
    )

    # Инициализация базы данных
    conn = init_db()
    application.bot_data['db_connection'] = conn

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))

    # ConversationHandler для добавления товара
    add_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Text(["📦 Добавить товар"]), add_product_start)],
        states={
            ADD_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)],
            ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_date)],
        },
        fallbacks=[MessageHandler(filters.Text(["↩️ Отмена"]), cancel_add)],
        per_message=False  # Явно указываем для избежания предупреждения
    )

    # ConversationHandler для редактирования товара
    edit_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_product_choice, pattern=r"^edit_\d+$")],
        states={
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_name)],
            EDIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_date)],
        },
        fallbacks=[
            MessageHandler(filters.Text(["↩️ Отмена"]), cancel_edit),  # Используем новую функцию
        ],
        per_message=False
    )

    application.add_handler(add_conv_handler)
    application.add_handler(edit_conv_handler)

    # Отдельные обработчики для callback queries
    application.add_handler(
        CallbackQueryHandler(edit_choice_handler, pattern=r"^(edit_name|edit_date|delete_product)$"))
    application.add_handler(CallbackQueryHandler(cancel_delete_handler, pattern=r"^cancel_delete$"))
    application.add_handler(CallbackQueryHandler(confirm_delete_handler, pattern=r"^confirm_delete$"))
    application.add_handler(CallbackQueryHandler(show_products_from_callback, pattern=r"^back_to_list$"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Запускаем ежедневные напоминания в 13:00
    application.job_queue.run_daily(
        send_daily_reminders,
        time=time(hour=13, minute=0),  # 13:00 по времени сервера
        name="daily_reminders"
    )

    logger.info("Бот запущен с ежедневными напоминаниями в 13:00")

    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()