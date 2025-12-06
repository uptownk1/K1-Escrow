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
FEE_RATE = 0.05  # 5% fee
FIAT_SYMBOL = "£"
FIAT_LABEL = "GBP"

# ---------------- DATA ----------------
escrows = {}  # key: ticket -> escrow dict

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
    escrows[ticket] = escrow
    return escrow

def create_escrow_buttons(escrow):
    buttons = []
    if not escrow["buyer_id"]:
        buttons.append([InlineKeyboardButton("Join as Buyer 💷", callback_data=f"join_buyer_{escrow['ticket']}")])
    if not escrow["seller_id"]:
        buttons.append([InlineKeyboardButton("Join as Seller 📦", callback_data=f"join_seller_{escrow['ticket']}")])
    if escrow["status"] is None:
        buttons.append([InlineKeyboardButton("Cancel ❌", callback_data=f"cancel_escrow_{escrow['ticket']}")])
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
    escrow = create_new_escrow(chat_id)
    await update.message.reply_text(
        "Both select your role to start escrow 👇",
        reply_markup=create_escrow_buttons(escrow)
    )

# ---------------- CALLBACK HANDLERS ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    parts = data.split("_")
    if len(parts) < 3:
        return
    action = parts[1]
    ticket = "_".join(parts[2:])
    payment_ok = action == "received"
    escrow = escrows.get(ticket)
    if not escrow:
        return
    chat_id = escrow["group_id"]
    buyer_id = escrow["buyer_id"]
    seller_id = escrow["seller_id"]
    buyer_username = (await context.bot.get_chat_member(chat_id, buyer_id)).user.username
    seller_username = (await context.bot.get_chat_member(chat_id, seller_id)).user.username
    await clear_previous_buttons(context, escrow)

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = True
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Payment Confirmed ✅\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {escrow['crypto']}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n📄 Action: Payment confirmed by admin",
            parse_mode="Markdown"
        )
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Payment Confirmed ✅\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {escrow['crypto']}\n"
            "📄 Action: Seller can now send goods/services to buyer\n"
            "👇 Seller: Mark as sent when done or dispute if needed",
            reply_markup=create_buttons([
                ("I've sent the goods/services ✅", f"seller_sent_goods_{ticket}"),
                ("Dispute ⚠️", f"dispute_{ticket}")
            ])
        )
        escrow["latest_message_id"] = msg.message_id
    else:
        escrow["status"] = "awaiting_payment"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ❌\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {escrow['crypto']}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n📄 Action: Payment not received",
            parse_mode="Markdown"
        )
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ❌\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n"
            "📄 Response: Payment has not yet been received. You will receive a message once it has confirmed on our system."
        )
        escrow["latest_message_id"] = msg.message_id

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data
    parts = data.split("_")
    action = parts[0]
    ticket = parts[-1] if len(parts) > 1 else None
    escrow = escrows.get(ticket)

    if not escrow and action in ["join", "cancel", "crypto", "buyer", "seller", "dispute"]:
        await query.message.reply_text("Escrow not found or expired.")
        return

    # Cancel Escrow
    if action == "cancel":
        if escrow["status"] not in [None, "crypto_selection", "awaiting_amount"]:
            await query.message.reply_text("⛔ Cannot cancel escrow at this stage.")
            return
        escrows.pop(ticket, None)
        await query.message.reply_text("Escrow cancelled. Use /escrow to start again.")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ Escrow {escrow['ticket']} was cancelled."
        )
        await clear_previous_buttons(context, escrow)
        return

    # Join Buyer
    if action == "join" and "buyer" in parts[1] and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"🤝 Status: New Trade\n📄 Action: @{username} joined as Buyer 💷\n🎟️ Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"🤝 Status: Buyer Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Buyer: @{username}")

    # Join Seller
    if action == "join" and "seller" in parts[1] and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"🤝 Status: New Trade\n📄 Action: @{username} joined as Seller 📦\n🎟️ Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"🤝 Status: Seller Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Seller: @{username}")

    # Both Joined → Crypto Selection
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Both Parties Joined ✅\n"
            f"👤 Buyer: @{(await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username}\n"
            f"👤 Seller: @{(await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username}\n"
            "📄 Action: Buyer select payment method 👇",
            reply_markup=create_buttons([
                ("BTC", f"crypto_BTC_{ticket}"),
                ("ETH", f"crypto_ETH_{ticket}"),
                ("LTC", f"crypto_LTC_{ticket}"),
                ("SOL", f"crypto_SOL_{ticket}")
            ])
        )
        escrow["latest_message_id"] = msg.message_id

    # Crypto Selection
    if action == "crypto" and user_id == escrow["buyer_id"]:
        crypto = parts[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Amount 💷\n"
            f"🪙 Crypto: {crypto}\n👤 Buyer: @{username}\n📄 Action: Buyer selected payment method"
        )
        await query.message.reply_text(f"📄 Action: You selected {crypto} 🪙\n✍️ Response: Type the amount in GBP using: `/amount 100`", parse_mode="Markdown")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Seller confirms goods sent
    if action == "seller" and "sent" in parts[1] and user_id == escrow.get("seller_id"):
        escrow["goods_sent"] = True
        escrow["status"] = "awaiting_buyer_action"
        await clear_previous_buttons(context, escrow)
        buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
        seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username

        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Goods/Services Sent 📦\n"
            f"👤 Seller: @{seller_username}\n👤 Buyer: @{buyer_username}\n"
            "📄 Action: Buyer confirm receipt or open dispute if needed",
            reply_markup=create_buttons([
                ("I've received the goods/services ✅", f"buyer_received_goods_{ticket}"),
                ("Dispute ⚠️", f"dispute_{ticket}")
            ])
        )
        escrow["latest_message_id"] = msg.message_id

    # Buyer confirms receipt
    if action == "buyer" and "received" in parts[1] and user_id == escrow.get("buyer_id"):
        escrow["goods_received"] = True
        escrow["status"] = "awaiting_seller_wallet"
        await clear_previous_buttons(context, escrow)
        await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Buyer Confirmed Receipt ✅\n"
            "📄 Action: Admin will now release funds to the seller",
            reply_markup=create_buttons([
                ("Dispute ⚠️", f"dispute_{ticket}")
            ])
        )

    # Dispute (both parties)
    if action == "dispute":
        if user_id not in [escrow.get("buyer_id"), escrow.get("seller_id")]:
            await query.message.reply_text("Only participants can open a dispute.")
            return
        if escrow.get("disputed"):
            await query.message.reply_text("Dispute already open. Please wait for admin.")
            return

        escrow["disputed"] = True
        escrow["status"] = "disputed"
        buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
        seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
        amount = escrow.get("fiat_amount", "N/A")
        crypto_amount = escrow.get("crypto_amount", 0)
        coin = escrow.get("crypto", "N/A")

        await clear_previous_buttons(context, escrow)
        await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Trade Disputed ⚠️\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(amount) if isinstance(amount, (int, float)) else amount} ({FIAT_LABEL}) ({fmt_crypto(crypto_amount)} {coin})\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
            f"📄 Action: Trade disputed by @{username}. Escrow paused, wait for admin review.",
            parse_mode="Markdown"
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Trade Disputed ⚠️\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(amount) if isinstance(amount, (int, float)) else amount} ({FIAT_LABEL}) ({fmt_crypto(crypto_amount)} {coin})\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
            f"📄 Action: Dispute opened by @{username}. Please manually review.",
            parse_mode="Markdown"
        )

