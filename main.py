import asyncio
import logging
import os
from datetime import datetime
import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
import matplotlib.pyplot as plt
import io

# ========= CONFIG =========
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
exchange = ccxt.binance({'enableRateLimit': True})

# In-memory storage
user_settings = {
    'pairs': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'],
    'timeframes': ['15m'],
    'scanning': False,
    'last_signal': {}
}

ADD_PAIR, ADD_TF = range(2)

# ========= PURE PYTHON INDICATORS (no TA-Lib) =========
def sma(series, period):
    return series.rolling(window=period).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def adx(high, low, close, period=14):
    tr = pd.DataFrame(index=high.index)
    tr['h_l'] = high - low
    tr['h_pc'] = abs(high - close.shift(1))
    tr['l_pc'] = abs(low - close.shift(1))
    tr['tr'] = tr[['h_l', 'h_pc', 'l_pc']].max(axis=1)
    atr = tr['tr'].rolling(window=period).mean()
    up = high - high.shift(1)
    down = low.shift(1) - low
    plus_dm = np.where((up > down) & (up > 0), up, 0)
    minus_dm = np.where((down > up) & (down > 0), down, 0)
    plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    return dx.rolling(window=period).mean()

# ========= CHART =========
async def send_chart(bot, pair, tf, ohlcv):
    df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
    df['sma200'] = sma(df['c'], 200)
    plt.figure(figsize=(10,5))
    plt.plot(df['c'][-80:], color='cyan', label='Close')
    plt.plot(df['sma200'][-80:], color='orange', label='SMA200')
    plt.title(f"{pair} {tf} – Engulf Sweep Signal")
    plt.legend(); plt.grid(alpha=0.3)
    plt.style.use('dark_background')
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0); plt.close()
    await bot.send_photo(CHANNEL_ID, buf, caption=f"{pair} {tf}")

# ========= SCANNER =========
async def scanner(bot):
    while True:
        if not user_settings['scanning']:
            await asyncio.sleep(10); continue

        for pair in user_settings['pairs']:
            for tf in user_settings['timeframes']:
                try:
                    bars = await exchange.fetch_ohlcv(pair, tf, limit=300)
                    if len(bars) < 3: continue
                    df = pd.DataFrame(bars, columns=['ts','open','high','low','close','volume'])
                    df.index = range(len(df))
                    prev, curr = df.iloc[-3], df.iloc[-2]
                    close = curr.close
                    key = f"{pair}-{tf}"

                    # Indicators (pure Python)
                    rsi_val = rsi(df['close'], 14).iloc[-2]
                    adx_val = adx(df['high'], df['low'], df['close'], 14).iloc[-2]
                    sma200 = sma(df['close'], 200).iloc[-2]
                    vol_ma20 = sma(df['volume'], 20).iloc[-2]
                    body = abs(curr.close - curr.open)

                    # LONG
                    if (prev.close < prev.open and curr.close > curr.open and curr.low < prev.low and
                        curr.open <= prev.close and curr.close >= prev.open and curr.close > sma200 and
                        rsi_val < 35 and curr.volume > 1.5 * vol_ma20 and adx_val > 20 and
                        user_settings['last_signal'].get(key, 0) < len(df)-10):

                        sl = curr.low - body
                        tp = close + 4*(close - sl)
                        await bot.send_message(CHANNEL_ID,
                            f"🚀 LONG – RSI ENGULF SWEEP v2\n"
                            f"Pair: {pair} | TF: {tf}\n"
                            f"Entry: ${close:.4f}\n"
                            f"SL: ${sl:.4f}\n"
                            f"TP 4R: ${tp:.4f}\n"
                            f"@MontyTheGuy_Signals", parse_mode='Markdown')
                        await send_chart(bot, pair, tf, bars)
                        user_settings['last_signal'][key] = len(df)

                    # SHORT
                    if (prev.close > prev.open and curr.close < curr.open and curr.high > prev.high and
                        curr.open >= prev.close and curr.close <= prev.open and curr.close < sma200 and
                        rsi_val > 65 and curr.volume > 1.5 * vol_ma20 and adx_val > 20 and
                        user_settings['last_signal'].get(key, 0) < len(df)-10):

                        sl = curr.high + body
                        tp = close - 4*(sl - close)
                        await bot.send_message(CHANNEL_ID,
                            f"🔻 SHORT – RSI ENGULF SWEEP v2\n"
                            f"Pair: {pair} | TF: {tf}\n"
                            f"Entry: ${close:.4f}\n"
                            f"SL: ${sl:.4f}\n"
                            f"TP 4R: ${tp:.4f}\n"
                            f"@MontyTheGuy_Signals", parse_mode='Markdown')
                        await send_chart(bot, pair, tf, bars)
                        user_settings['last_signal'][key] = len(df)

                    await asyncio.sleep(0.5)
                except Exception as e:
                    logging.error(f"Error in scanner: {e}")
        await asyncio.sleep(20)

# ========= TELEGRAM MENU =========
async def start(update, context):
    kb = [
        [InlineKeyboardButton("Edit Coins", callback_data='coins'),
         InlineKeyboardButton("Edit TFs", callback_data='tfs')],
        [InlineKeyboardButton("Start/Stop Scan", callback_data='toggle')],
        [InlineKeyboardButton("Status", callback_data='status')]
    ]
    status_text = f"RSI-Engulf Sweep Bot v2\n\nCoins: {', '.join(user_settings['pairs'])}\nTFs: {', '.join(user_settings['timeframes'])}\nStatus: {'ON' if user_settings['scanning'] else 'OFF'}"
    await update.message.reply_text(status_text, reply_markup=InlineKeyboardMarkup(kb))

async def button(update, context):
    q = update.callback_query; await q.answer()
    if q.data == 'toggle':
        user_settings['scanning'] = not user_settings['scanning']
        await q.edit_message_text(f"Scanning {'ON' if user_settings['scanning'] else 'OFF'}")
    elif q.data == 'coins':
        await q.message.reply_text("Send coins (e.g., BTC/USDT ETH/USDT SOL/USDT)")
        return ADD_PAIR
    elif q.data == 'tfs':
        await q.message.reply_text("Send TFs (e.g., 15m 1h 4h)")
        return ADD_TF
    elif q.data == 'status':
        await q.edit_message_text(f"Scanning {len(user_settings['pairs'])} coins on {len(user_settings['timeframes'])} TFs")

async def add_pairs(update, context):
    text = update.message.text.upper().strip()
    user_settings['pairs'] = [p.strip() for p in text.split() if '/' in p]
    await update.message.reply_text(f"Coins updated: {', '.join(user_settings['pairs'])}")
    return ConversationHandler.END

async def add_tfs(update, context):
    user_settings['timeframes'] = [t.strip() for t in update.message.text.split()]
    await update.message.reply_text(f"TFs updated: {', '.join(user_settings['timeframes'])}")
    return ConversationHandler.END

# ========= RUN =========
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CallbackQueryHandler(button))
conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(button)],
    states={ADD_PAIR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_pairs)],
            ADD_TF: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tfs)]},
    fallbacks=[])
app.add_handler(conv)

async def main():
    await app.initialize(); await app.start(); await app.updater.start_polling()
    asyncio.create_task(scanner(app.bot))
    while True: await asyncio.sleep(3600)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
