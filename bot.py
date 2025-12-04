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
        "goods_sent": False,
        "goods_received": False,
        "disputed": False
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
    symbol_mapping = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "LTC": "litecoin",
        "SOL": "solana"
    }
    coingecko_symbol = symbol_mapping.get(symbol.upper())
    if not coingecko_symbol:
        return None
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_symbol}&vs_currencies={FIAT_CURRENCY}"
        response = requests.get(url)
        response.raise_for_status()  # Raise an HTTPError for bad responses
        data = response.json()
        price = data.get(coingecko_symbol, {}).get(FIAT_CURRENCY)
        if price is None:
            logging.error(f"Price for {symbol} not found in the response.")
            return None
        return price
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching crypto price for {symbol}: {e}")
        return None
    except ValueError:
        logging.error(f"Error parsing response for {symbol}")
        return None

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Thank you for using K1 Escrow Bot!\n"
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

# ---------------- CALLBACK HANDLER (ADMIN FIRST!) ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    logging.info(f"[ADMIN HANDLER TRIGGERED] Callback data: {data}")
    await query.answer()

    if "_" not in data:
        return

    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        logging.warning("Invalid admin callback format")
        return

    payment_ok = status_word == "received"

    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        logging.warning(f"No escrow found for ticket {ticket}")
        return

    escrow["status"] = "payment_confirmed"
    escrow["buyer_confirmed"] = payment_ok

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Admin confirmed payment for Escrow {ticket}: {'RECEIVED' if payment_ok else 'NOT RECEIVED'}."
    )

    await context.bot.send_message(
        escrow["group_id"],
        f"Admin has confirmed that the payment was "
        f"{'received ✔️' if payment_ok else 'NOT received ❌'}.\n\n"
        f"{'This payment has been confirmed and is currently held safely in escrow. Seller can now send the buyer the goods/services, and press below to confirm when done.' if payment_ok else 'Please resolve the payment issue.'}",
        reply_markup=create_buttons([("I've sent the goods/services", "seller_sent_goods")])
    )

# ---------------- CALLBACK HANDLER (ALL OTHER BUTTONS) ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data

    logging.info(f"[MAIN HANDLER] Callback data: {data} from {username} in {chat_id}")

    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = create_new_escrow(chat_id)

    # Cancel Escrow
    if data == "cancel_escrow":
        escrows.pop(chat_id, None)
        await query.message.reply_text("Escrow cancelled. Use /escrow to start again.")
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']} was cancelled in group {chat_id}."
        )
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"@{username} has joined as Buyer.\nWaiting for Seller.\nTicket: {escrow['ticket']}"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Buyer @{username} joined escrow {escrow['ticket']}."
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"@{username} has joined as Seller.\nWaiting for Buyer.\nTicket: {escrow['ticket']}"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Seller @{username} joined escrow {escrow['ticket']}."
        )

    # Both joined
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(
            chat_id,
            "Both parties joined. Buyer, select the crypto:",
            reply_markup=create_buttons([
                ("BTC", "crypto_BTC"),
                ("ETH", "crypto_ETH"),
                ("LTC", "crypto_LTC"),
                ("SOL", "crypto_SOL")
            ])
        )

    # Crypto selection
    if data.startswith("crypto_") and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"

        await query.message.reply_text(
            f"You selected {crypto}. Now send the amount in GBP using /amount <number>."
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Buyer paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"

        await query.message.reply_text("Payment marked as sent. Waiting for admin confirmation...")

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Buyer @{username} marked payment sent for Escrow {escrow['ticket']}.\nConfirm?",
            reply_markup=create_buttons([
                ("Yes", f"payment_received_{escrow['ticket']}"),
                ("No", f"payment_not_received_{escrow['ticket']}")
            ])
        )

    # Seller sent goods/services
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True
        await context.bot.send_message(
            chat_id,
            "Buyer please confirm that you've received the goods/services and you're happy for payment to be released. If you're not happy, dispute this trade below.",
            reply_markup=create_buttons([("I've received the goods/services", "buyer_received_goods")])
        )

# ---------------- HANDLE WALLET ADDRESS ----------------
async def handle_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    escrow = escrows.get(chat_id)
    if not escrow:
        await update.message.reply_text("No active escrow. Use /escrow.")
        return

    if escrow["seller_id"] != user_id or not escrow["goods_sent"]:
        await update.message.reply_text("You cannot provide the wallet address yet.")
        return

    # Validate wallet address format (you can use a more specific format check if necessary)
    escrow["wallet_address"] = text
    escrow["status"] = "awaiting_payment_release"

    await update.message.reply_text(
        "We are now releasing the payment to the seller's wallet. Please wait, you will receive confirmation once payment's been sent."
    )

    # Send wallet details to admin group for review
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Escrow {escrow['ticket']} - {escrow['crypto_amount']} {escrow['crypto']} will be released to the following address:\n{escrow['wallet_address']}",
        reply_markup=create_buttons([("Payment Released", f"payment_released_{escrow['ticket']}")])
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    app = ApplicationBuilder().token(TOKEN).build()

    # 🔥 ORDER MATTERS — ADMIN FIRST!!!
    app.add_handler(CallbackQueryHandler(
        handle_admin_payment_confirmation,
        pattern=r"^payment_(received|not_received)_[A-Z0-9]+$"
    ))

    # Then all other callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(MessageHandler(filters.TEXT, handle_wallet_address))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
