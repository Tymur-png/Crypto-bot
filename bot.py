import o
import asyncio
import logging
import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
RAILWAY_URL = os.getenv("RAILWAY_URL")  # Railway добавляет это значение автоматически

INTERVALS = [0.1, 1, 5, 10]
COINS = [
    'BTC', 'ETH', 'BNB', 'SOL', 'ADA', 'XRP', 'DOT', 'DOGE', 'AVAX', 'MATIC',
    'LTC', 'SHIB', 'TRX', 'ATOM', 'LINK', 'ALGO', 'XLM', 'FTM', 'VET', 'ICP',
    'THETA', 'FIL', 'EOS', 'AAVE', 'MKR', 'NEAR', 'CAKE', 'SAND', 'GRT', 'KSM'
]
CURRENCIES = ['USDT', 'BUSD', 'USDC', 'BTC', 'ETH', 'DAI']

user_states = {}

# --- Получение цены ---
async def get_price(symbol: str) -> float:
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise ValueError(f"HTTP ошибка: {resp.status_code}")
    data = resp.json()
    if 'price' not in data:
        raise ValueError(f"Ключ 'price' отсутствует в ответе Binance. Ответ: {data}")
    return float(data['price'])

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_states[chat_id] = {'history': []}
    keyboard = []
    row = []

    for i, coin in enumerate(COINS, 1):
        row.append(InlineKeyboardButton(coin, callback_data=f"coin_{coin}"))
        if i % 5 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Выбери монету для отслеживания:", reply_markup=reply_markup)

# --- Выбор монеты ---
async def coin_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    coin = query.data.split('_')[1]
    user_states.setdefault(chat_id, {})['coin'] = coin

    keyboard = []
    row = []
    for i, curr in enumerate(CURRENCIES, 1):
        row.append(InlineKeyboardButton(curr, callback_data=f"currency_{curr}"))
        if i % 5 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Вы выбрали монету {coin}. Теперь выберите валюту:", reply_markup=reply_markup)

# --- Выбор валюты ---
async def currency_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    currency = query.data.split('_')[1]
    user_states.setdefault(chat_id, {})['currency'] = currency

    keyboard = [[InlineKeyboardButton(str(interval), callback_data=f"interval_{interval}") for interval in INTERVALS]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"Вы выбрали валюту {currency}. Выберите интервал:", reply_markup=reply_markup)

# --- Выбор интервала ---
async def interval_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    interval = float(query.data.split('_')[1])
    user_states.setdefault(chat_id, {})['interval'] = interval

    coin = user_states[chat_id]['coin']
    currency = user_states[chat_id]['currency']
    symbol = coin + currency

    try:
        price = await get_price(symbol)
    except Exception as e:
        await query.edit_message_text(f"Ошибка при получении цены для {symbol}: {e}")
        return

    user_states[chat_id]['last_price'] = price
    user_states[chat_id]['awaiting_input'] = 'drop_threshold'

    await query.edit_message_text(
        f"Текущая цена {symbol}: {price:.4f}\n"
        f"Теперь введи, насколько должна УПАСТЬ цена для уведомления:"
    )

# --- Обработка ввода порогов ---
async def handle_threshold_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_states.get(chat_id)
    if not state or 'awaiting_input' not in state:
        return

    try:
        value = float(update.message.text.replace(',', '.'))
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число.")
        return

    if state['awaiting_input'] == 'drop_threshold':
        state['drop_threshold'] = value
        state['awaiting_input'] = 'rise_threshold'
        await update.message.reply_text("Теперь введи, насколько должна ВЫРАСТИ цена для уведомления:")
    elif state['awaiting_input'] == 'rise_threshold':
        state['rise_threshold'] = value
        del state['awaiting_input']

        coin = state['coin']
        currency = state['currency']
        interval = state['interval']
        price = state['last_price']

        await update.message.reply_text(
            f"✅ Настройка завершена!\n"
            f"Отслеживаем {coin + currency}.\nТекущая цена: {price:.4f}\n"
            f"Порог падения: -{state['drop_threshold']}, порог роста: +{state['rise_threshold']}"
        )

        if 'price_task' not in context.application.bot_data:
            context.application.bot_data['price_task'] = context.application.create_task(price_watcher(context))

# --- Отслеживание цены ---
async def price_watcher(context: ContextTypes.DEFAULT_TYPE):
    while True:
        for chat_id, state in user_states.items():
            if all(k in state for k in ('coin', 'currency', 'last_price', 'drop_threshold', 'rise_threshold')):
                symbol = state['coin'] + state['currency']
                try:
                    current_price = await get_price(symbol)
                except Exception:
                    continue

                last_price = state['last_price']
                diff = current_price - last_price
                send = False

                if diff <= -state['drop_threshold']:
                    direction = "📉 Падение"
                    send = True
                elif diff >= state['rise_threshold']:
                    direction = "📈 Рост"
                    send = True

                if send:
                    msg = f"{direction} {symbol}: {current_price:.4f} ({diff:+.4f})"
                    try:
                        await context.bot.send_message(chat_id=chat_id, text=msg)
                    except Exception:
                        pass
                    state['last_price'] = current_price
                    state['history'].append(msg)
                    if len(state['history']) > 20:
                        state['history'].pop(0)
        await asyncio.sleep(15)

# --- Команда /history ---
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_states.get(chat_id)
    if not state or 'history' not in state or not state['history']:
        await update.message.reply_text("История уведомлений пока пуста.")
        return
    await update.message.reply_text("Последние уведомления:\n" + "\n".join(state['history']))

# --- Команда /status ---
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_states.get(chat_id)
    if not state:
        await update.message.reply_text("Вы ещё не настроили бота. Введите /start для начала.")
        return

    coin = state.get('coin')
    currency = state.get('currency')
    interval = state.get('interval')
    last_price = state.get('last_price')
    drop = state.get('drop_threshold')
    rise = state.get('rise_threshold')

    if not (coin and currency and interval and last_price):
        await update.message.reply_text("Настройка не завершена. Введите /start.")
        return

    await update.message.reply_text(
        f"Текущие настройки:\nМонета: {coin}\nВалюта: {currency}\nИнтервал: {interval}\n"
        f"Порог падения: {drop}\nПорог роста: {rise}\nПоследняя цена: {last_price:.4f}"
    )

# --- Запуск приложения ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    PORT = int(os.getenv("PORT", 8000))
    WEBHOOK_PATH = f"/{BOT_TOKEN}"
    WEBHOOK_URL = f"https://{RAILWAY_URL}/{BOT_TOKEN}"

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CallbackQueryHandler(coin_chosen, pattern="^coin_"))
    app.add_handler(CallbackQueryHandler(currency_chosen, pattern="^currency_"))
    app.add_handler(CallbackQueryHandler(interval_chosen, pattern="^interval_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_threshold_input))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_path=WEBHOOK_PATH,
        webhook_url=WEBHOOK_URL,
    )