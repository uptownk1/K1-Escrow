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
if not TOKEN or not ADMIN_GROUP_ID:
    print("TOKEN or ADMIN_GROUP_ID not set! Exiting.")
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
escrows = {}  # ticket -> escrow dict
escrows_by_group = {}  # chat_id -> list of tickets

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
    escrows_by_group.setdefault(chat_id, []).append(ticket)
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
    symbol_mapping = {"BTC": "bitcoin","ETH": "ethereum","LTC": "litecoin","SOL": "solana"}
    coingecko_symbol = symbol_mapping.get(symbol.upper())
    if not coingecko_symbol:
        return None
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_symbol}&vs_currencies={FIAT_CURRENCY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get(coingecko_symbol, {}).get(FIAT_CURRENCY)
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
        f"🎟️ Ticket: {escrow['ticket']}\nBoth select your role to start escrow 👇",
        reply_markup=create_escrow_buttons(escrow)
    )

# ---------------- BUTTON CALLBACK ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data

    parts = data.split("_")
    ticket = parts[-1]
    escrow = escrows.get(ticket)
    if not escrow:
        await query.message.reply_text("Escrow not found or expired.")
        return

    action = "_".join(parts[:-1])

    # Cancel Escrow
    if action == "cancel_escrow":
        if escrow["status"] not in [None, "crypto_selection", "awaiting_amount"]:
            await query.message.reply_text("⛔ Cannot cancel escrow at this stage.")
            return
        escrows.pop(ticket, None)
        escrows_by_group[chat_id].remove(ticket)
        await query.message.reply_text(f"Escrow {ticket} cancelled. Use /escrow to start again.")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ Escrow {ticket} was cancelled."
        )
        await clear_previous_buttons(context, escrow)
        return

    # Join Buyer
    if action == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"🤝 Status: New Trade\n📄 Action: @{username} joined as Buyer 💷\n🎟️ Ticket: {ticket}")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"🤝 Status: Buyer Joined\n🎟️ Ticket: {ticket}\n👤 Buyer: @{username}")

    # Join Seller
    if action == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"🤝 Status: New Trade\n📄 Action: @{username} joined as Seller 📦\n🎟️ Ticket: {ticket}")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"🤝 Status: Seller Joined\n🎟️ Ticket: {ticket}\n👤 Seller: @{username}")

    # Both Joined → Crypto Selection
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {ticket}\n📌 Status: Both Parties Joined ✅\n"
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
    if action.startswith("crypto") and user_id == escrow["buyer_id"]:
        crypto = parts[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {ticket}\n📌 Status: Awaiting Amount 💷\n"
            f"🪙 Crypto: {crypto}\n👤 Buyer: @{username}\n📄 Action: Buyer selected payment method"
        )
        await query.message.reply_text(
            f"📄 Action: You selected {crypto} 🪙\n✍️ Response: Type the amount in GBP using: `/amount 100`",
            parse_mode="Markdown"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

# ---------------- AMOUNT HANDLER ----------------
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    escrow = None
    # Find correct escrow for this buyer awaiting amount
    for t in escrows_by_group.get(chat_id, []):
        e = escrows.get(t)
        if e and e["status"] == "awaiting_amount" and e["buyer_id"] == user_id:
            escrow = e
            break
    if not escrow:
        await update.message.reply_text("No active escrow awaiting amount for you.")
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
            ("I've Paid ✅", f"buyer_paid_{escrow['ticket']}"),
            ("Cancel ❌", f"cancel_escrow_{escrow['ticket']}"),
        ])
    )

# ---------------- WALLET HANDLER ----------------
async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    escrow = None
    for t in escrows_by_group.get(chat_id, []):
        e = escrows.get(t)
        if e and e["status"] == "awaiting_seller_wallet" and e["seller_id"] == user_id:
            escrow = e
            break

    if not escrow:
        for t in escrows_by_group.get(chat_id, []):
            e = escrows.get(t)
            if e and e["status"] == "awaiting_seller_wallet":
                await update.message.reply_text("Only seller can provide release address! Nice try.")
                return
        await update.message.reply_text("No active escrow awaiting your wallet.")
        return

    try:
        wallet_address = text.split(maxsplit=1)[1]
    except IndexError:
        await update.message.reply_text("Please provide your wallet: /wallet <your-wallet>")
        return

    escrow["wallet_address"] = wallet_address
    ticket = escrow["ticket"]
    buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
    seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
    coin = escrow['crypto']
    amount_fiat = escrow['fiat_amount'] or 0
    amount_crypto = escrow['crypto_amount'] or 0
    fee_fiat = amount_fiat * FEE_RATE
    payout_fiat = amount_fiat - fee_fiat

    # Notify admin with release button
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n📌 Status: Awaiting Admin Release ⏳\n\n"
        f"💷 Trade Amount: {FIAT_SYMBOL}{fmt_auto(amount_fiat)} ({FIAT_LABEL}) ({fmt_crypto(amount_crypto)} {coin})\n"
        f"💸 Escrow Fee (5%): {FIAT_SYMBOL}{fmt_auto(fee_fiat)} ({FIAT_LABEL})\n"
        f"🏦 Send To Seller: {FIAT_SYMBOL}{fmt_auto(payout_fiat)} ({FIAT_LABEL})\n\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"👛 Seller Wallet: `{wallet_address}`\n\n📄 Response: Please confirm funds release",
        parse_mode="Markdown",
        reply_markup=create_buttons([("Mark as Sent ✅", f"admin_sent_{ticket}")])
    )

    await update.message.reply_text(
        f"🎟️ Ticket: {ticket}\n📌 Status: Processing Payment...⏳\n\n"
        f"💷 Trade Amount: {FIAT_SYMBOL}{fmt_auto(amount_fiat)} ({FIAT_LABEL}) ({fmt_crypto(amount_crypto)} {coin})\n"
        f"💸 Escrow Fee (5%): {FIAT_SYMBOL}{fmt_auto(fee_fiat)} ({FIAT_LABEL})\n"
        f"🏦 Amount Being Released: {FIAT_SYMBOL}{fmt_auto(payout_fiat)} ({FIAT_LABEL})\n\n"
        "📄 Response: Funds are being sent to seller, you will receive an update in this chat when payment has been sent.",
        parse_mode="Markdown"
    )

