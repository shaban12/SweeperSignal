import asyncio
import logging
import os
from datetime import datetime
import ccxt.async_support as ccxt
import pandas as pd
import talib
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
import matplotlib.pyplot as plt
import io

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
exchange = ccxt.binance({'enableRateLimit': True})

user_settings = {
    'pairs': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'],
    'timeframes': ['15m'],
    'scanning': False,
    'last_signal': {}
}

ADD_PAIR, ADD_TF = range(2)

async def send_chart(bot, pair, tf, ohlcv):
    df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
    df['sma200'] = df['c'].rolling(200).mean()
    plt.figure(figsize=(10,5))
    plt.plot(df['c'][-80:], color='cyan', label='Close')
    plt.plot(df['sma200'][-80:], color='orange', label='SMA200')
    plt.title(f"{pair} {tf} – Engulf Sweep")
    plt.legend(); plt.grid(alpha=0.3)
    plt.style.use('dark_background')
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0); plt.close()
    await bot.send_photo(CHANNEL_ID, buf, caption=f"{pair} {tf}")

async def scanner(bot):
    while True:
        if not user_settings['scanning']:
            await asyncio.sleep(10); continue
        for pair in user_settings['pairs']:
            for tf in user_settings['timeframes']:
                try:
                    bars = await exchange.fetch_ohlcv(pair, tf, limit=300)
                    df = pd.DataFrame(bars, columns=['ts','open','high','low','close','volume'])
                    if len(df) < 3: continue
                    prev, curr = df.iloc[-3], df.iloc[-2]
                    close = curr.close
                    key = f"{pair}-{tf}"

                    rsi = talib.RSI(df['close'], 14)[-2]
                    adx = talib.ADX(df['high'], df['low'], df['close'], 14)[-2]
                    sma200 = talib.SMA(df['close'], 200)[-2]
                    vol_ma20 = talib.SMA(df['volume'], 20)[-2]
                    body = abs(curr.close - curr.open)

                    # LONG
                    if (prev.close < prev.open and curr.close > curr.open and curr.low < prev.low and
                        curr.open <= prev.close and curr.close >= prev.open and curr.close > sma200 and
                        rsi < 35 and curr.volume > 1.5 * vol_ma20 and adx > 20 and
                        user_settings['last_signal'].get(key, 0) < len(df)-10):

                        sl = curr.low - body
                        tp = close + 4*(close - sl)
                        await bot.send_message(CHANNEL_ID,
                            f"Long Signal – RSI ENGULF SWEEP v2\n{pair} | {tf}\nEntry: \( {close:.4f}\nSL: \){sl:.4f}\nTP 4R: ${tp:.4f}\n@MontyTheGuy_Signals",
                            parse_mode='Markdown')
                        await send_chart(bot, pair, tf, bars)
                        user_settings['last_signal'][key] = len(df)

                    # SHORT (mirror)
                    if (prev.close > prev.open and curr.close < curr.open and curr.high > prev.high and
                        curr.open >= prev.close and curr.close <= prev.open and curr.close < sma200 and
                        rsi > 65 and curr.volume > 1.5 * vol_ma20 and adx > 20 and
                        user_settings['last_signal'].get(key, 0) < len(df)-10):

                        sl = curr.high + body
                        tp = close - 4*(sl - close)
                        await bot.send_message(CHANNEL_ID,
                            f"Short Signal – RSI ENGULF SWEEP v2\n{pair} | {tf}\nEntry: \( {close:.4f}\nSL: \){sl:.4f}\nTP 4R: ${tp:.4f}\n@MontyTheGuy_Signals",
                            parse_mode='Markdown')
                        await send_chart(bot, pair, tf, bars)
                        user_settings['last_signal'][key] = len(df)

                    await asyncio.sleep(0.5)
                except Exception as e:
                    logging.error(str(e))
        await asyncio.sleep(20)

async def start(update, context):
    kb = [[InlineKeyboardButton("Coins", callback_data='coins'), InlineKeyboardButton("TFs", callback_data='tfs')],
          [InlineKeyboardButton("Start/Stop", callback_data='toggle')]]
    await update.message.reply_text(
        f"RSI-Engulf Sweep Bot v2\nCoins: {', '.join(user_settings['pairs'])}\nTFs: {', '.join(user_settings['timeframes'])}\nStatus: {'ON' if user_settings['scanning'] else 'OFF'}",
        reply_markup=InlineKeyboardMarkup(kb))

async def button(update, context):
    q = update.callback_query; await q.answer()
    if q.data == 'toggle':
        user_settings['scanning'] = not user_settings['scanning']
        await q.edit_message_text(f"Scanning {'ON' if user_settings['scanning'] else 'OFF'}")
    elif q.data == 'coins':
        await q.message.reply_text("Send coins (e.g. BTC/USDT ETH/USDT)")
        return ADD_PAIR
    elif q.data == 'tfs':
        await q.message.reply_text("Send timeframes (e.g. 15m 1h)")
        return ADD_TF

async def add_pairs(update, context):
    user_settings['pairs'] = [p.strip() for p in update.message.text.upper().split() if '/' in p]
    await update.message.reply_text(f"Coins → {', '.join(user_settings['pairs'])}")
    return ConversationHandler.END

async def add_tfs(update, context):
    user_settings['timeframes'] = update.message.text.strip().split()
    await update.message.reply_text(f"TFs → {', '.join(user_settings['timeframes'])}")
    return ConversationHandler.END

app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(ConversationHandler(
    entry_points=[CallbackQueryHandler(button)],
    states={ADD_PAIR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_pairs)],
            ADD_TF: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tfs)]},
    fallbacks=[]))

async def main():
    await app.initialize(); await app.start(); await app.updater.start_polling()
    asyncio.create_task(scanner(app.bot))
    while True: await asyncio.sleep(3600)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
