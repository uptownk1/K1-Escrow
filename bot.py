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
        "latest_message_id": None,
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
            logging.error(f"Price for {symbol} not found in response.")
            return None
        return price
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching crypto price for {symbol}: {e}")
        return None

async def update_dispute_button(chat_id, context: ContextTypes.DEFAULT_TYPE, escrow):
    # Remove previous buttons
    if escrow.get("latest_message_id"):
        try:
            await context.bot.edit_message_reply_markup(chat_id, escrow["latest_message_id"], reply_markup=None)
        except:
            pass

    # Determine buttons
    buttons = [("Dispute 🛑", "dispute_trade")]
    if escrow["buyer_id"]:
        buttons.insert(0, ("Cancel ❌", "cancel_escrow"))

    msg = await context.bot.send_message(
        chat_id,
        f"🛎️ *Status Update*",
        parse_mode="Markdown",
        reply_markup=create_buttons(buttons)
    )
    escrow["latest_message_id"] = msg.message_id

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Welcome To K1 Escrow Bot! 🤖\n\n"
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

# ---------------- CALLBACKS ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Format: payment_received_ticket or payment_not_received_ticket
    if "_" not in data:
        return
    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        return

    payment_ok = status_word == "received"

    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        return

    # Fetch usernames
    buyer = await context.bot.get_chat_member(escrow["group_id"], escrow["buyer_id"])
    seller = await context.bot.get_chat_member(escrow["group_id"], escrow["seller_id"])
    buyer_name = f"@{buyer.user.username} (Buyer)" if buyer else "Buyer Unknown"
    seller_name = f"@{seller.user.username} (Seller)" if seller else "Seller Unknown"

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = True

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"✅ *Payment Confirmed*\n"
            f"Action: Payment RECEIVED\n"
            f"Ticket: {ticket}\n"
            f"Buyer: {buyer_name}\n"
            f"Seller: {seller_name}\n"
            f"Amount: £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}",
            parse_mode="Markdown"
        )

        # Send group message with dispute button only
        await update_dispute_button(escrow["group_id"], context, escrow)

    else:
        escrow["status"] = "awaiting_payment"
        escrow["buyer_confirmed"] = False

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ *Payment NOT Received*\n"
            f"Action: Payment NOT RECEIVED\n"
            f"Ticket: {ticket}\n"
            f"Buyer: {buyer_name}\n"
            f"Seller: {seller_name}\n"
            f"Amount: £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}",
            parse_mode="Markdown"
        )

        await context.bot.send_message(
            escrow["group_id"],
            f"🕒 *Status: Awaiting Payment*\n"
            f"Ticket Number: {ticket}\n"
            f"Response: Payment of £{escrow['fiat_amount']} has not yet been received. "
            f"This chat will update shortly when payment has been received in escrow.",
            parse_mode="Markdown"
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

    # Cancel
    if data == "cancel_escrow":
        escrows.pop(chat_id, None)
        await query.message.reply_text("Escrow cancelled.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"Escrow {escrow['ticket']} was cancelled.")
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"✅ @{username} joined as Buyer 💷\nTicket: {escrow['ticket']}")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🛎️ *Status: New Trade*\nAction: @{username} joined as Buyer 💷\nTicket: {escrow['ticket']}",
            parse_mode="Markdown"
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"✅ @{username} joined as Seller 📦\nTicket: {escrow['ticket']}")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🛎️ *Status: New Trade*\nAction: @{username} joined as Seller 📦\nTicket: {escrow['ticket']}",
            parse_mode="Markdown"
        )

    # Both joined
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(
            chat_id,
            "✅ Both Parties Joined\nAction: Buyer select payment method",
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
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🪙 *Crypto Selected*\nAction: Buyer @{username} selected {crypto}\nTicket: {escrow['ticket']}",
            parse_mode="Markdown"
        )
        await query.message.reply_text(f"You selected {crypto}. Now type amount with /amount (e.g /amount 100)")

    # Buyer Paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"💷 *Awaiting Payment Confirmation*\nBuyer @{username} marked as paid.\nTicket: {escrow['ticket']}\nAmount: £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}",
            parse_mode="Markdown",
            reply_markup=create_buttons([
                ("Yes ✅", f"payment_received_{escrow['ticket']}"),
                ("No ❌", f"payment_not_received_{escrow['ticket']}")
            ])
        )

        await context.bot.send_message(
            chat_id,
            f"🕒 *Status: Awaiting Payment*\nAmount: £{escrow['fiat_amount']}\n{escrow['crypto']} Amount: {escrow['crypto_amount']}\nResponse: Please wait whilst we confirm this transaction on our network...",
            parse_mode="Markdown"
        )

        # Add dispute button for latest message
        await update_dispute_button(chat_id, context, escrow)

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
        await update.message.reply_text("Unable to fetch crypto price. Try later.")
        return

    crypto_amount = round(amount / price, 8)
    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"
    wallet = ESCROW_WALLETS.get(crypto)

    await update.message.reply_text(
        f"💷 Amount: £{amount}\n🪙 {crypto} Amount: {crypto_amount}\nSend exact amount to:\n`{wallet}`\n\nConfirm below once payment is made 👇",
        parse_mode="Markdown",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow")
        ])
    )

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"💰 *Amount Set*\nBuyer @{update.message.from_user.username} set amount £{amount} / {crypto_amount} {crypto}\nTicket: {escrow['ticket']}",
        parse_mode="Markdown"
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    app = ApplicationBuilder().token(TOKEN).build()

    # Admin first
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
