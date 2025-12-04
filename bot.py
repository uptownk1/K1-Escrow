import os
import logging
from uuid import uuid4
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
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
    }
    escrows[chat_id] = escrow
    return escrow

def create_escrow_buttons(escrow):
    buttons = []
    if not escrow["buyer_id"]:
        buttons.append([InlineKeyboardButton("Join as Buyer", callback_data="join_buyer")])
    if not escrow["seller_id"]:
        buttons.append([InlineKeyboardButton("Join as Seller", callback_data="join_seller")])
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel_escrow")])
    return InlineKeyboardMarkup(buttons)

def create_buttons(items):
    """Generic button creator: items = list of tuples (text, callback_data)"""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items])

def get_crypto_price(symbol):
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol.lower()}&vs_currencies={FIAT_CURRENCY}"
        response = requests.get(url)
        data = response.json()
        return data[symbol.lower()][FIAT_CURRENCY]
    except:
        return None

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
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"You have joined as Buyer. Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']}: Buyer @{username} joined group {chat_id}."
        )

    # --- Join Seller ---
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"You have joined as Seller. Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']}: Seller @{username} joined group {chat_id}."
        )

    # --- Both parties joined ---
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(
            chat_id,
            f"Both parties have joined successfully! Escrow Ticket: {escrow['ticket']}\n\n"
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
            f"Escrow {escrow['ticket']} started in group {chat_id} with Buyer ID {escrow['buyer_id']} "
            f"and Seller ID {escrow['seller_id']}."
        )

    # --- Crypto selection ---
    if data.startswith("crypto_") and escrow["status"] == "crypto_selection" and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"
        await query.message.reply_text(
            f"Crypto selected: {crypto}. Please use the /amount <amount> command to specify the GBP amount you want to pay."
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']}: Buyer @{username} selected crypto {crypto}."
        )

# ---------------- MESSAGE HANDLERS ----------------

# General message handler to capture all messages (with logging)
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    logging.info(f"Received message: {text} from user: {user_id} in chat: {chat_id}")

    # Check if it's from a group chat
    if update.message.chat.type in ['group', 'supergroup']:
        logging.info(f"Received message from group: {update.message.chat.title}")

    # Check if buyer is in 'awaiting_amount' state and in correct group
    escrow = escrows.get(update.message.chat_id)
    if escrow and escrow['status'] == 'awaiting_amount' and update.message.from_user.id == escrow['buyer_id']:
        logging.info(f"Buyer entered amount: {update.message.text}")
        # Now we will handle the amount logic
        await handle_amount(update, context)


# Handle the /amount command message
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    logging.info(f"Handling amount: {text} from user: {user_id} in chat: {chat_id}")

    # Ensure that escrow exists
    escrow = escrows.get(chat_id)
    if not escrow:
        logging.warning("No escrow found for this chat.")
        return
    
    # Ensure we're in the correct state
    if escrow["status"] != "awaiting_amount" or user_id != escrow["buyer_id"]:
        logging.warning(f"Status is not 'awaiting_amount' or user is not the buyer.")
        return

    if not text.startswith("/amount"):
        logging.warning(f"Message does not start with '/amount': {text}")
        return

    amount_text = text.split()[1].strip()
    try:
        fiat_amount = float(amount_text.replace("£", "").replace(",", ""))
    except ValueError:
        await update.message.reply_text(
            "Invalid amount format. Please enter a valid GBP amount after the /amount command (e.g., /amount 1000)."
        )
        return

    crypto_symbol = escrow["crypto"]
    crypto_price = get_crypto_price(crypto_symbol)
    if crypto_price is None:
        await update.message.reply_text("Error fetching crypto price. Try again later.")
        return

    crypto_amount = round(fiat_amount / crypto_price, 8)
    escrow["fiat_amount"] = fiat_amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"

    wallet_address = ESCROW_WALLETS[crypto_symbol]
    escrow["wallet_address"] = wallet_address

    await update.message.reply_text(
        f"Amount confirmed: £{fiat_amount:.2f}\n"
        f"Your wallet address for {crypto_symbol} ({crypto_amount} {crypto_symbol}): {wallet_address}\n\n"
        "Please send the specified amount to the wallet address."
    )

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Escrow {escrow['ticket']} amount confirmed: £{fiat_amount} for crypto {crypto_symbol}. "
        f"Buyer: {user_id} ({update.message.from_user.username or update.message.from_user.first_name}) "
        f"has provided amount {fiat_amount}."
    )


# ---------------- MAIN ----------------
if __name__ == '__main__':
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        level=logging.INFO)
    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("escrow", escrow_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT, handle_all_messages))

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)
