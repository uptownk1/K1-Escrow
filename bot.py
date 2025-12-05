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
        return data.get(coingecko_symbol, {}).get(FIAT_CURRENCY)
    except:
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

# ---------------- CALLBACK HANDLER (ADMIN CONFIRMATION) ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    logging.info(f"[ADMIN HANDLER TRIGGERED] Callback data: {data}")
    await query.answer()

    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        return

    payment_ok = status_word == "received"

    # Find escrow by ticket
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        return

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = True

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Admin confirmed payment for Escrow {ticket}: RECEIVED."
        )

        await context.bot.send_message(
            escrow["group_id"],
            f"Status: Payment Received ✅\n"
            f"Amount £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']} received in escrow.\n\n"
            "Seller can now send goods/services 👇",
            reply_markup=create_buttons([
                ("I've sent the goods/services ✅", "seller_sent_goods")
            ])
        )
    else:
        escrow["status"] = "payment_not_received"
        escrow["buyer_confirmed"] = False

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Admin marked payment for Escrow {ticket}: NOT RECEIVED."
        )

        await context.bot.send_message(
            escrow["group_id"],
            "Payment has not yet been received in escrow. You will receive an update shortly.",
            reply_markup=create_buttons([
                ("Cancel Escrow", "cancel_escrow")
            ])
        )

# ---------------- CALLBACK HANDLER (MAIN BUTTONS) ----------------
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
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']} was cancelled in group {chat_id}."
        )
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"Status: New Trade 🤝\nAction: @{username} joined as Buyer 💷\nTicket: {escrow['ticket']} 🎟️"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"Buyer @{username} joined escrow {escrow['ticket']}.")

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"Status: New Trade 🤝\nAction: @{username} joined as Seller 📦\nTicket: {escrow['ticket']} 🎟️"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"Seller @{username} joined escrow {escrow['ticket']}.")

    # Both joined
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(
            chat_id,
            f"Both parties joined! Buyer, select payment method 👇",
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
            f"You selected {crypto} 🪙\nNow type the amount in GBP using /amount command"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # ---------------- BUYER MARKED AS PAID (FIXED) ----------------
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"

        await query.message.reply_text(
            f"Status: Awaiting Payment ⏳\n"
            f"Ticket Number: {escrow['ticket']} 🎟️\n"
            "Buyer marked as paid. Waiting for admin confirmation…"
        )

        await query.message.edit_reply_markup(create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Dispute 🛑", "dispute_trade")
        ]))

        # FIX: Correct variables
        amount = escrow["fiat_amount"]
        crypto_amount = escrow["crypto_amount"]
        crypto = escrow["crypto"]

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🔔 *Buyer Marked as Paid*\n\n"
            f"👤 Buyer: @{username}\n"
            f"🎟️ Ticket: {escrow['ticket']}\n\n"
            f"💷 Fiat Amount: *£{amount}*\n"
            f"🪙 Crypto Amount: *{crypto_amount} {crypto}*\n\n"
            f"Confirm payment:",
            reply_markup=create_buttons([
                ("Yes ✅", f"payment_received_{escrow['ticket']}"),
                ("No ❌", f"payment_not_received_{escrow['ticket']}")
            ])
        )

    # Seller sent goods
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True
        escrow["status"] = "awaiting_buyer_confirmation"

        await context.bot.send_message(
            chat_id,
            "Seller marked goods/services as sent. Buyer, confirm receipt or dispute."
        )

    # Dispute
    if data == "dispute_trade":
        escrow["disputed"] = True
        escrow["status"] = "disputed"

        await query.message.reply_text(
            "Trade has been disputed. Please add admin to resolve."
        )

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']} disputed by "
            f"{'Buyer' if user_id == escrow['buyer_id'] else 'Seller'}."
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
    except:
        await update.message.reply_text("Invalid amount. Example: /amount 50")
        return

    crypto = escrow["crypto"]
    price = get_crypto_price(crypto)
    if price is None:
        await update.message.reply_text("Unable to fetch crypto price. Try again later.")
        return

    crypto_amount = round(amount / price, 8)

    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"

    wallet = ESCROW_WALLETS.get(crypto)

    await update.message.reply_text(
        f"Send payment:\n\n"
        f"£{amount} → {crypto_amount} {crypto}\n"
        f"Wallet:\n{wallet}\n\n"
        "Tap below once paid 👇",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow")
        ])
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    app = ApplicationBuilder().token(TOKEN).build()

    # Admin confirmation handler first
    app.add_handler(CallbackQueryHandler(
        handle_admin_payment_confirmation,
        pattern=r"^payment_(received|not_received)_[A-Z0-9]+$"
    ))

    # Other button callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
