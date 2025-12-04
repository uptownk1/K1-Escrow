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

    # Convert symbol to lowercase and map to CoinGecko ID
    coingecko_symbol = symbol_mapping.get(symbol.upper())
    if not coingecko_symbol:
        logging.error(f"Unsupported cryptocurrency symbol: {symbol}")
        return None

    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_symbol}&vs_currencies={FIAT_CURRENCY}"
        response = requests.get(url)
        response.raise_for_status()  # Raise an error if the response code is not 2xx
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

    # --- Buyer Paid ---
    if data == "buyer_paid" and escrow["status"] == "awaiting_payment" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        await query.message.reply_text(
            "Buyer marked as paid, please wait for the admin to confirm payment."
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Buyer @{username} has marked as paid for Escrow {escrow['ticket']}. "
            "Please confirm if the payment was received.",
            reply_markup=create_buttons([("Yes", "payment_received"), ("No", "payment_not_received")])
        )


# ---------------- ADMIN RESPONSE HANDLER ----------------
async def admin_response_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    escrow = escrows.get(chat_id)

    if not escrow:
        await query.message.reply_text("No active escrow found for this group.")
        return

    # Handle payment confirmation (admin response)
    if data == "payment_received":
        escrow["status"] = "completed"
        await query.message.reply_text("Payment confirmed. Escrow is now complete.")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']} completed. Payment received."
        )
        # Notify buyer and seller
        await context.bot.send_message(escrow["buyer_id"], "Payment confirmed. Escrow is complete.")
        await context.bot.send_message(escrow["seller_id"], "Payment confirmed. Escrow is complete.")

        # Notify the escrow group
        await context.bot.send_message(escrow["group_id"], "Escrow completed. Payment received.")

    if data == "payment_not_received":
        escrow["status"] = "failed"
        await query.message.reply_text("Payment not received. Escrow has been failed.")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']} failed. Payment was not received."
        )
        # Notify buyer and seller
        await context.bot.send_message(escrow["buyer_id"], "Payment not confirmed. Escrow has failed.")
        await context.bot.send_message(escrow["seller_id"], "Payment not confirmed. Escrow has failed.")

        # Notify the escrow group
        await context.bot.send_message(escrow["group_id"], "Escrow failed. Payment not received.")


# ---------------- MAIN ----------------

def main():
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("escrow", escrow_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(CallbackQueryHandler(admin_response_handler, pattern="payment_received"))
    application.add_handler(CallbackQueryHandler(admin_response_handler, pattern="payment_not_received"))

    application.run_polling()

if __name__ == "__main__":
    main()
