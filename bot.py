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
last_message_ids = {}  # key: chat_id -> last bot message id

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
        "disputed": False,
    }
    escrows[chat_id] = escrow
    return escrow

def create_escrow_buttons(escrow):
    buttons = []
    if not escrow["buyer_id"]:
        buttons.append([InlineKeyboardButton("Join as Buyer 💷", callback_data="join_buyer")])
    if not escrow["seller_id"]:
        buttons.append([InlineKeyboardButton("Join as Seller 📦", callback_data="join_seller")])
    buttons.append([InlineKeyboardButton("Cancel ❌", callback_data="cancel_escrow")])
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
        response.raise_for_status()
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
        "🤖 Welcome To K1 Escrow Bot! 🤖\n"
        "\n"
        "1) Add this bot to group with buyer/seller\n"
        "2) /escrow to start a trade\n"
    )

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id not in escrows:
        create_new_escrow(chat_id)
    escrow = escrows[chat_id]
    await update.message.reply_text(
        "Both select your role to start escrow 👇",
        reply_markup=create_escrow_buttons(escrow)
    )

# ---------------- CALLBACK HANDLER (ADMIN FIRST!) ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    # Format: payment_received_ABC12345 or payment_not_received_ABC12345
    if "_" not in data:
        return

    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        logging.warning("Invalid admin callback format")
        return

    payment_ok = status_word == "received"

    # Find escrow by ticket
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        logging.warning(f"No escrow found for ticket {ticket}")
        return

    # Remove previous dispute buttons
    if escrow["group_id"] in last_message_ids:
        await context.bot.edit_message_reply_markup(
            chat_id=escrow["group_id"],
            message_id=last_message_ids[escrow["group_id"]],
            reply_markup=None
        )

    buyer_user = await context.bot.get_chat_member(escrow["group_id"], escrow["buyer_id"])
    seller_user = await context.bot.get_chat_member(escrow["group_id"], escrow["seller_id"])
    buyer_name = f"@{buyer_user.user.username} (Buyer)"
    seller_name = f"@{seller_user.user.username} (Seller)"

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = True

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"*Status:* Payment Confirmed ✅\n"
            f"*Action:* {buyer_name} payment received\n"
            f"*Ticket:* {escrow['ticket']} 🎟️",
            parse_mode="Markdown"
        )

        # Notify trade group
        msg = await context.bot.send_message(
            escrow["group_id"],
            f"Status: Payment Received ✅\n"
            f"Buyer: {buyer_name}\n"
            f"Amount: £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"Response: Seller can now send goods/services and confirm below 👇",
            reply_markup=create_buttons([
                ("I've sent the goods/services ✅", "seller_sent_goods"),
                ("Dispute 🛑", "dispute_trade")
            ])
        )
        last_message_ids[escrow["group_id"]] = msg.message_id
    else:
        escrow["status"] = "payment_not_received"
        escrow["buyer_confirmed"] = False

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"*Status:* Payment Not Received ❌\n"
            f"*Action:* {buyer_name} payment not received\n"
            f"*Ticket:* {escrow['ticket']} 🎟️",
            parse_mode="Markdown"
        )

        msg = await context.bot.send_message(
            escrow["group_id"],
            f"Status: Payment Not Received ❌\n"
            f"Response: You will receive an update when payment is confirmed.",
            reply_markup=create_buttons([
                ("Dispute 🛑", "dispute_trade")
            ])
        )
        last_message_ids[escrow["group_id"]] = msg.message_id

