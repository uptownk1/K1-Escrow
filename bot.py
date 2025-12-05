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
last_message_ids = {}  # Keep track of last bot message per chat for dispute button management

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

# ---------------- CALLBACK HANDLER ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    # Format: payment_received_ABC12345 or payment_not_received_ABC12345
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

    buyer_username = (await context.bot.get_chat_member(escrow['group_id'], escrow['buyer_id'])).user.username
    seller_username = (await context.bot.get_chat_member(escrow['group_id'], escrow['seller_id'])).user.username

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = True

        # Notify admin
        admin_msg = (
            f"*Status:* Payment Confirmed ✅\n"
            f"*Action:* @{buyer_username} (Buyer) payment received\n"
            f"*Ticket:* {escrow['ticket']} 🎟️\n"
            f"*Amount:* £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}"
        )
        await context.bot.send_message(ADMIN_GROUP_ID, admin_msg, parse_mode="Markdown")

        # Notify trade group
        await context.bot.send_message(
            escrow['group_id'],
            f"Status: Payment Received ✅\nTicket: {escrow['ticket']} 🎟️\nSeller can now send goods/services 👇",
            reply_markup=create_buttons([
                ("I've sent the goods/services ✅", "seller_sent_goods")
            ])
        )
    else:
        escrow["status"] = "payment_not_received"
        escrow["buyer_confirmed"] = False

        admin_msg = (
            f"*Status:* Payment Not Confirmed ❌\n"
            f"*Action:* @{buyer_username} (Buyer) payment NOT received\n"
            f"*Ticket:* {escrow['ticket']} 🎟️"
        )
        await context.bot.send_message(ADMIN_GROUP_ID, admin_msg, parse_mode="Markdown")

        await context.bot.send_message(
            escrow['group_id'],
            "Payment has not yet been received. You will receive an update when confirmed.",
            reply_markup=create_buttons([
                ("Dispute 🛑", "dispute_trade")
            ])
        )

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
            ADMIN_GROUP_ID, f"*Status:* Escrow Cancelled ❌\n*Action:* Escrow {escrow['ticket']} was cancelled in group {chat_id}\n*Ticket:* {escrow['ticket']}",
            parse_mode="Markdown"
        )
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"Status: New Trade 🤝\nAction: @{username} joined as Buyer 💷\nTicket: {escrow['ticket']} 🎟️"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"*Status:* Buyer Joined 💷\n*Action:* @{username} (Buyer) joined\n*Ticket:* {escrow['ticket']}",
            parse_mode="Markdown"
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"Status: New Trade 🤝\nAction: @{username} joined as Seller 📦\nTicket: {escrow['ticket']} 🎟️"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"*Status:* Seller Joined 📦\n*Action:* @{username} (Seller) joined\n*Ticket:* {escrow['ticket']}",
            parse_mode="Markdown"
        )

    # Both joined -> crypto selection
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        msg = await context.bot.send_message(
            chat_id,
            f"Status: Both Parties Joined ✅\nTicket: {escrow['ticket']} 🎟️\nAction: Buyer, select payment method 👇",
            reply_markup=create_buttons([
                ("BTC", "crypto_BTC"),
                ("ETH", "crypto_ETH"),
                ("LTC", "crypto_LTC"),
                ("SOL", "crypto_SOL")
            ])
        )
        last_message_ids[chat_id] = msg.message_id

    # Crypto selection
    if data.startswith("crypto_") and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"

        await query.message.reply_text(
            f"Status: Payment Method 💷\nTicket: {escrow['ticket']} 🎟️\nAction: You selected {crypto} 🪙\nResponse: Now type the amount in GBP using /amount command ✍️\n(e.g /amount 100)"
        )

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"*Status:* Crypto Selected 🪙\n*Action:* @{username} (Buyer) selected {crypto}\n*Ticket:* {escrow['ticket']}",
            parse_mode="Markdown"
        )

    # Buyer Paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        if escrow["status"] != "awaiting_payment":
            await query.answer("You cannot mark as paid now.", show_alert=True)
            return

        escrow["status"] = "awaiting_admin_confirmation"

        # Remove Cancel buttons and only keep Dispute
        await query.message.edit_reply_markup(create_buttons([
            ("Dispute 🛑", "dispute_trade")
        ]))

        admin_msg = (
            f"*Status:* Buyer Marked Payment ✅\n"
            f"*Action:* @{username} (Buyer) marked as paid\n"
            f"*Ticket:* {escrow['ticket']} 🎟️\n"
            f"*Amount:* £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"Please confirm:"
        )

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            admin_msg,
            parse_mode="Markdown",
            reply_markup=create_buttons([
                ("Yes ✅", f"payment_received_{escrow['ticket']}"),
                ("No ❌", f"payment_not_received_{escrow['ticket']}")
            ])
        )

    # Seller sent goods
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True
        escrow["status"] = "awaiting_buyer_confirmation"

        await query.message.reply_text("Seller has marked the goods/services as sent.")
        msg = await context.bot.send_message(
            escrow["group_id"],
            "Buyer, please confirm that you've received the goods/services. If not, dispute below.",
            reply_markup=create_buttons([
                ("I've received the goods/services ✅", "buyer_received_goods"),
                ("Dispute 🛑", "dispute_trade")
            ])
        )
        last_message_ids[chat_id] = msg.message_id

    # Dispute
    if data == "dispute_trade":
        escrow["disputed"] = True
        escrow["status"] = f"Dispute Raised - Escrow has been disputed by {'Buyer' if user_id == escrow['buyer_id'] else 'Seller'} (@{username}). Please add admin @uptownk1 to the group for this to be resolved manually. Funds are safe."

        # Remove previous dispute buttons
        if chat_id in last_message_ids:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=last_message_ids[chat_id], reply_markup=None)

        msg = await context.bot.send_message(
            chat_id,
            escrow["status"]
        )
        last_message_ids[chat_id] = msg.message_id

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"*Status:* Trade Disputed 🛑\n*Action:* @{username} ({'Buyer' if user_id == escrow['buyer_id'] else 'Seller'}) raised dispute\n*Ticket:* {escrow['ticket']}",
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

    msg = await update.message.reply_text(
        f"Status: Buyer Deposit ⏳\nAmount: £{amount} 💷\n{crypto} Amount: {crypto_amount} {crypto} 🪙\n"
        f"Send exact amount to:\n`{wallet}`\n\n"
        "Confirm below once payment is made 👇",
        parse_mode="Markdown",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow")
        ])
    )
    last_message_ids[chat_id] = msg.message_id

    # Notify admin
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"*Status:* Amount Set 💷\n*Action:* @{update.message.from_user.username} (Buyer) set amount £{amount} for {crypto} ({crypto_amount} {crypto})\n*Ticket:* {escrow['ticket']}",
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

    # All other callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