# ---------------- ADMIN RELEASE FUNDS ----------------
async def admin_sent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if query.message.chat.id != ADMIN_GROUP_ID:
        return
    if not data.startswith("admin_sent_"):
        return

    ticket = data.split("_", 2)[2]
    escrow = escrows.get(ticket)
    if not escrow:
        await query.message.reply_text("Escrow not found.")
        return

    chat_id = escrow["group_id"]
    buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
    seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
    amount_fiat = escrow.get('fiat_amount', 0)
    amount_crypto = escrow.get('crypto_amount', 0)
    coin = escrow.get('crypto', 'N/A')
    fee_fiat = amount_fiat * FEE_RATE
    payout_fiat = amount_fiat - fee_fiat

    await clear_previous_buttons(context, escrow)
    escrow["status"] = "completed"

    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n📌 Status: Trade Completed ✅\n\n"
        f"💷 Amount Sent: {FIAT_SYMBOL}{fmt_auto(amount_fiat)} ({FIAT_LABEL}) ({fmt_crypto(amount_crypto)} {coin})\n"
        f"💸 Escrow Fee (5%): {FIAT_SYMBOL}{fmt_auto(fee_fiat)} ({FIAT_LABEL})\n"
        f"🏦 Amount After Fee: {FIAT_SYMBOL}{fmt_auto(payout_fiat)} ({FIAT_LABEL})\n\n"
        "📄 Action: Funds have been released to seller's wallet.",
        parse_mode="Markdown"
    )

    await context.bot.send_message(
        chat_id,
        f"🎉 Trade Completed!\n\n"
        f"🎟️ Ticket: {ticket}\n"
        f"💷 Amount Released: {FIAT_SYMBOL}{fmt_auto(payout_fiat)} ({FIAT_LABEL})\n"
        f"🪙 Crypto Amount: ({fmt_crypto(amount_crypto - (amount_crypto * FEE_RATE))} {coin})\n"
        f"💸 Escrow Fee Taken: {FIAT_SYMBOL}{fmt_auto(fee_fiat)} ({FIAT_LABEL})\n\n"
        "📄 Response: Funds have successfully been sent to seller.\n\n"
        "🫡 Thank you for using K1 Escrow Bot! You can now close this group.",
        parse_mode="Markdown"
    )

    escrows.pop(ticket, None)
    escrows_by_group[chat_id].remove(ticket)

# ---------------- DISPUTE HANDLER ----------------
async def dispute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name

    escrow = None
    for t in escrows_by_group.get(chat_id, []):
        e = escrows.get(t)
        if e and e["status"] not in ["completed", None] and user_id in [e.get("buyer_id"), e.get("seller_id")]:
            escrow = e
            break

    if not escrow:
        await query.message.reply_text("No active escrow you can dispute.")
        return

    await clear_previous_buttons(context, escrow)
    if escrow.get("disputed"):
        await query.message.reply_text("Dispute already open. Please wait for admin.")
        return

    escrow["disputed"] = True
    escrow["status"] = "disputed"
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"⚠️ Dispute Opened!\n🎟️ Ticket: {escrow['ticket']}\n"
        f"👤 User: @{username}\n"
        f"📄 Response: Admin, please resolve dispute.",
    )
    await query.message.reply_text("⚠️ Dispute has been reported to admin. Please wait for resolution.")

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("amount", handle_amount))
    app.add_handler(CommandHandler("wallet", wallet_command))

    # Callback buttons
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CallbackQueryHandler(admin_sent_callback, pattern=r'^admin_sent_'))
    app.add_handler(CallbackQueryHandler(dispute_callback, pattern=r'^dispute_'))

    print("Bot running on K1 Server (Panama City - Offshore) with no deploy issues...")
    app.run_polling()

if __name__ == "__main__":
    main()
