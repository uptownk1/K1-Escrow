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
    filters,
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
FEE_RATE = 0.05
FIAT_SYMBOL = "£"
FIAT_LABEL = "GBP"

# ---------------- DATA ----------------
escrows = {}

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
    if escrow["status"] is None:
        buttons.append([InlineKeyboardButton("Cancel ❌", callback_data="cancel_escrow")])
    return InlineKeyboardMarkup(buttons) if buttons else None

def create_buttons(items):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items])

def get_crypto_price(symbol):
    mapping = {"BTC": "bitcoin", "ETH": "ethereum", "LTC": "litecoin", "SOL": "solana"}
    coingecko_symbol = mapping.get(symbol.upper())
    if not coingecko_symbol:
        return None
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_symbol}&vs_currencies={FIAT_CURRENCY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get(coingecko_symbol, {}).get(FIAT_CURRENCY)
    except Exception:
        return None

def fmt_auto(number):
    try:
        n = float(number)
    except Exception:
        return str(number)
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n))}"
    else:
        return f"{n:.2f}"

def fmt_crypto(number):
    try:
        n = float(number)
    except Exception:
        return str(number)
    s = f"{n:.8f}".rstrip('0').rstrip('.')
    if '.' not in s:
        return s
    dec_part = s.split('.', 1)[1]
    if len(dec_part) == 1:
        return f"{s}0"
    return s

async def clear_previous_buttons(context: ContextTypes.DEFAULT_TYPE, escrow: dict):
    if escrow.get("latest_message_id"):
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=escrow["group_id"],
                message_id=escrow["latest_message_id"],
                reply_markup=None
            )
        except Exception:
            pass

def user_in_active_escrow(user_id):
    for chat_id, e in escrows.items():
        if e.get("buyer_id") == user_id or e.get("seller_id") == user_id:
            status = e.get("status")
            if status not in ["completed", "cancelled"]:
                return chat_id, e
    return None, None

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Welcome To K1 Escrow Bot! 🤖\n\n"
        "1) Add this bot to a group with buyer/seller\n"
        "2) /escrow to start a trade\n"
    )

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    existing_chat_id, existing_escrow = user_in_active_escrow(user_id)
    if existing_escrow:
        seller_joined = existing_escrow.get("seller_id") is not None
        status = existing_escrow.get("status")
        payment_marked = status in ["awaiting_payment", "awaiting_admin_confirmation", "awaiting_seller_wallet", "awaiting_admin_release", "awaiting_release"]
        if not seller_joined and not payment_marked:
            await update.message.reply_text(
                "⚠️ You already have a trade open.\nUse /cancel to cancel the current trade and start a new one."
            )
            return
        else:
            await update.message.reply_text(
                "🚫 You already have an active escrow trade and cannot start a new one."
            )
            return

    if chat_id not in escrows:
        create_new_escrow(chat_id)
    escrow = escrows[chat_id]
    await update.message.reply_text(
        "Both select your role to start escrow 👇",
        reply_markup=create_escrow_buttons(escrow)
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    active_chat_id, escrow = user_in_active_escrow(user_id)
    if not escrow:
        await update.message.reply_text("❌ You don't have any active trades.")
        return

    seller_joined = escrow.get("seller_id") is not None
    status = escrow.get("status")
    payment_marked = status in ["awaiting_payment", "awaiting_admin_confirmation", "awaiting_seller_wallet", "awaiting_admin_release", "awaiting_release"]

    if (not seller_joined) and (not payment_marked):
        escrows.pop(active_chat_id, None)
        await context.bot.send_message(chat_id=update.message.chat_id, text="🛑 Trade cancelled successfully — no seller had joined yet.")
        await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"❌ Escrow {escrow['ticket']} was cancelled by user.")
        return

    await update.message.reply_text(
        "🚫 You cannot cancel this trade. Contact an admin if needed."
    )

# ---------------- BUTTON CALLBACK ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data
    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = create_new_escrow(chat_id)

    # Cancel
    if data == "cancel_escrow":
        if escrow["status"] not in [None, "crypto_selection", "awaiting_amount"]:
            await query.message.reply_text("⛔ Cannot cancel escrow at this stage.")
            return
        escrows.pop(chat_id, None)
        await query.message.reply_text("Escrow cancelled. Use /escrow to start again.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"❌ Escrow {escrow['ticket']} was cancelled.")
        await clear_previous_buttons(context, escrow)
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"🤝 @{username} joined as Buyer 💷\n🎟️ Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"🤝 Buyer Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Buyer: @{username}")

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"🤝 @{username} joined as Seller 📦\n🎟️ Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"🤝 Seller Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Seller: @{username}")

    # Both Joined -> crypto selection
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        buyer_user = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
        seller_user = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\nBoth Parties Joined ✅\nBuyer: @{buyer_user}\nSeller: @{seller_user}\nSelect crypto 👇",
            reply_markup=create_buttons([
                ("BTC", "crypto_BTC"),
                ("ETH", "crypto_ETH"),
                ("LTC", "crypto_LTC"),
                ("SOL", "crypto_SOL")
            ])
        )
        escrow["latest_message_id"] = msg.message_id

    # Buyer selects crypto
    if data.startswith("crypto_") and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"
        await clear_previous_buttons(context, escrow)
        await context.bot.send_message(ADMIN_GROUP_ID, f"🎟️ Ticket: {escrow['ticket']}\nStatus: Awaiting Amount 💷\nCrypto: {crypto}\nBuyer: @{username}")
        await query.message.reply_text(f"📄 You selected {crypto} 🪙\nType amount in GBP using: `/amount 100`", parse_mode="Markdown")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))

# ---------------- AMOUNT HANDLER ----------------
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
        await update.message.reply_text("Unable to fetch price. Try later.")
        return
    crypto_amount = round(amount / price, 8)
    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"
    wallet = ESCROW_WALLETS.get(crypto)
    await clear_previous_buttons(context, escrow)
    await update.message.reply_text(
        f"🎟️ Ticket: {escrow['ticket']}\nAmount: {FIAT_SYMBOL}{fmt_auto(amount)} ({FIAT_LABEL})\nCrypto: {fmt_crypto(crypto_amount)} {crypto}\nSend to wallet:\n`{wallet}`",
        parse_mode="Markdown",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow"),
        ])
    )

# ---------------- MAIN ----------------
async def main():
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))
    app.add_handler(CallbackQueryHandler(button_callback))

    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
