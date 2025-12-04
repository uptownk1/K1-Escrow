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

    if data == "cancel_escrow":
        if escrow:
            escrows.pop(chat_id)
        await query.message.reply_text(
            "Escrow has been closed, use /escrow to open a new trade."
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']} in group {chat_id} was cancelled."
        )
        return

    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"@{username} has joined as Buyer. Please wait for seller to join. Escrow Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']}: Buyer @{username} joined group {chat_id}."
        )

    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"@{username} has joined as Seller. Please wait for buyer to join. Escrow Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']}: Seller @{username} joined group {chat_id}."
        )

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

    if data.startswith("crypto_") and escrow["status"] == "crypto_selection" and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"
        logging.info(f"Buyer: {username} selected {crypto}. Escrow Ticket: {escrow['ticket']}.")

        await query.message.reply_text(
            f"Crypto selected: {crypto}. Please use the /amount feature to confirm the amount you're sending in pounds. e.g /amount 500. Please do not put the £ symbol just type the number"
        )

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow Ticket: {escrow['ticket']}: Buyer @{username} selected crypto {crypto}."
        )

        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))

    if data == "buyer_paid" and escrow["status"] == "awaiting_payment" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        await query.message.reply_text(
            "Buyer marked as paid, please wait whilst we check the transaction on our network."
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Buyer @{username} has marked as paid for Escrow {escrow['ticket']}. "
            "Please confirm if the payment was received.",
            reply_markup=create_buttons([("Yes", f"payment_received_{escrow['ticket']}"), ("No", f"payment_not_received_{escrow['ticket']}")])
        )

# ---------------- ADMIN PAYMENT CONFIRMATION ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    logging.info(f"[ADMIN HANDLER] Callback data: {data}")

    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        logging.warning("Invalid admin callback format")
        return

    # Find escrow by ticket
    escrow = next((e for e in escrows.values() if e['ticket'] == ticket), None)
    if not escrow:
        logging.warning(f"No escrow found for ticket {ticket}")
        return

    escrow["buyer_confirmed"] = status_word == "received"
    escrow["status"] = "payment_confirmed" if escrow["buyer_confirmed"] else "awaiting_payment"

    # Notify admin group
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Admin confirmed payment status for Escrow Ticket {ticket}: {status_word}."
    )

    if escrow["buyer_confirmed"]:
        # Notify seller to send goods/services
        escrow["status"] = "awaiting_seller_to_send"
        seller_id = escrow["seller_id"]
        await context.bot.send_message(
            seller_id,
            "The payment has been received and is safely held in escrow. "
            "Please now send the buyer their goods/services. "
            "Once done, press 'I've sent goods/services' below.",
            reply_markup=create_buttons([("I've sent goods/services", f"seller_sent_goods_{ticket}")])
        )
        # Notify group
        await context.bot.send_message(
            escrow["group_id"],
            "Admin has confirmed the payment has been received. Seller will now send the goods/services."
        )
    else:
        await context.bot.send_message(
            escrow["group_id"],
            "Admin did not confirm the payment as received. Please resolve the issue."
        )

# ---------------- SELLER & BUYER CONFIRMATION ----------------
async def seller_sent_goods(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    ticket = data.split("_")[-1]

    escrow = next((e for e in escrows.values() if e['ticket'] == ticket), None)
    if not escrow:
        await query.message.reply_text("Escrow not found.")
        return

    escrow["status"] = "awaiting_buyer_confirmation"
    buyer_id = escrow["buyer_id"]
    await context.bot.send_message(
        buyer_id,
        "The seller has marked goods/services as sent. Please confirm receipt or open a dispute.",
        reply_markup=create_buttons([
            ("I've received goods/services", f"buyer_received_goods_{ticket}"),
            ("Dispute", f"open_dispute_{ticket}")
        ])
    )
    await query.message.reply_text("Marked as sent. Buyer has been notified.")

async def buyer_goods_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    ticket = data.split("_")[-1]

    escrow = next((e for e in escrows.values() if e['ticket'] == ticket), None)
    if not escrow:
        await query.message.reply_text("Escrow not found.")
        return

    if data.startswith("buyer_received_goods"):
        escrow["status"] = "completed"
        await context.bot.send_message(
            escrow["group_id"],
            "Buyer confirmed receipt. Escrow completed successfully!"
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow Ticket {ticket} completed successfully."
        )
    elif data.startswith("open_dispute"):
        escrow["status"] = "dispute_open"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow Ticket {ticket} has a dispute opened by the buyer."
        )
        await context.bot.send_message(
            escrow["group_id"],
            "A dispute has been opened. Admins have been notified."
        )

# ---------------- MESSAGE HANDLERS ----------------
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    logging.info(f"Received message: {text} from user: {user_id}")
    escrow = escrows.get(chat_id)
    if not escrow:
        await update.message.reply_text("No active escrow found. Please use /escrow to start a new trade.")
        return

    if escrow["status"] != "awaiting_amount" or escrow["buyer_id"] != user_id:
        await update.message.reply_text("Please make sure you are the buyer and in the correct state.")
        return

    try:
        fiat_amount = float(text.split()[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Please enter a valid amount after the /amount command (e.g., /amount 50).")
        return

    crypto_symbol = escrow.get("crypto")
    if not crypto_symbol:
        await update.message.reply_text("No cryptocurrency selected. Please select one first.")
        return

    price = get_crypto_price(crypto_symbol)
    if not price:
        await update.message.reply_text("Error fetching crypto price. Please use /escrow to cancel trade and try again.")
        return

    crypto_amount = round(fiat_amount / price, 8)
    escrow["fiat_amount"] = fiat_amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"
    wallet_address = ESCROW_WALLETS.get(crypto_symbol)
    if not wallet_address:
        await update.message.reply_text(f"Sorry, we do not have a wallet address for {crypto_symbol}.")
        return

    await update.message.reply_text(
        f"Amount: £{fiat_amount} (~{crypto_amount} {crypto_symbol}) has been registered for this escrow.\n\n"
        f"Please send the exact amount to the escrow wallet address:\n\n"
        f"{wallet_address}\n\nOnce you have sent the payment, press 'I’ve Paid' below to confirm.",
        reply_markup=create_buttons([
            ("I’ve Paid", "buyer_paid"),
            ("Cancel", "cancel_escrow")
        ])
    )

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Escrow Ticket: {escrow['ticket']} Status: awaiting payment Buyer @{update.message.from_user.username}, "
        f"Amount: £{fiat_amount} / {crypto_amount} {crypto_symbol}, Depositing To Wallet: {wallet_address}"
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("escrow", escrow_command))
    application.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    # Admin payment handler FIRST
    application.add_handler(CallbackQueryHandler(
        handle_admin_payment_confirmation,
        pattern=r"^payment_(received|not_received)_[A-Z0-9]+$"
    ))

    # Seller and buyer confirmation handlers
    application.add_handler(CallbackQueryHandler(
        seller_sent_goods,
        pattern=r"^seller_sent_goods_[A-Z0-9]+$"
    ))
    application.add_handler(CallbackQueryHandler(
        buyer_goods_confirmation,
        pattern=r"^(buyer_received_goods|open_dispute)_[A-Z0-9]+$"
    ))

    # General buttons
    application.add_handler(CallbackQueryHandler(button_callback))

    application.run_polling()

if __name__ == "__main__":
    main()
