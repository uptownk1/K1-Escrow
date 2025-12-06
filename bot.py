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
        "disputed": False,
        "latest_message_id": None
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
        return price
    except:
        return None

async def clear_previous_buttons(context: ContextTypes.DEFAULT_TYPE, escrow: dict):
    """Clears buttons on the previous message to keep chat clean."""
    if escrow.get("latest_message_id"):
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=escrow["group_id"],
                message_id=escrow["latest_message_id"],
                reply_markup=None
            )
        except:
            pass

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Welcome To K1 Escrow Bot! 🤖\n\n"
        "1) Add this bot to a group with buyer/seller\n"
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

# ---------------- CALLBACK HANDLERS ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # "payment_received_<ticket>" or "payment_not_received_<ticket>"

    if not data.startswith("payment_"):
        return

    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        return

    payment_ok = status_word == "received"

    # Find escrow by ticket
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        await query.message.reply_text(f"Escrow with ticket {ticket} not found.")
        return

    chat_id = escrow["group_id"]
    buyer_id = escrow["buyer_id"]
    seller_id = escrow["seller_id"]

    await clear_previous_buttons(context, escrow)

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = True
        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"✅ Payment Confirmed\n🎟️ Ticket: {ticket}\n💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 {escrow['crypto_amount']} {escrow['crypto']}\n👤 Buyer: @{(await context.bot.get_chat_member(chat_id, buyer_id)).user.username}\n"
            f"👤 Seller: @{(await context.bot.get_chat_member(chat_id, seller_id)).user.username}"
        )
        # Notify escrow chat
        await context.bot.send_message(
            chat_id,
            f"✅ Status: Payment Confirmed\n🎟️ Ticket: {ticket}\n💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 {escrow['crypto_amount']} {escrow['crypto']}\nSeller can now send goods/services."
        )
    else:
        escrow["status"] = "awaiting_payment"
        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ Payment Not Received\n🎟️ Ticket: {ticket}\n💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 {escrow['crypto_amount']} {escrow['crypto']}\n👤 Buyer: @{(await context.bot.get_chat_member(chat_id, buyer_id)).user.username}\n"
            f"👤 Seller: @{(await context.bot.get_chat_member(chat_id, seller_id)).user.username}"
        )
        # Notify escrow chat
        await context.bot.send_message(
            chat_id,
            f"⏳ Status: Awaiting Payment\n🎟️ Ticket: {ticket}\n💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 {escrow['crypto_amount']} {escrow['crypto']}\nResponse: Payment has not yet been received in escrow. You will be updated shortly once payment is received."
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data

    if chat_id is None:
        return

    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = create_new_escrow(chat_id)

    # Cancel Escrow
    if data == "cancel_escrow":
        escrows.pop(chat_id, None)
        await query.message.reply_text("Escrow cancelled. Use /escrow to start again.")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ Escrow {escrow['ticket']} was cancelled in group {chat_id}."
        )
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"🤝 Status: New Trade\nAction: @{username} joined as Buyer 💷\nTicket: {escrow['ticket']}"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🤝 Status: Buyer Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Buyer: @{username}"
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"🤝 Status: New Trade\nAction: @{username} joined as Seller 📦\nTicket: {escrow['ticket']}"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🤝 Status: Seller Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Seller: @{username}"
        )

    # Both Joined — next step
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        msg = await context.bot.send_message(
            chat_id,
            f"✅ Status: Both Parties Joined\n🎟️ Ticket: {escrow['ticket']}\n"
            f"👤 Buyer: @{(await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username}\n"
            f"👤 Seller: @{(await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username}\n"
            "Action: Buyer, select payment method 👇",
            reply_markup=create_buttons([
                ("BTC", "crypto_BTC"),
                ("ETH", "crypto_ETH"),
                ("LTC", "crypto_LTC"),
                ("SOL", "crypto_SOL")
            ])
        )
        escrow["latest_message_id"] = msg.message_id

    # Crypto Selection
    if data.startswith("crypto_") and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"💷 Payment Method Selected\n🎟️ Ticket: {escrow['ticket']}\n🪙 Crypto: {crypto}\n👤 Buyer: @{username}"
        )

        await query.message.reply_text(
            f"Action: You selected {crypto} 🪙\nResponse: Now type the amount in GBP using /amount command ✍️\n(e.g /amount 100)"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Buyer Paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"

        await clear_previous_buttons(context, escrow)

        msg = await context.bot.send_message(
            chat_id,
            f"⏳ Status: Awaiting Payment\n"
            f"🎟️ Ticket: {escrow['ticket']}\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 {escrow['crypto_amount']} {escrow['crypto']}\n"
            "Response: Please wait whilst we confirm this transaction..."
        )
        escrow["latest_message_id"] = msg.message_id

        # Send Yes/No buttons to admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"💰 Payment Awaiting Confirmation\n🎟️ Ticket: {escrow['ticket']}\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"👤 Buyer: @{(await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username}\n"
            f"👤 Seller: @{(await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username}",
            reply_markup=create_buttons([
                ("Yes ✅", f"payment_received_{escrow['ticket']}"),
                ("No ❌", f"payment_not_received_{escrow['ticket']}")
            ])
        )

        dispute_msg = await context.bot.send_message(
            chat_id,
            "⚠️ Dispute button available if needed",
            reply_markup=create_buttons([("Dispute ⚠️", "dispute")])
        )
        escrow["latest_message_id"] = dispute_msg.message_id

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

    await update.message.reply_text(
        f"💷 Amount: £{amount}\n🪙 {crypto} Amount: {crypto_amount}\nSend exact amount to:\n`{wallet}`",
        parse_mode="Markdown",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow")
        ])
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    app = ApplicationBuilder().token(TOKEN).build()

    # Payment Yes/No handler MUST come first
    app.add_handler(CallbackQueryHandler(
        handle_admin_payment_confirmation,
        pattern=r"^payment_(received|not_received)_[A-Z0-9]+$"
    ))

    # General button handler
    app.add_handler(CallbackQueryHandler(button_callback))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
