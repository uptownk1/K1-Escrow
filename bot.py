import os
import logging
import re
import asyncio
from uuid import uuid4
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# ---------------- CONFIG ----------------
TOKEN = os.environ.get("TOKEN")
ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_ID"))  # Make sure to set as env var
ESCROW_WALLETS = {
    "BTC": "your_btc_wallet_address",
    "ETH": "your_eth_wallet_address",
    "LTC": "your_ltc_wallet_address",
    "SOL": "your_sol_wallet_address"
}
FIAT_CURRENCY = "gbp"

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- ESCROW TRACKING ----------------
escrows = {}

# ---------------- HELPERS ----------------
def parse_amount(text: str):
    clean = re.sub(r"[^\d.]", "", text)
    try:
        return float(clean)
    except:
        return None

def get_crypto_price(coin: str):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin.lower()}&vs_currencies={FIAT_CURRENCY}"
    try:
        r = requests.get(url).json()
        return r[coin.lower()][FIAT_CURRENCY]
    except Exception as e:
        logger.error(f"Error fetching price: {e}")
        return None

def create_buttons(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data)] for text, data in buttons])

def generate_ticket():
    return str(uuid4())[:8]

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Thank you for using K1 Escrow Bot!\n\n"
        "Add this bot into a group with the buyer and seller and type /escrow to start a trade."
    )

async def start_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = create_buttons([("Join as Buyer", "join_buyer"), ("Join as Seller", "join_seller")])
    await update.message.reply_text("Select your role to start escrow:", reply_markup=buttons)

# ---------------- CALLBACK HANDLER ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data
    chat_id = query.message.chat_id
    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = {"group_id": chat_id, "buyer_id": None, "seller_id": None, "status": None,
                  "crypto": None, "fiat_amount": None, "crypto_amount": None, "wallet_address": None,
                  "ticket_number": None, "buyer_confirmed": False, "seller_confirmed": False,
                  "timeout_task": None, "seller_wallet": None}
        escrows[chat_id] = escrow

    # --- handle join buttons ---
    if data == "join_buyer":
        if escrow["buyer_id"]: await query.message.reply_text("Buyer already joined."); return
        escrow["buyer_id"] = user_id
        await query.message.reply_text("You joined as Buyer.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"Buyer @{username} joined group {chat_id}.")
    elif data == "join_seller":
        if escrow["seller_id"]: await query.message.reply_text("Seller already joined."); return
        escrow["seller_id"] = user_id
        await query.message.reply_text("You joined as Seller.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"Seller @{username} joined group {chat_id}.")

    # --- both joined, start crypto selection ---
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(chat_id,
            "Both parties have joined successfully!\nBuyer, please select a cryptocurrency:",
            reply_markup=create_buttons([("BTC","crypto_BTC"),("ETH","crypto_ETH"),("LTC","crypto_LTC"),("SOL","crypto_SOL")])
        )
        await context.bot.send_message(ADMIN_GROUP_ID,
            f"Escrow started in group {chat_id} with Buyer @{username} and Seller @{username}."
        )

    # --- crypto selection ---
    if data.startswith("crypto_") and user_id == escrow["buyer_id"]:
        coin = data.split("_")[1]
        escrow["crypto"] = coin
        escrow["status"] = "awaiting_amount"
        await query.message.reply_text(f"You selected {coin}. Please enter the amount in fiat (e.g. 100, £100.00):")

# ---------------- MESSAGE HANDLER ----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    escrow = escrows.get(chat_id)
    if not escrow: return

    # --- amount entry ---
    if escrow["status"] == "awaiting_amount" and user_id == escrow["buyer_id"]:
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text("Invalid amount. Please enter a numeric value.")
            return
        escrow["fiat_amount"] = amount
        coin = escrow["crypto"]
        price = get_crypto_price(coin)
        if price is None: await update.message.reply_text("Error fetching live price."); return
        crypto_amount = round(amount / price, 8)
        escrow["crypto_amount"] = crypto_amount
        escrow["wallet_address"] = ESCROW_WALLETS[coin]
        await update.message.reply_text(
            f"Please send {crypto_amount} {coin} to the escrow wallet below:\n{ESCROW_WALLETS[coin]}\n"
            f"Live rate: £{price} per {coin}"
        )

# ---------------- MAIN ----------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", start_escrow))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