# ---------------- AMOUNT HANDLER ----------------
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    escrow = next((e for e in escrows.values() if e["group_id"] == chat_id and e["buyer_id"] == user_id and e["status"]=="awaiting_amount"), None)
    if not escrow:
        await update.message.reply_text("No active escrow. Use /escrow.")
        return
    try:
        amount = float(text.split()[1])
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid amount. Example: /amount 50")
        return
    crypto = escrow["crypto"]
    price = get_crypto_price(crypto)
    if price is None:
        await update.message.reply_text("Unable to fetch the price. Try later.")
        return
    crypto_amount = round(amount / price, 8)
    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"
    wallet = ESCROW_WALLETS.get(crypto)
    await clear_previous_buttons(context, escrow)
    await update.message.reply_text(
        f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ⏳\n"
        f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(amount)} ({FIAT_LABEL})\n🪙 {fmt_crypto(crypto_amount)} {crypto}\n\n"
        f"📄 Send exact amount to wallet:\n\n`{wallet}`\n\n"
        "👇Mark as paid once done",
        parse_mode="Markdown",
        reply_markup=create_buttons([
            ("Payment Sent 💳", f"payment_sent_{escrow['ticket']}")
        ])
    )

# ---------------- MAIN ----------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("amount", handle_amount))
    app.add_handler(CallbackQueryHandler(handle_admin_payment_confirmation, pattern=r'payment_'))
    app.add_handler(CallbackQueryHandler(button_callback))
    print("Bot started")
    app.run_polling()
