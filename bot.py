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
        "button_message_ids": []  # store message ids of buttons for later clearing
    }
    escrows[chat_id] = escrow
    return escrow

def create_buttons(items):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items])

def clear_buttons(context, escrow):
    """Clears previous buttons for a specific escrow."""
    for msg_id in escrow["button_message_ids"]:
        try:
            context.bot.edit_message_reply_markup(escrow["group_id"], msg_id, reply_markup=None)
        except Exception as e:
            logging.error(f"Failed to clear buttons for message {msg_id}: {e}")
    escrow["button_message_ids"] = []  # reset message ids

async def update_buttons_for_payment_status(context, escrow, buyer_paid=False):
    """Sets the correct buttons based on payment status."""
    clear_buttons(context, escrow)
    
    if not buyer_paid:
        # Before buyer marks as paid: show Cancel button for buyer
        msg = await context.bot.send_message(
            escrow["group_id"],
            f"Buyer can cancel the escrow before marking as paid 👇\n\nTicket: {escrow['ticket']}",
            reply_markup=create_buttons([("Cancel ❌", "cancel_escrow")])
        )
        escrow["button_message_ids"].append(msg.message_id)
    else:
        # After buyer marks as paid: dispute button for both
        msg = await context.bot.send_message(
            escrow["group_id"],
            f"Dispute button available for both parties 🛑\n\nTicket: {escrow['ticket']}",
            reply_markup=create_buttons([("Dispute 🛑", "dispute_trade")])
        )
        escrow["button_message_ids"].append(msg.message_id)

async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if "_" not in data:
        return

    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        logging.warning("Invalid admin callback format")
        return

    payment_ok = status_word == "received"
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        logging.warning(f"No escrow found for ticket {ticket}")
        return

    if payment_ok:
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = payment_ok

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"✅ Status: Payment Confirmed\n🎟️ Ticket: {ticket}\nAction: Payment received and confirmed."
        )

        await context.bot.send_message(
            escrow["group_id"],
            f"✅ Status: Payment Confirmed\n🎟️ Ticket: {ticket}\n"
            f"Amount: £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}\n"
            "Response: Seller can now send goods/services and confirm below when done 👇",
            reply_markup=create_buttons([("I've sent the goods/services ✅", "seller_sent_goods")])
        )
    else:
        escrow["status"] = "payment_not_received"
        escrow["buyer_confirmed"] = False

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"❌ Status: Payment Not Received\n🎟️ Ticket: {ticket}\nAction: Payment was not received."
        )

        await context.bot.send_message(
            escrow["group_id"],
            f"❌ Status: Payment Not Received\n🎟️ Ticket: {ticket}\n"
            "Response: Payment has not yet been received. Please wait while we confirm this transaction."
        )

# ---------------- COMMAND HANDLERS ----------------
async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id not in escrows:
        create_new_escrow(chat_id)
    escrow = escrows[chat_id]
    await update.message.reply_text(
        f"Both select your role to start escrow 👇\n\nTicket: {escrow['ticket']}",
        reply_markup=create_buttons([("Join as Buyer 💷", "join_buyer"), ("Join as Seller 📦", "join_seller")])
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    data = query.data

    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = create_new_escrow(chat_id)

    # ---------------- Join Buyer ----------------
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"Status: Buyer Joined 💷\n🎟️ Ticket: {escrow['ticket']}\nAction: @{query.from_user.username} joined as Buyer\nResponse: Waiting for Seller 📦"
        )
        await query.message.edit_reply_markup(create_buttons([("Cancel ❌", "cancel_escrow")]))
        await update_buttons_for_payment_status(context, escrow, buyer_paid=False)

    # ---------------- Join Seller ----------------
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"Status: Seller Joined 📦\n🎟️ Ticket: {escrow['ticket']}\nAction: @{query.from_user.username} joined as Seller\nResponse: Waiting for Buyer 💷"
        )
        await query.message.edit_reply_markup(create_buttons([("Cancel ❌", "cancel_escrow")]))
        await update_buttons_for_payment_status(context, escrow, buyer_paid=False)

    # ---------------- Buyer Marks as Paid ----------------
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        await query.message.reply_text(
            f"⏳ Status: Awaiting Payment\n🎟️ Ticket: {escrow['ticket']}\nAmount: £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}\nResponse: Please wait whilst we confirm this transaction on our network..."
        )
        await update_buttons_for_payment_status(context, escrow, buyer_paid=True)

        # Notify admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"⏳ Status: Awaiting Payment Confirmation\n🎟️ Ticket: {escrow['ticket']}\nBuyer: @{query.from_user.username}\nAmount: £{escrow['fiat_amount']} / {escrow['crypto_amount']} {escrow['crypto']}\nConfirm?",
            reply_markup=create_buttons([("Yes ✅", f"payment_received_{escrow['ticket']}"), ("No ❌", f"payment_not_received_{escrow['ticket']}")])
        )

    # ---------------- Seller Sent Goods ----------------
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True
        escrow["status"] = "awaiting_buyer_confirmation"
        await query.message.reply_text(
            f"Status: Seller Has Sent Goods 📦\n🎟️ Ticket: {escrow['ticket']}\nAction: Seller has sent the goods/services."
        )
        await query.message.edit_reply_markup(create_buttons([("Confirm Goods Received ✅", "buyer_received_goods")]))

    # ---------------- Buyer Confirms Goods Received ----------------
    if data == "buyer_received_goods" and user_id == escrow["buyer_id"]:
        escrow["goods_received"] = True
        escrow["status"] = "escrow_complete"
        await query.message.reply_text(
            f"🎉 Status: Escrow Complete 🎉\n🎟️ Ticket: {escrow['ticket']}\nAction: Buyer has confirmed receipt of goods/services."
        )
        await query.message.edit_reply_markup(create_buttons([]))  # clear buttons

# ---------------- MAIN ----------------
def main():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", escrow_command))
    application.add_handler(CallbackQueryHandler(button_callback))

    application.run_polling()

if __name__ == "__main__":
    main()
