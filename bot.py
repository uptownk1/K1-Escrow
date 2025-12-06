import os
import logging
from uuid import uuid4
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
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

# ---------------- DYNAMIC BUTTONS ----------------
def create_escrow_buttons_dynamic(escrow, user_id):
    buttons = []

    # Join buttons
    if not escrow["buyer_id"] and user_id != escrow.get("seller_id"):
        buttons.append([InlineKeyboardButton("Join as Buyer 💷", callback_data=f"join_buyer_{escrow['ticket']}")])
    if not escrow["seller_id"] and user_id != escrow.get("buyer_id"):
        buttons.append([InlineKeyboardButton("Join as Seller 📦", callback_data=f"join_seller_{escrow['ticket']}")])

    # Cancel (buyer-only, before payment)
    if escrow["status"] in [None, "crypto_selection", "awaiting_amount", "awaiting_payment"] and user_id == escrow.get("buyer_id"):
        buttons.append([InlineKeyboardButton("Cancel ❌", callback_data=f"cancel_escrow_{escrow['ticket']}")])

    # Crypto selection (buyer-only)
    if escrow["status"] == "crypto_selection" and user_id == escrow.get("buyer_id"):
        buttons.append([
            InlineKeyboardButton("BTC", callback_data=f"crypto_BTC_{escrow['ticket']}"),
            InlineKeyboardButton("ETH", callback_data=f"crypto_ETH_{escrow['ticket']}"),
            InlineKeyboardButton("LTC", callback_data=f"crypto_LTC_{escrow['ticket']}"),
            InlineKeyboardButton("SOL", callback_data=f"crypto_SOL_{escrow['ticket']}")
        ])

    # I've Paid (buyer-only)
    if escrow["status"] == "awaiting_payment" and user_id == escrow.get("buyer_id"):
        buttons.append([InlineKeyboardButton("I've Paid ✅", callback_data=f"buyer_paid_{escrow['ticket']}")])

    # Release Funds (buyer-only)
    if escrow["status"] == "awaiting_seller_wallet" and user_id == escrow.get("buyer_id"):
        buttons.append([InlineKeyboardButton("Release Funds 💷", callback_data=f"buyer_release_{escrow['ticket']}")])

    # Dispute (buyer or seller, after payment marked)
    if escrow["status"] == "payment_marked" and user_id in [escrow.get("buyer_id"), escrow.get("seller_id")]:
        buttons.append([InlineKeyboardButton("Dispute ⚠️", callback_data=f"dispute_{escrow['ticket']}")])

    return InlineKeyboardMarkup(buttons) if buttons else None

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
    escrow = create_new_escrow(chat_id)
    markup = create_escrow_buttons_dynamic(escrow, user_id)
    await update.message.reply_text(
        f"🎟️ Ticket: {escrow['ticket']}\nBoth select your role to start escrow 👇",
        reply_markup=markup
    )

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    escrow = None
    for t in escrows_by_group.get(chat_id, []):
        e = escrows.get(t)
        if e and e["status"] == "awaiting_amount" and e["buyer_id"] == user_id:
            escrow = e
            break
    if not escrow:
        await update.message.reply_text("⛔ Only the buyer can enter the amount for this escrow.")
        return
    try:
        amount = float(text.split()[1])
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid amount. Example: /amount 50")
        return
    crypto = escrow["crypto"]
    price = get_crypto_price(crypto)
    if price is None:
        await update.message.reply_text("Unable to fetch price. Try again in 1-2 minutes.")
        return
    crypto_amount = round(amount / price, 8)
    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"
    wallet = ESCROW_WALLETS.get(crypto)
    await clear_previous_buttons(context, escrow)
    markup = create_escrow_buttons_dynamic(escrow, user_id)
    await update.message.reply_text(
        f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ⏳\n"
        f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(amount)} ({FIAT_LABEL})\n🪙 {fmt_crypto(crypto_amount)} {crypto}\n\n"
        f"📄 Send exact amount to wallet:\n`{wallet}`\n\n"
        "👇Mark as paid once done",
        parse_mode="Markdown",
        reply_markup=markup
    )

