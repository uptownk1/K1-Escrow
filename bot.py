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
    if escrow["status"] is None:
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
    data = query.data
    await query.answer()
    parts = data.split("_")
    if len(parts) < 3:
        return
    action = parts[1]
    ticket = "_".join(parts[2:])
    payment_ok = action == "received"
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
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
            f"💷 Amount: £{escrow['fiat_amount']}\n🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n📄 Action: Payment confirmed by admin",
            parse_mode="Markdown"
        )
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Payment Confirmed ✅\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            "📄 Action: Seller can now send goods/services 👇",
            reply_markup=create_buttons([
                ("I've sent the goods/services ✅", "seller_sent_goods"),
                ("Dispute ⚠️", "dispute")
            ])
        )
        escrow["latest_message_id"] = msg.message_id
    else:
        escrow["status"] = "awaiting_payment"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ❌\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n📄 Action: Payment not received",
            parse_mode="Markdown"
        )
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ❌\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            "📄 Response: Payment has not yet been received. You will be updated once received.",
            reply_markup=create_buttons([("Dispute ⚠️", "dispute")])
        )
        escrow["latest_message_id"] = msg.message_id

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

    # Cancel Escrow
    if data == "cancel_escrow":
        if escrow["status"] not in [None, "crypto_selection", "awaiting_amount"]:
            await query.message.reply_text("⛔ Cannot cancel escrow at this stage.")
            return
        escrows.pop(chat_id, None)
        await query.message.reply_text("Escrow cancelled. Use /escrow to start again.")
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ Escrow {escrow['ticket']} was cancelled in group {chat_id}."
        )
        await clear_previous_buttons(context, escrow)
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"🤝 Status: New Trade\nAction: @{username} joined as Buyer 💷\n🎟️ Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"🤝 Status: Buyer Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Buyer: @{username}")

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"🤝 Status: New Trade\nAction: @{username} joined as Seller 📦\n🎟️ Ticket: {escrow['ticket']}")
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
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Amount 💷\n"
            f"🪙 Crypto: {crypto}\n👤 Buyer: @{username}\n📄 Action: Buyer selected payment method"
        )
        await query.message.reply_text(f"Action: You selected {crypto} 🪙\nResponse: Now type the amount in GBP using /amount command ✍️\n(e.g /amount 100)")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Buyer Paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        await clear_previous_buttons(context, escrow)
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ⏳\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            "📄 Response: Please wait whilst we confirm this transaction...",
            reply_markup=create_buttons([("Dispute ⚠️", "dispute")])
        )
        escrow["latest_message_id"] = msg.message_id
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ⏳\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"👤 Buyer: @{(await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username}\n"
            f"👤 Seller: @{(await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username}\n"
            "📄 Action: Payment awaiting admin confirmation",
            reply_markup=create_buttons([("Yes ✅", f"payment_received_{escrow['ticket']}"), ("No ❌", f"payment_notreceived_{escrow['ticket']}")])
        )

    # Seller Sent Goods
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True
        escrow["status"] = "awaiting_buyer_action"
        buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Goods Sent 📦\n"
            f"👤 Seller: @{username}\n👤 Buyer: @{buyer_username}\n📄 Action: Seller marked goods as sent"
        )
        msg_text = "📦 Seller says goods are sent.\nOnce happy, press 'Release Funds ✅' below.\nAny issues, press 'Dispute ⚠️'."
        buttons = create_buttons([("Release Funds ✅", "buyer_release_funds"), ("Dispute ⚠️", "dispute")])
        msg = await context.bot.send_message(chat_id, msg_text, reply_markup=buttons)
        escrow["latest_message_id"] = msg.message_id

    # Buyer confirms receipt / Release Funds
    if data == "buyer_release_funds" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_seller_wallet"
        ticket = escrow["ticket"]
        coin = escrow["crypto"]
        buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
        seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {ticket}\n📌 Status: Awaiting Seller Wallet ⏳\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n🪙 Crypto: {coin}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n📄 Action: Buyer confirmed receipt. Seller, please paste your wallet address."
        )
        await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {ticket}\n📌 Status: Awaiting Seller Wallet ⏳\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n🪙 Crypto: {coin}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n📄 Response: Buyer confirmed receipt. Seller, please use /wallet <your-wallet>.",
            reply_markup=create_buttons([("Dispute ⚠️", "dispute")])
        )

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
        await update.message.reply_text("Unable to fetch the price for the selected cryptocurrency. Try again later.")
        return
    crypto_amount = round(amount / price, 8)
    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"
    wallet = ESCROW_WALLETS.get(crypto)
    await clear_previous_buttons(context, escrow)
    await update.message.reply_text(
        f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ⏳\n"
        f"💷 Amount: £{amount}\n🪙 {crypto} Amount: {crypto_amount}\nSend exact amount to:\n`{wallet}`",
        parse_mode="Markdown",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow"),
            ("Dispute ⚠️", "dispute")
        ])
    )