# ---------------- CALLBACK HANDLER (ALL OTHER BUTTONS) ----------------
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
            f"Status: New Trade 🤝\nAction: @{username} joined as Buyer 💷\nResponse: Waiting for Seller 📦\nTicket Number: {escrow['ticket']} 🎟️"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Buyer @{username} joined escrow {escrow['ticket']}."
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"Status: New Trade 🤝\nAction: @{username} joined as Seller 📦\nResponse: Waiting for Buyer ⏳\nTicket Number: {escrow['ticket']} 🎟️"
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
            f"Status: Both Parties Joined ✅\nTicket Number: {escrow['ticket']} 🎟️\nAction: Buyer, select payment method 👇",
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

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"*Status:* Payment Method Selected 💷\n*Action:* @{username} (Buyer) selected {crypto}\n*Ticket:* {escrow['ticket']} 🎟️",
            parse_mode="Markdown"
        )

        await query.message.reply_text(
            f"Status: Payment Method 💷\nTicket Number: {escrow['ticket']} 🎟️\nAction: You selected {crypto} 🪙\nResponse: Now type the amount in GBP using /amount command ✍️\n(e.g /amount 100)"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Buyer paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        if escrow["status"] != "awaiting_payment":
            await query.answer("You cannot mark as paid now.", show_alert=True)
            return

        escrow["status"] = "awaiting_admin_confirmation"

        # Remove previous buttons
        if chat_id in last_message_ids:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=last_message_ids[chat_id],
                reply_markup=None
            )

        # New status message with dispute button only
        msg = await context.bot.send_message(
            chat_id,
            f"Status: Awaiting Payment ⏳\n"
            f"Amount: £{escrow['fiat_amount']} 💷\n"
            f"{escrow['crypto_amount']} {escrow['crypto']} 🪙\n"
            f"Response: Please wait whilst we confirm this transaction on our network...",
            reply_markup=create_buttons([
                ("Dispute 🛑", "dispute_trade")
            ])
        )
        last_message_ids[chat_id] = msg.message_id

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"*Status:* Buyer Marked Payment ✅\n"
            f"*Action:* @{username} (Buyer) marked as paid\n"
            f"*Ticket:* {escrow['ticket']} 🎟️\n"
            f"*Amount:* £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}\nPlease confirm:",
            parse_mode="Markdown",
            reply_markup=create_buttons([
                ("Yes ✅", f"payment_received_{escrow['ticket']}"),
                ("No ❌", f"payment_not_received_{escrow['ticket']}")
            ])
        )

    # Seller has sent goods
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True
        escrow["status"] = "awaiting_buyer_confirmation"

        # Remove previous buttons
        if chat_id in last_message_ids:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=last_message_ids[chat_id],
                reply_markup=None
            )

        msg = await context.bot.send_message(
            escrow["group_id"],
            "Seller has marked the goods/services as sent. Buyer, please confirm receipt below.",
            reply_markup=create_buttons([
                ("I've received the goods/services", "buyer_received_goods"),
                ("Dispute 🛑", "dispute_trade")
            ])
        )
        last_message_ids[chat_id] = msg.message_id

    # Dispute the trade
    if data == "dispute_trade":
        escrow["disputed"] = True
        escrow["status"] = "disputed"

        # Remove previous buttons
        if chat_id in last_message_ids:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=last_message_ids[chat_id],
                reply_markup=None
            )

        user_role = "Buyer" if user_id == escrow["buyer_id"] else "Seller"
        msg = await context.bot.send_message(
            chat_id,
            f"Dispute Raised - Escrow has been disputed by {user_role} (@{username}). "
            "Please add admin @uptownk1 to the group for this to be resolved manually. Funds are safe.",
            reply_markup=create_buttons([
                ("Dispute 🛑", "dispute_trade")
            ])
        )
        last_message_ids[chat_id] = msg.message_id

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"*Status:* Dispute Raised ⚠️\n"
            f"*Action:* {user_role} @{username} raised a dispute\n"
            f"*Ticket:* {escrow['ticket']} 🎟️",
            parse_mode="Markdown"
        )

# ---------------- MESSAGE HANDLER ----------------
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    escrow = escrows.get(chat_id)
    if not escrow:
        await update.message.reply_text("No active escrow. Use /escrow.")
        return

    if escrow["buyer_id"] != user_id or escrow["status"] != "awaiting_amount":
        await update.message.reply_text("You cannot set the amount now.")
        return

    try:
        amount = float(text.split()[1])
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid amount. Example: /amount 50")
        return

    crypto = escrow["crypto"]
    price = get_crypto_price(crypto)
    if price is None:
        await update.message.reply_text("Unable to fetch the price for the selected cryptocurrency. Try again later.")
        return

    crypto_amount = round(amount / price, 8)
    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"

    wallet = ESCROW_WALLETS.get(crypto)

    # Remove previous buttons
    if chat_id in last_message_ids:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=last_message_ids[chat_id],
            reply_markup=None
        )

    msg = await update.message.reply_text(
        f"Status: Buyer Deposit ⏳\nAmount: £{amount} 💷\n"
        f"{crypto_amount} {crypto} 🪙\n"
        f"Send exact amount to:\n`{wallet}`\n\n"
        "Confirm below once payment is made 👇",
        parse_mode="Markdown",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Dispute 🛑", "dispute_trade")
        ])
    )
    last_message_ids[chat_id] = msg.message_id

    # Notify admin
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"*Status:* Payment Amount Set 💷\n"
        f"*Action:* @{update.message.from_user.username} set amount £{amount} / {crypto_amount} {crypto}\n"
        f"*Ticket:* {escrow['ticket']} 🎟️",
        parse_mode="Markdown"
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    app = ApplicationBuilder().token(TOKEN).build()

    # Admin callbacks first
    app.add_handler(CallbackQueryHandler(
        handle_admin_payment_confirmation,
        pattern=r"^payment_(received|not_received)_[A-Z0-9]+$"
    ))

    # Other callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