# ---------------- WALLET ----------------
async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    escrow = None
    for t in escrows_by_group.get(chat_id, []):
        e = escrows.get(t)
        if e and e["status"] == "awaiting_seller_wallet":
            escrow = e
            break
    if not escrow:
        await update.message.reply_text("No active escrow awaiting wallet submission.")
        return
    if user_id != escrow.get("seller_id"):
        await update.message.reply_text("⛔ Only the seller can provide wallet for this escrow.")
        return
    try:
        wallet_address = text.split(maxsplit=1)[1]
    except IndexError:
        await update.message.reply_text("Please provide your wallet: /wallet <your-wallet>")
        return
    escrow["wallet_address"] = wallet_address
    escrow["status"] = "awaiting_admin_release"
    ticket = escrow["ticket"]
    buyer_id = escrow["buyer_id"]
    seller_id = escrow["seller_id"]
    # Notify admin
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\nAdmin: Please release funds.",
        reply_markup=create_escrow_buttons_dynamic(escrow, None)
    )
    # Notify buyer and seller
    for uid in [buyer_id, seller_id]:
        markup = create_escrow_buttons_dynamic(escrow, uid)
        await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {ticket}\n📌 Status: Wallet Submitted ⏳\nWaiting for admin release.",
            reply_markup=markup
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

    # Cancel escrow (buyer-only, before payment)
    if action == "cancel_escrow":
        if user_id != escrow.get("buyer_id"):
            await query.message.reply_text("⛔ Only the buyer can cancel this escrow.")
            return
        if escrow["status"] in ["payment_marked", "awaiting_admin_release", "completed"]:
            await query.message.reply_text("⛔ Cannot cancel after payment is marked.")
            return
        escrows.pop(ticket, None)
        escrows_by_group[chat_id].remove(ticket)
        await query.message.reply_text(f"Escrow {ticket} cancelled.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"❌ Escrow {ticket} cancelled by buyer.")
        await clear_previous_buttons(context, escrow)
        return

    # Join buyer/seller
    if action == "join_buyer" and not escrow.get("buyer_id"):
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"🤝 @{username} joined as Buyer 💷")
        await query.message.edit_reply_markup(create_escrow_buttons_dynamic(escrow, user_id))
        await context.bot.send_message(ADMIN_GROUP_ID, f"Buyer joined | Ticket: {ticket}")
        return
    if action == "join_seller" and not escrow.get("seller_id"):
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"🤝 @{username} joined as Seller 📦")
        await query.message.edit_reply_markup(create_escrow_buttons_dynamic(escrow, user_id))
        await context.bot.send_message(ADMIN_GROUP_ID, f"Seller joined | Ticket: {ticket}")
        return

    # Crypto selection (buyer-only)
    if action.startswith("crypto"):
        if user_id != escrow.get("buyer_id"):
            await query.message.reply_text("⛔ Only buyer can select crypto.")
            return
        escrow["crypto"] = parts[1]
        escrow["status"] = "awaiting_amount"
        await query.message.reply_text(f"📄 You selected {escrow['crypto']}. Type `/amount <value>`.")
        await query.message.edit_reply_markup(create_escrow_buttons_dynamic(escrow, user_id))
        return

    # Buyer paid
    if action.startswith("buyer_paid"):
        if user_id != escrow.get("buyer_id"):
            await query.message.reply_text("⛔ Only buyer can mark payment.")
            return
        escrow["buyer_confirmed"] = True
        escrow["status"] = "payment_marked"
        await clear_previous_buttons(context, escrow)
        # Notify buyer
        markup_buyer = create_escrow_buttons_dynamic(escrow, escrow["buyer_id"])
        await context.bot.send_message(
            chat_id,
            f"✅ Payment marked for Ticket: {ticket}\nWaiting for seller and admin.",
            reply_markup=markup_buyer
        )
        # Notify seller
        markup_seller = create_escrow_buttons_dynamic(escrow, escrow["seller_id"])
        await context.bot.send_message(
            chat_id,
            f"💳 Buyer has marked payment for Ticket: {ticket}\nYou can now proceed.",
            reply_markup=markup_seller
        )
        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"💳 Payment marked by buyer for Ticket: {ticket}\nEscrow ready for next step."
        )
        return

    # Dispute
    if action.startswith("dispute"):
        if user_id not in [escrow.get("buyer_id"), escrow.get("seller_id")]:
            await query.message.reply_text("⛔ Only buyer or seller can dispute.")
            return
        if escrow["status"] != "payment_marked":
            await query.message.reply_text("⛔ Can dispute only after payment is marked.")
            return
        escrow["disputed"] = True
        escrow["status"] = "disputed"
        await context.bot.send_message(ADMIN_GROUP_ID, f"⚠️ Dispute opened | Ticket: {ticket}")
        await query.message.reply_text("⚠️ Dispute reported to admin.")
        await query.message.edit_reply_markup(create_escrow_buttons_dynamic(escrow, user_id))
        return

    # Buyer release funds
    if action.startswith("buyer_release"):
        if user_id != escrow.get("buyer_id"):
            await query.message.reply_text("⛔ Only buyer can release funds.")
            return
        if escrow["status"] != "awaiting_seller_wallet":
            await query.message.reply_text("⛔ Cannot release funds at this stage.")
            return
        escrow["status"] = "awaiting_admin_release"
        await query.message.reply_text("💷 Funds released. Waiting for admin.")
        await query.message.edit_reply_markup(create_escrow_buttons_dynamic(escrow, user_id))
        await context.bot.send_message(ADMIN_GROUP_ID, f"Buyer released funds | Ticket: {ticket}")
        return

# ---------------- ADMIN MARK SENT ----------------
async def admin_sent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.message.chat.id != ADMIN_GROUP_ID or not query.data.startswith("admin_sent_"):
        return
    ticket = query.data.split("_", 2)[2]
    escrow = escrows.get(ticket)
    if not escrow:
        await query.message.reply_text("Escrow not found.")
        return
    chat_id = escrow["group_id"]
    escrow["status"] = "completed"
    buyer_id = escrow["buyer_id"]
    seller_id = escrow["seller_id"]
    for uid in [buyer_id, seller_id]:
        markup = create_escrow_buttons_dynamic(escrow, uid)
        await context.bot.send_message(
            chat_id,
            f"🎉 Trade Completed!\n🎟️ Ticket: {ticket}\nFunds released to seller.",
            reply_markup=markup
        )
    escrows.pop(ticket, None)
    escrows_by_group[chat_id].remove(ticket)

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("amount", handle_amount))
    app.add_handler(CommandHandler("wallet", wallet_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CallbackQueryHandler(admin_sent_callback, pattern=r'^admin_sent_'))
    print("Bot running with fully user-specific buttons and fixed 'I've Paid' behavior...")
    app.run_polling()

if __name__ == "__main__":
    main()
