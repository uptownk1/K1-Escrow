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
ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_ID"))
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
        "button_message_ids": [],  # Track all messages with buttons
    }
    escrows[chat_id] = escrow
    return escrow

def create_buttons(items):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items])

def clear_buttons(context: ContextTypes.DEFAULT_TYPE, escrow: dict):
    """Clear reply_markup from all previous messages with buttons."""
    for msg_id in escrow.get("button_message_ids", []):
        try:
            context.bot.edit_message_reply_markup(chat_id=escrow["group_id"], message_id=msg_id, reply_markup=None)
        except:
            pass
    escrow["button_message_ids"] = []

def get_crypto_price(symbol):
    symbol_mapping = {"BTC": "bitcoin", "ETH": "ethereum", "LTC": "litecoin", "SOL": "solana"}
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

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Welcome To K1 Escrow Bot!\nUse /escrow to start a trade.")

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id not in escrows:
        create_new_escrow(chat_id)
    escrow = escrows[chat_id]
    buttons = []
    if not escrow["buyer_id"]:
        buttons.append([InlineKeyboardButton("Join as Buyer 💷", callback_data="join_buyer")])
    if not escrow["seller_id"]:
        buttons.append([InlineKeyboardButton("Join as Seller 📦", callback_data="join_seller")])
    await update.message.reply_text("Both select your role to start escrow 👇", reply_markup=InlineKeyboardMarkup(buttons))

# ---------------- CALLBACK HANDLER ----------------
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

    # Buyer joins
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"💷 @{username} joined as Buyer.")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🤝 Status: Buyer Joined\n💷 Buyer: @{username}\n🎟️ Ticket: {escrow['ticket']}"
        )

    # Seller joins
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"📦 @{username} joined as Seller.")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🤝 Status: Seller Joined\n📦 Seller: @{username}\n🎟️ Ticket: {escrow['ticket']}"
        )

    # Both joined -> choose crypto
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        msg = await context.bot.send_message(
            chat_id,
            f"✅ Status: Both Parties Joined\n🎟️ Ticket: {escrow['ticket']}\nAction: Buyer, select payment method 👇",
            reply_markup=create_buttons([
                ("BTC", "crypto_BTC"),
                ("ETH", "crypto_ETH"),
                ("LTC", "crypto_LTC"),
                ("SOL", "crypto_SOL")
            ])
        )
        escrow["button_message_ids"].append(msg.message_id)

    # Crypto selection
    if data.startswith("crypto_") and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]
        escrow["crypto"] = crypto
        escrow["status"] = "awaiting_amount"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"💷 Status: Payment Method Selected\n🎟️ Ticket: {escrow['ticket']}\nBuyer: @{username}\nAction: {crypto} selected"
        )
        await query.message.reply_text(f"💷 You selected {crypto} 🪙\nNow type /amount <GBP amount>")

    # Buyer confirms payment
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        clear_buttons(context, escrow)

        msg = await context.bot.send_message(
            chat_id,
            f"⏳ Status: Awaiting Payment\n🎟️ Ticket: {escrow['ticket']}\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n"
            f"{escrow['crypto']} Amount: {escrow['crypto_amount']} {escrow['crypto']}\n"
            "Response: Please wait whilst we confirm this transaction on our network..."
        )
        escrow["button_message_ids"].append(msg.message_id)

        # Persistent admin buttons
        admin_msg = await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"⏳ Status: Awaiting Payment Confirmation\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Buyer: @{username}\nAmount: £{escrow['fiat_amount']}\n{escrow['crypto']} Amount: {escrow['crypto_amount']} {escrow['crypto']}\nConfirm?",
            reply_markup=create_buttons([
                ("Yes ✅", f"payment_received_{escrow['ticket']}"),
                ("No ❌", f"payment_not_received_{escrow['ticket']}")
            ])
        )
        escrow["button_message_ids"].append(admin_msg.message_id)

    # Dispute
    if data == "dispute_trade":
        escrow["status"] = "disputed"
        user_type = "Buyer" if user_id == escrow["buyer_id"] else "Seller"
        await query.message.reply_text(
            f"⚠️ Status: Dispute Raised\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Action: {user_type} @{username} has raised a dispute.\n"
            "Please add admin @uptownk1 to the group. Funds are safe."
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"⚠️ Status: Dispute Raised\n🎟️ Ticket: {escrow['ticket']}\n{user_type}: @{username}"
        )

# ---------------- ADMIN PAYMENT CONFIRMATION ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_")
    if len(parts) < 3:
        return
    _, status_word, ticket = parts
    payment_ok = status_word == "received"

    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        return

    buyer_user = await context.bot.get_chat_member(escrow["group_id"], escrow["buyer_id"])
    seller_user = await context.bot.get_chat_member(escrow["group_id"], escrow["seller_id"])
    buyer_name = f"@{buyer_user.user.username} (Buyer)" if buyer_user.user.username else f"{buyer_user.user.first_name} (Buyer)"
    seller_name = f"@{seller_user.user.username} (Seller)" if seller_user.user.username else f"{seller_user.user.first_name} (Seller)"

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        msg = await context.bot.send_message(
            escrow["group_id"],
            f"✅ Status: Payment Confirmed\n🎟️ Ticket: {escrow['ticket']}\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n"
            f"{escrow['crypto']} Amount: {escrow['crypto_amount']} {escrow['crypto']}\n"
            "Seller can now send goods/services. Both parties can raise dispute if needed.",
            reply_markup=create_buttons([("Dispute 🛑", "dispute_trade")])
        )
        escrow["button_message_ids"].append(msg.message_id)
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"✅ Status: Payment Confirmed\n🎟️ Ticket: {escrow['ticket']}\nBuyer: {buyer_name}\nSeller: {seller_name}"
        )
    else:
        escrow["status"] = "awaiting_payment"
        msg = await context.bot.send_message(
            escrow["group_id"],
            f"⏳ Status: Awaiting Payment\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: Payment of £{escrow['fiat_amount']} has not yet been received. This chat will update shortly when payment is received.",
            reply_markup=create_buttons([("Dispute 🛑", "dispute_trade")])
        )
        escrow["button_message_ids"].append(msg.message_id)
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ Status: Payment Not Received\n🎟️ Ticket: {escrow['ticket']}\nBuyer: {buyer_name}\nSeller: {seller_name}"
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
        await update.message.reply_text("Unable to fetch crypto price. Try again later.")
        return
    crypto_amount = round(amount / price, 8)
    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"
    wallet = ESCROW_WALLETS.get(crypto)

    clear_buttons(context, escrow)
    msg = await update.message.reply_text(
        f"💷 Status: Buyer Deposit\n🎟️ Ticket: {escrow['ticket']}\n"
        f"{crypto} Amount: {crypto_amount} {crypto} 🪙\n"
        f"Deposit Wallet: `{wallet}`\n\nConfirm below once payment is made 👇",
        parse_mode="MarkdownV2",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid")
        ])
    )
    escrow["button_message_ids"].append(msg.message_id)

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(TOKEN).build()

    # Admin payment confirmation
    app.add_handler(CallbackQueryHandler(
        handle_admin_payment_confirmation,
        pattern=r"^payment_(received|not_received)_[A-Z0-9]+$"
    ))

    # Other buttons
    app.add_handler(CallbackQueryHandler(button_callback))

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))

    # Amount handler
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
