# bot.py
import os
import re
import asyncio
from uuid import uuid4
from datetime import datetime, timedelta
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
TOKEN = os.environ.get("TOKEN")
ADMIN_GROUP_ID = os.environ.get("ADMIN_GROUP_ID")
if ADMIN_GROUP_ID is None:
    print("ADMIN_GROUP_ID not set! Exiting.")
    exit(1)
ADMIN_GROUP_ID = int(ADMIN_GROUP_ID)

ESCROW_WALLETS = {
    "BTC": os.environ.get("BTC_WALLET"),
    "ETH": os.environ.get("ETH_WALLET"),
    "LTC": os.environ.get("LTC_WALLET"),
    "SOL": os.environ.get("SOL_WALLET")
}

FIAT_CURRENCY = "gbp"

# ---------------- DATA ----------------
escrows = {}  # key: chat_id -> escrow dict

# ---------------- HELPERS ----------------
def create_new_escrow(chat_id):
    ticket = str(uuid4())[:8].upper()
    escrow = {
        "group_id": chat_id,
        "buyer_id": None,
        "seller_id": None,
        "status": None,
        "crypto": None,
        "fiat_amount": None,
        "crypto_amount": None,
        "wallet_address": None,
        "ticket": ticket,
        "buyer_confirmed": False,
        "seller_confirmed": False,
        "dispute": False,
        "auto_dispute_task": None,
    }
    escrows[chat_id] = escrow
    return escrow

def create_escrow_buttons(escrow):
    buttons = []
    if escrow["buyer_id"] is None:
        buttons.append([InlineKeyboardButton("Join as Buyer", callback_data="join_buyer")])
    if escrow["seller_id"] is None:
        buttons.append([InlineKeyboardButton("Join as Seller", callback_data="join_seller")])
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel_escrow")])
    return InlineKeyboardMarkup(buttons)

def create_buttons(items):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items])

def parse_amount(amount_text):
    clean = re.sub(r"[£,]", "", amount_text)
    try:
        return float(clean)
    except:
        return None

def get_crypto_price(crypto_symbol):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_symbol.lower()}&vs_currencies={FIAT_CURRENCY}"
    try:
        data = requests.get(url).json()
        price = data[crypto_symbol.lower()][FIAT_CURRENCY]
        return price
    except:
        return None

async def auto_dispute_check(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    escrow = escrows.get(chat_id)
    if escrow and escrow["seller_confirmed"] and not escrow["buyer_confirmed"]:
        escrow["dispute"] = True
        await context.bot.send_message(
            chat_id,
            f"⚠️ Buyer did not confirm receipt within 15 minutes. Dispute opened. Ticket: {escrow['ticket']}"
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']} auto-dispute triggered in group {chat_id}."
        )

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Thank you for using K1 Escrow Bot!\n\n"
        "Add this bot into a group with the Buyer and Seller, then type /escrow to start a trade."
    )

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id not in escrows:
        create_new_escrow(chat_id)
    escrow = escrows[chat_id]
    await update.message.reply_text(
        "Select your role to start escrow:",
        reply_markup=create_escrow_buttons(escrow)
    )

# ---------------- CALLBACKS ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data

    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = create_new_escrow(chat_id)

    # --- Cancel Escrow ---
    if data == "cancel_escrow":
        if escrow:
            escrows.pop(chat_id)
        await query.message.reply_text(
            "Escrow has been closed, use /escrow to open a new trade."
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']} in group {chat_id} was cancelled."
        )
        return

    # --- Join Buyer ---
    if data == "join_buyer" and escrow["buyer_id"] is None:
        escrow["buyer_id"] = user_id
        other_msg = ""
        if escrow["seller_id"]:
            seller_username = (await context.bot.get_chat_member(chat_id, escrow["seller_id"])).user.username
            other_msg = f"@{seller_username} joined as Seller.\n"
        await query.message.reply_text(
            f"You have joined as Buyer.\n{other_msg}Please wait for the Seller to join escrow."
        )
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']}: Buyer @{username} joined group {chat_id}."
        )

    # --- Join Seller ---
    if data == "join_seller" and escrow["seller_id"] is None:
        escrow["seller_id"] = user_id
        other_msg = ""
        if escrow["buyer_id"]:
            buyer_username = (await context.bot.get_chat_member(chat_id, escrow["buyer_id"])).user.username
            other_msg = f"@{buyer_username} joined as Buyer.\n"
        await query.message.reply_text(
            f"You have joined as Seller.\n{other_msg}Please wait for the Buyer to join escrow."
        )
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']}: Seller @{username} joined group {chat_id}."
        )

    # --- Both parties joined ---
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        buyer_username = (await context.bot.get_chat_member(chat_id, escrow["buyer_id"])).user.username
        seller_username = (await context.bot.get_chat_member(chat_id, escrow["seller_id"])).user.username
        await context.bot.send_message(
            chat_id,
            f"Both parties have joined successfully! Escrow Ticket: {escrow['ticket']}\n\n"
            f"Buyer: @{buyer_username}\nSeller: @{seller_username}\n\n"
            "Buyer, please select a cryptocurrency:",
            reply_markup=create_buttons([
                ("BTC","crypto_BTC"),
                ("ETH","crypto_ETH"),
                ("LTC","crypto_LTC"),
                ("SOL","crypto_SOL")
            ])
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']} started in group {chat_id} with Buyer @{buyer_username} and Seller @{seller_username}."
        )

    # --- Buyer selects crypto ---
    if data.startswith("crypto_") and escrow["status"] == "crypto_selection":
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"
        await context.bot.send_message(
            chat_id,
            f"Buyer selected {crypto}. Please enter the amount in GBP or crypto (e.g. £100, 100, 0.005 {crypto}):"
        )

# ---------------- AMOUNT INPUT ----------------
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    escrow = escrows.get(chat_id)
    if not escrow or escrow["status"] != "awaiting_amount":
        return
    if user_id != escrow["buyer_id"]:
        return

    amount_text = update.message.text
    fiat_amount = parse_amount(amount_text)
    if fiat_amount is None:
        await update.message.reply_text("Invalid amount format. Please try again.")
        return

    crypto_price = get_crypto_price(escrow["crypto"])
    if crypto_price is None:
        await update.message.reply_text("Error fetching crypto price. Try again later.")
        return

    crypto_amount = round(fiat_amount / crypto_price, 8)
    escrow["fiat_amount"] = fiat_amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"

    wallet_address = ESCROW_WALLETS[escrow["crypto"]]
    await update.message.reply_text(
        f"Please send {crypto_amount} {escrow['crypto']} (~£{fiat_amount}) to the following escrow wallet:\n\n{wallet_address}\n\n"
        "Once you’ve sent the funds, press 'I’ve Paid' below.",
        reply_markup=create_buttons([
            ("I’ve Paid", "buyer_paid"),
            ("Cancel", "cancel_escrow")
        ])
    )

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Escrow {escrow['ticket']} awaiting payment: Buyer @{update.message.from_user.username}, "
        f"Amount: £{fiat_amount} / {crypto_amount} {escrow['crypto']}, Crypto: {escrow['crypto']}"
    )

# ---------------- MAIN ----------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_amount))

    print("Bot is running...")
    app.run_polling()
