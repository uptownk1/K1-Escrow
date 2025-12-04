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
    # Mapping user-friendly symbol to CoinGecko ID
    symbol_mapping = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "LTC": "litecoin",
        "SOL": "solana"
    }

    coingecko_symbol = symbol_mapping.get(symbol.upper())
    if not coingecko_symbol:
        logging.error(f"Unsupported cryptocurrency symbol: {symbol}")
        return None

    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_symbol}&vs_currencies={FIAT_CURRENCY}"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        return data[coingecko_symbol][FIAT_CURRENCY]
    except Exception as e:
        logging.error(f"Error fetching crypto price for {symbol}: {e}")
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

    logging.info(f"Callback data received: {data} from user: {username} (ID: {user_id}) in chat: {chat_id}")

    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = create_new_escrow(chat_id)

    # Cancel Escrow
    if data == "cancel_escrow":
        if escrow:
            escrows.pop(chat_id)
        await query.message.reply_text("Escrow has been closed, use /escrow to open a new trade.")
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']} in group {chat_id} was cancelled."
        )
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"@{username} has joined as Buyer. Please wait for seller to join. Escrow Ticket: {escrow['ticket']}"
        )
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']}: Buyer @{username} joined group {chat_id}."
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"@{username} has joined as Seller. Please wait for buyer to join. Escrow Ticket: {escrow['ticket']}"
        )
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']}: Seller @{username} joined group {chat_id}."
        )

    # Both parties joined
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(
            chat_id,
            f"Buyer+Seller have joined successfully. Escrow Ticket: {escrow['ticket']}\n\n"
            "Buyer, please select the coin you would like to be held in escrow:",
            reply_markup=create_buttons([
                ("BTC", "crypto_BTC"),
                ("ETH", "crypto_ETH"),
                ("LTC", "crypto_LTC"),
                ("SOL", "crypto_SOL")
            ])
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow Ticket Number: {escrow['ticket']} started in group {chat_id} with Buyer ID {escrow['buyer_id']} "
            f"and Seller ID {escrow['seller_id']}."
        )

    # Crypto selection
    if data.startswith("crypto_") and escrow["status"] == "crypto_selection" and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"

        await query.message.reply_text(
            f"Crypto selected: {crypto}. Use /amount <number> to confirm the GBP amount."
        )

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow Ticket: {escrow['ticket']}: Buyer @{username} selected crypto {crypto}."
        )

        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))

    # Buyer Paid
    if data == "buyer_paid" and escrow["status"] == "awaiting_payment" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        await query.message.reply_text(
            "Buyer marked as paid, please wait whilst we check the transaction."
        )

        # Send ticket-tagged callback data to admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Buyer @{username} marked as paid for Escrow {escrow['ticket']}. Confirm?",
            reply_markup=create_buttons([
                ("Yes", f"payment_received_{escrow['ticket']}"),
                ("No", f"payment_not_received_{escrow['ticket']}")
            ])
        )

# --- Admin Confirms Payment ---
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # Example data: payment_received_AB12CD34
    parts = data.split("_")
    status = parts[0] + "_" + parts[1]  # payment_received OR payment_not_received
    ticket = parts[2]

    # Find escrow by ticket
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)

    if not escrow:
        logging.warning(f"No escrow found for ticket {ticket}.")
        await query.answer("Error: Escrow not found.")
        return

    payment_ok = status == "payment_received"
    escrow["buyer_confirmed"] = payment_ok
    escrow["status"] = "payment_confirmed"

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Admin confirmed payment for Ticket {ticket}: {'received' if payment_ok else 'NOT received'}."
    )

    await context.bot.send_message(
        escrow["group_id"],
        f"Admin has confirmed the payment was {'received' if payment_ok else 'NOT received'}.\n"
        f"{'Trade may proceed.' if payment_ok else 'Please resolve the issue.'}"
    )

    await query.answer("Done.")

# ---------------- MESSAGE HANDLERS ----------------

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    escrow = escrows.get(chat_id)
    if not escrow:
        await update.message.reply_text("No active escrow. Use /escrow to start.")
        return

    if escrow["status"] != "awaiting_amount" or escrow["buyer_id"] != user_id:
        await update.message.reply_text("You are not allowed to set the amount right now.")
        return

    try:
        fiat_amount = float(text.split()[1])
    except:
        await update.message.reply_text("Invalid amount. Use: /amount 50")
        return

    crypto_symbol = escrow.get("crypto")
    price = get_crypto_price(crypto_symbol)

    if not price:
        await update.message.reply_text("Error fetching crypto price. Try again.")
        return

    crypto_amount = round(fiat_amount / price, 8)

    escrow["fiat_amount"] = fiat_amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"
    wallet_address = ESCROW_WALLETS.get(crypto_symbol)

    await update.message.reply_text(
        f"Amount registered: £{fiat_amount} (~{crypto_amount} {crypto_symbol})\n\n"
        f"Send to:\n{wallet_address}\n\n"
        f"Press 'I've Paid' once sent.",
        reply_markup=create_buttons([
            ("I’ve Paid", "buyer_paid"),
            ("Cancel", "cancel_escrow")
        ])
    )

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Escrow Ticket {escrow['ticket']} awaiting payment. Buyer @{update.message.from_user.username} "
        f"£{fiat_amount} / {crypto_amount} {crypto_symbol} to {wallet_address}"
    )

# ---------------- MAIN ----------------

def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("escrow", escrow_command))
    application.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(
        CallbackQueryHandler(handle_admin_payment_confirmation,
                             pattern="payment_received_|payment_not_received_")
    )

    application.run_polling()

if __name__ == "__main__":
    main()