# ---------------- WALLET HANDLER ----------------
async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    escrow = escrows.get(chat_id)
    if not escrow:
        await update.message.reply_text("No active escrow in this group.")
        return
    if escrow.get("status") != "awaiting_seller_wallet":
        await update.message.reply_text("Cannot set wallet now.")
        return
    if user_id != escrow.get("seller_id"):
        await update.message.reply_text("Only the seller can send the wallet address.")
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
    amount = escrow['fiat_amount']
    # Notify admin with release button
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n📌 Status: Awaiting Admin Release ⏳\n"
        f"💷 Amount: £{amount}\n🪙 Crypto: {coin}\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"👛 Seller Wallet: `{wallet_address}`\n📄 Response: Please confirm funds release.",
        parse_mode="Markdown",
        reply_markup=create_buttons([("Mark as Sent ✅", f"admin_sent_{ticket}")])
    )
    # Notify escrow group
    await update.message.reply_text(
        f"🎟️ Ticket: {ticket}\n📌 Status: Awaiting Admin Release ⏳\n"
        f"💷 Amount: £{amount}\n🪙 Crypto: {coin}\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        "📄 Response: Please wait, your funds are now being released..."
    )

# ---------------- ADMIN RELEASE FUNDS ----------------
async def admin_sent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("admin_sent_"):
        return
    ticket = data.split("_", 2)[2]
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        await query.message.reply_text("Escrow not found.")
        return
    chat_id = escrow["group_id"]
    buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
    seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
    amount = escrow['fiat_amount']
    crypto = escrow['crypto']
    wallet = escrow.get('wallet_address', 'Not provided')
    await clear_previous_buttons(context, escrow)
    escrow["status"] = "completed"
    # Notify admin
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n📌 Status: Trade Completed ✅\n"
        f"💷 Amount: £{amount}\n🪙 Crypto: {crypto}\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"👛 Seller Wallet: `{wallet}`\n📄 Response: Funds released successfully.",
        parse_mode="Markdown"
    )
    # Notify escrow group
    await context.bot.send_message(
        chat_id,
        f"🎟️ Ticket: {ticket}\n📌 Status: Trade Completed ✅\n"
        f"💷 Amount: £{amount}\n🪙 Crypto: {crypto}\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"👛 Seller Wallet: `{wallet}`\n📄 Response: Trade completed successfully. Funds released to seller.",
        parse_mode="Markdown"
    )
    escrows.pop(chat_id, None)

# ---------------- DISPUTE HANDLER ----------------
async def dispute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    escrow = escrows.get(chat_id)
    if not escrow:
        await query.message.reply_text("No active escrow in this group.")
        return
    if user_id not in [escrow.get("buyer_id"), escrow.get("seller_id")]:
        await query.message.reply_text("Only participants can open a dispute.")
        return
    if escrow.get("disputed"):
        await query.message.reply_text("Dispute is already open. Please wait for admin to resolve.")
        return
    escrow["disputed"] = True
    escrow["status"] = "disputed"
    ticket = escrow["ticket"]
    buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
    seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
    amount = escrow.get("fiat_amount", "N/A")
    crypto = escrow.get("crypto", "N/A")
    await context.bot.send_message(
        chat_id,
        f"🎟️ Ticket: {ticket}\n📌 Status: Trade Disputed ⚠️\n"
        f"💷 Amount: {amount}\n🪙 Crypto: {crypto}\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"📄 Action: Trade disputed by @{username}. Escrow is now paused. Please wait for admin to review.",
        parse_mode="Markdown"
    )
    try:
        invite_link = await context.bot.create_chat_invite_link(chat_id)
    except:
        invite_link = "Cannot generate invite link."
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n📌 Status: Trade Disputed ⚠️\n"
        f"💷 Amount: {amount}\n🪙 Crypto: {crypto}\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"📄 Action: Trade disputed by @{username}. Escrow is paused. Join the group to review:\n{invite_link}",
        parse_mode="Markdown"
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    app = ApplicationBuilder().token(TOKEN).build()
    # Callback Handlers
    app.add_handler(CallbackQueryHandler(handle_admin_payment_confirmation, pattern=r"^payment_(received|notreceived)_.*$"))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CallbackQueryHandler(admin_sent_callback, pattern=r"^admin_sent_.*$"))
    app.add_handler(CallbackQueryHandler(dispute_callback, pattern=r"^dispute$"))
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("wallet", wallet_command))
    # Messages
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))
    app.run_polling()

if __name__ == "__main__":
    main()
