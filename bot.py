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
        "latest_message_id": None  # track last message to clear buttons
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
    except Exception as e:
        logging.error(f"Error fetching crypto price: {e}")
        return None

async def clear_previous_buttons(context, escrow):
    if escrow.get("latest_message_id"):
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=escrow["group_id"],
                message_id=escrow["latest_message_id"],
                reply_markup=None
            )
        except Exception as e:
            logging.warning(f"Failed to clear previous buttons: {e}")

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Welcome To K1 Escrow Bot! 🤖\n"
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

    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        return

    payment_ok = status_word == "received"
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        return

    buyer_username = (await context.bot.get_chat_member(escrow['group_id'], escrow['buyer_id'])).user.username
    seller_username = (await context.bot.get_chat_member(escrow['group_id'], escrow['seller_id'])).user.username

    # Clear previous buttons in group
    await clear_previous_buttons(context, escrow)

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = True

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"✅ *Status: Payment Confirmed*\n"
            f"💷 *Amount:* £{escrow['fiat_amount']}\n"
            f"🪙 *Crypto Amount:* {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"🎟️ *Ticket:* {escrow['ticket']}\n"
            f"👤 Buyer: @{buyer_username}\n"
            f"👤 Seller: @{seller_username}",
            parse_mode="Markdown"
        )

        # Notify group
        msg = await context.bot.send_message(
            escrow["group_id"],
            f"✅ Status: Payment Confirmed\n"
            f"🎟️ Ticket: {escrow['ticket']}\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            "Seller can now send goods/services.",
            reply_markup=create_buttons([("Dispute ⚠️", "dispute")])
        )
        escrow["latest_message_id"] = msg.message_id

    else:
        escrow["status"] = "awaiting_payment"
        escrow["buyer_confirmed"] = False

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ *Status: Payment Not Received*\n"
            f"💷 *Amount:* £{escrow['fiat_amount']}\n"
            f"🪙 *Crypto Amount:* {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"🎟️ *Ticket:* {escrow['ticket']}\n"
            f"👤 Buyer: @{buyer_username}\n"
            f"👤 Seller: @{seller_username}",
            parse_mode="Markdown"
        )

        # Notify group
        msg = await context.bot.send_message(
            escrow["group_id"],
            f"⏳ Status: Awaiting Payment\n"
            f"🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: Payment of £{escrow['fiat_amount']} has not yet been received. This chat will update shortly when payment is received in escrow.",
            reply_markup=create_buttons([("Dispute ⚠️", "dispute"), ("I've Paid ✅", "buyer_paid")])
        )
        escrow["latest_message_id"] = msg.message_id

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
            ADMIN_GROUP_ID, f"❌ *Escrow Cancelled*\n🎟️ Ticket: {escrow['ticket']}\n👤 @{username} cancelled the trade.",
            parse_mode="Markdown"
        )
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"🤝 Status: New Trade\nAction: @{username} joined as Buyer 💷\nResponse: Waiting for Seller 📦\n🎟️ Ticket: {escrow['ticket']}"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🤝 *Status: Buyer Joined*\n🎟️ Ticket: {escrow['ticket']}\n👤 Buyer: @{username}",
            parse_mode="Markdown"
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"🤝 Status: New Trade\nAction: @{username} joined as Seller 📦\nResponse: Waiting for Buyer ⏳\n🎟️ Ticket: {escrow['ticket']}"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🤝 *Status: Seller Joined*\n🎟️ Ticket: {escrow['ticket']}\n👤 Seller: @{username}",
            parse_mode="Markdown"
        )

    # Both joined
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

    # Crypto selection
    if data.startswith("crypto_") and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"
        await query.message.reply_text(
            f"💷 Status: Payment Method\n🎟️ Ticket: {escrow['ticket']}\nAction: You selected {crypto} 🪙\nResponse: Now type the amount in GBP using /amount command ✍️\n(e.g /amount 100)"
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
        f"⏳ Status: Awaiting Payment\n🎟️ Ticket: {escrow['ticket']}\n"
        f"💷 Amount: £{amount}\n"
        f"🪙 Crypto: {crypto_amount} {crypto}\n"
        f"Send exact amount to:\n`{wallet}`\n\n"
        "Confirm below once payment is made 👇",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow"),
            ("Dispute ⚠️", "dispute")
        ]),
        parse_mode="Markdown"
    )
    escrow["latest_message_id"] = msg.message_id

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    app = ApplicationBuilder().token(TOKEN).build()

    # Admin first
    app.add_handler(CallbackQueryHandler(
        handle_admin_payment_confirmation,
        pattern=r"^payment_(received|not_received)_[A-Z0-9]+$"
    ))

    # All other buttons
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
