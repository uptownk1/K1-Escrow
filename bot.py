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
    data = query.data
    await query.answer()

    parts = data.split("_")
    if len(parts) < 3:
        return

    action = parts[1]  # 'received' or 'notreceived'
    ticket = "_".join(parts[2:])
    payment_ok = action == "received"

    # Find escrow by ticket
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        return

    chat_id = escrow["group_id"]
    buyer_id = escrow["buyer_id"]
    seller_id = escrow["seller_id"]

    buyer_username = (await context.bot.get_chat_member(chat_id, buyer_id)).user.username
    seller_username = (await context.bot.get_chat_member(chat_id, seller_id)).user.username

    # Clear all previous buttons in escrow chat
    await clear_previous_buttons(context, escrow)

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = True

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"✅ Payment Confirmed by Admin\n"
            f"🎟️ Ticket: {ticket}\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"👤 Buyer: @{buyer_username}\n"
            f"👤 Seller: @{seller_username}"
        )

        # Notify escrow group
        msg = await context.bot.send_message(
            chat_id,
            f"✅ Status: Payment Confirmed\n"
            f"🎟️ Ticket: {ticket}\n"
            f"Response: Payment has been confirmed. Seller can now send goods/services.",
            reply_markup=create_buttons([
                ("I've sent the goods/services ✅", "seller_sent_goods")
            ])
        )
        escrow["latest_message_id"] = msg.message_id

    else:
        escrow["status"] = "awaiting_payment"

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ Payment Not Received by Admin\n"
            f"🎟️ Ticket: {ticket}\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"👤 Buyer: @{buyer_username}\n"
            f"👤 Seller: @{seller_username}"
        )

        # Notify escrow group
        msg = await context.bot.send_message(
            chat_id,
            f"⏳ Status: Awaiting Payment\n"
            f"🎟️ Ticket: {ticket}\n"
            f"Response: Payment has not yet been received. You will be updated once admin confirms."
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
        await query.message.reply_text(
            f"🤝 Status: New Trade\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: @{username} joined as Buyer 💷"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🤝 Buyer Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Buyer: @{username}"
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"🤝 Status: New Trade\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: @{username} joined as Seller 📦"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🤝 Seller Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Seller: @{username}"
        )

    # Both Joined → Crypto Selection
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        msg = await context.bot.send_message(
            chat_id,
            f"✅ Status: Both Parties Joined\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: Buyer, select payment method 👇",
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
            f"💷 Status: Payment Method Selected\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: You selected {crypto} 🪙. Now type the amount in GBP using /amount command (e.g /amount 100)."
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Buyer Paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"

        # Clear previous buttons
        await clear_previous_buttons(context, escrow)

        # Notify escrow group
        msg = await context.bot.send_message(
            chat_id,
            f"💷 Status: Payment Marked as Paid\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: Buyer @{username} marked payment as sent. Awaiting admin confirmation."
        )
        escrow["latest_message_id"] = msg.message_id

        # Send admin buttons
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"💰 Payment Awaiting Confirmation\n🎟️ Ticket: {escrow['ticket']}\n"
            f"💷 Amount: £{escrow['fiat_amount']}\n"
            f"🪙 Crypto: {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"👤 Buyer: @{(await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username}\n"
            f"👤 Seller: @{(await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username}",
            reply_markup=create_buttons([
                ("Yes ✅", f"payment_received_{escrow['ticket']}"),
                ("No ❌", f"payment_notreceived_{escrow['ticket']}")
            ])
        )

        # Send dispute button for both buyer and seller
        dispute_msg = await context.bot.send_message(
            chat_id,
            f"⚠️ Status: Awaiting Payment\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: If there is an issue with payment, you can open a dispute 👇",
            reply_markup=create_buttons([("Dispute ⚠️", f"dispute_{escrow['ticket']}")])
        )
        escrow["latest_message_id"] = dispute_msg.message_id

    # Seller confirms goods sent
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True

        # Clear previous buttons
        await clear_previous_buttons(context, escrow)

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"📦 Seller @{username} marked goods/services as sent.\n"
            f"🎟️ Ticket: {escrow['ticket']}\nPlease confirm this action."
        )

        # Notify group
        msg = await context.bot.send_message(
            chat_id,
            f"📦 Status: Goods/Services Sent\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: Seller says they’ve sent goods/services. Buyer, press 'Release Funds ✅' if received or 'Dispute ⚠️' if issues.",
            reply_markup=create_buttons([
                ("Release Funds ✅", f"release_funds_{escrow['ticket']}"),
                ("Dispute ⚠️", f"dispute_{escrow['ticket']}")
            ])
        )
        escrow["latest_message_id"] = msg.message_id

    # Buyer presses Release Funds
    if data.startswith("release_funds_") and user_id == escrow["buyer_id"]:
        escrow["status"] = "completed"

        await clear_previous_buttons(context, escrow)

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"💷 Buyer @{username} released funds.\n🎟️ Ticket: {escrow['ticket']}\nEscrow completed successfully."
        )

        # Notify group
        await context.bot.send_message(
            chat_id,
            f"💷 Status: Funds Released\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: Buyer @{username} released funds. Escrow completed successfully."
        )

    # Buyer or Seller presses Dispute
    if data.startswith("dispute_") and user_id in [escrow["buyer_id"], escrow["seller_id"]]:
        escrow["disputed"] = True

        await clear_previous_buttons(context, escrow)

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"⚠️ Dispute opened by @{username}.\n🎟️ Ticket: {escrow['ticket']}\nPlease review and resolve."
        )

        # Notify group
        await context.bot.send_message(
            chat_id,
            f"⚠️ Status: Dispute Opened\n🎟️ Ticket: {escrow['ticket']}\n"
            f"Response: Dispute opened by @{username}. Admin has been notified."
        )
