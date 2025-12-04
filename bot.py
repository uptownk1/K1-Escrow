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
    "SOL": os.environ.get("SOL_WALLETS")
}

FIAT_CURRENCY = "gbp"

# ---------------- DATA ----------------
escrows = {}

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
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items])

def get_crypto_price(symbol):
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol.lower()}&vs_currencies={FIAT_CURRENCY}"
        
        # Log the request URL for debugging
        logging.info(f"Fetching price from URL: {url}")
        
        response = requests.get(url)

        # Print the raw response for debugging
        logging.info(f"API Response: {response.text}")
        
        # Parse the JSON response
        data = response.json()

        # Check if the symbol is in the response
        if symbol.lower() in data:
            return data[symbol.lower()][FIAT_CURRENCY]
        else:
            logging.error(f"Invalid cryptocurrency symbol in response: {symbol}. Response: {response.text}")
            return None
    except Exception as e:
        # Log the error with the exception details
        logging.error(f"Error fetching crypto price for {symbol}: {str(e)}")
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

    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"You have joined as Buyer. Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']}: Buyer @{username} joined group {chat_id}."
        )

    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"You have joined as Seller. Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']}: Seller @{username} joined group {chat_id}."
        )

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
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    logging.info(f"Received message: {text} from user: {user_id}")

    escrow = escrows.get(chat_id)
    if not escrow:
        logging.warning("No escrow found for this chat.")
        return
    
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
    logging.info(f"Fetching price for {crypto_symbol}.")
    crypto_price = get_crypto_price(crypto_symbol)
    if crypto_price is None:
        await update.message.reply_text("Error fetching crypto price. Try again later.")
        return

    crypto_amount = round(fiat_amount / crypto_price, 8)
    escrow["fiat_amount"] = fiat_amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"

    wallet_address = ESCROW_WALLETS[crypto_symbol]
    await update.message.reply_text(
        f"Amount {fiat_amount} GBP (~{crypto_amount} {crypto_symbol}) has been registered for this escrow.\n\n"
        "Please send the crypto to the following wallet address:\n\n"
        f"{wallet_address}\n\nOnce you have sent the payment, press 'I’ve Paid' below to confirm.",
        reply_markup=create_buttons([
            ("I’ve Paid", "buyer_paid"),
            ("Cancel", "cancel_escrow")
        ])
    )

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Escrow {escrow['ticket']} awaiting payment: Buyer @{update.message.from_user.username}, "
        f"Amount: £{fiat_amount} / {crypto_amount} {crypto_symbol}, Wallet: {wallet_address}"
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO)
    
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("escrow", escrow_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount))
    application.add_handler(CallbackQueryHandler(button_callback))

    application.run_polling()

if __name__ == "__main__":
    main()
