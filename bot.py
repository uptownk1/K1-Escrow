import logging
import re
import asyncio
from uuid import uuid4
from datetime import datetime, timedelta

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ---------------- CONFIG ----------------
TOKEN = "YOUR_BOT_TOKEN"
ADMIN_GROUP_ID = -1001234567890  # replace with your admin group ID
ESCROW_WALLETS = {
    "BTC": "your_btc_wallet_address",
    "ETH": "your_eth_wallet_address",
    "LTC": "your_ltc_wallet_address",
    "SOL": "your_sol_wallet_address"
}
FIAT_CURRENCY = "gbp"  # can change to "usd"

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- ESCROW TRACKING ----------------
escrows = {}  # escrow_id : dict

# ---------------- HELPERS ----------------
def parse_amount(text: str):
    # Remove currency symbols and commas
    clean = re.sub(r"[^\d.]", "", text)
    try:
        return float(clean)
    except:
        return None

def get_crypto_price(coin: str):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin.lower()}&vs_currencies={FIAT_CURRENCY}"
    try:
        r = requests.get(url).json()
        price = r[coin.lower()][FIAT_CURRENCY]
        return price
    except Exception as e:
        logger.error(f"Error fetching price: {e}")
        return None

def create_buttons(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data)] for text, data in buttons])

def generate_ticket():
    return str(uuid4())[:8]

# ---------------- COMMANDS ----------------
async def start_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = create_buttons([("Join as Buyer", "join_buyer"), ("Join as Seller", "join_seller")])
    await update.message.reply_text("Select your role to start escrow:", reply_markup=buttons)

# ---------------- CALLBACK HANDLERS ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data

    # Track escrow_id per chat
    chat_id = query.message.chat_id
    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = {
            "group_id": chat_id,
            "buyer_id": None,
            "seller_id": None,
            "status": None,
            "crypto": None,
            "fiat_amount": None,
            "crypto_amount": None,
            "wallet_address": None,
            "ticket_number": None,
            "buyer_confirmed": False,
            "seller_confirmed": False,
            "timeout_task": None
        }
        escrows[chat_id] = escrow

    # ---------------- JOIN HANDLERS ----------------
    if data == "join_buyer":
        if escrow["buyer_id"]:
            await query.message.reply_text("Buyer already joined.")
            return
        escrow["buyer_id"] = user_id
        await query.message.reply_text("You joined as Buyer.")
        # Notify admin
        await context.bot.send_message(ADMIN_GROUP_ID, f"Buyer @{username} joined group {chat_id}.")
    elif data == "join_seller":
        if escrow["seller_id"]:
            await query.message.reply_text("Seller already joined.")
            return
        escrow["seller_id"] = user_id
        await query.message.reply_text("You joined as Seller.")
        # Notify admin
        await context.bot.send_message(ADMIN_GROUP_ID, f"Seller @{username} joined group {chat_id}.")
    else:
        # Proceed if both joined
        pass

    # Check if both joined
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(chat_id, "Both parties have joined successfully!\nBuyer, please select a cryptocurrency:",
            reply_markup=create_buttons([("BTC","crypto_BTC"),("ETH","crypto_ETH"),("LTC","crypto_LTC"),("SOL","crypto_SOL")]))
        await context.bot.send_message(ADMIN_GROUP_ID, f"Escrow started in group {chat_id} with Buyer @{username} and Seller @{username}.")

    # ---------------- CRYPTO SELECTION ----------------
    if data.startswith("crypto_"):
        if user_id != escrow["buyer_id"]:
            await query.message.reply_text("Only the buyer can select the crypto.")
            return
        coin = data.split("_")[1]
        escrow["crypto"] = coin
        escrow["status"] = "awaiting_amount"
        await query.message.reply_text(f"You selected {coin}. Please enter the amount in fiat (e.g. 100, £100.00):")

    # ---------------- PAYMENT BUTTONS ----------------
    if data == "i_paid":
        if user_id != escrow["buyer_id"]:
            await query.message.reply_text("Only buyer can mark as paid.")
            return
        escrow["status"] = "awaiting_admin_confirmation"
        await query.message.reply_text("Thank you, please wait whilst we check the transaction on our server...")
        # Send message to admin
        buyer_name = query.from_user.username or query.from_user.first_name
        seller_id = escrow["seller_id"]
        seller_name = "Seller"
        await context.bot.send_message(ADMIN_GROUP_ID, f"Buyer @{buyer_name} claims to have paid {escrow['fiat_amount']} {FIAT_CURRENCY} in {escrow['crypto']} for escrow in group {chat_id}.\nHas funds been received? Reply Yes/No.")

    if data == "cancel_trade":
        if user_id != escrow["buyer_id"]:
            await query.message.reply_text("Only buyer can cancel trade.")
            return
        escrow["status"] = "cancelled"
        await query.message.reply_text("Escrow has been cancelled.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"Escrow in group {chat_id} has been cancelled by buyer.")

    if data == "dispute":
        ticket = generate_ticket()
        escrow["ticket_number"] = ticket
        await query.message.reply_text(f"You have raised a dispute. Ticket #{ticket}\nPlease add the admin to the group to resolve the issue.")

# ---------------- MESSAGE HANDLER ----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text
    escrow = escrows.get(chat_id)
    if not escrow:
        return

    # ---------------- AMOUNT ENTRY ----------------
    if escrow["status"] == "awaiting_amount" and user_id == escrow["buyer_id"]:
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text("Invalid amount. Please enter a numeric value (e.g. 100, £100.00).")
            return
        escrow["fiat_amount"] = amount
        # Fetch crypto price
        coin = escrow["crypto"]
        price = get_crypto_price(coin)
        if price is None:
            await update.message.reply_text("Error fetching live price. Try again later.")
            return
        crypto_amount = round(amount / price, 8)
        escrow["crypto_amount"] = crypto_amount
        escrow["wallet_address"] = ESCROW_WALLETS[coin]
        await update.message.reply_text(
            f"Please send {crypto_amount} {coin} to the escrow wallet below:\n{ESCROW_WALLETS[coin]}\n"
            f"Live rate: £{price} per {coin}",
            reply_markup=create_buttons([("I have paid","i_paid"),("Cancel","cancel_trade"),("Dispute","dispute")])
        )

# ---------------- MAIN ----------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("escrow", start_escrow))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
