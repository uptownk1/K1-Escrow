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
        "goods_sent": False,  # Track if seller has sent goods
        "goods_received": False,  # Track if buyer has received goods
        "disputed": False,  # Track if trade is disputed
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
        response.raise_for_status()  # Raise an HTTPError for bad responses
        data = response.json()
        price = data.get(coingecko_symbol, {}).get(FIAT_CURRENCY)
        if price is None:
            logging.error(f"Price for {symbol} not found in the response.")
            return None
        return price
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching crypto price for {symbol}: {e}")
        return None
    except ValueError:
        logging.error(f"Error parsing response for {symbol}")
        return None

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖Welcome To K1 Escrow Bot!🤖\n"
        "1) Add this bot into group with buyer+seller ✍️\n"
        "2) /escrow to start new trade 🫡"
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

# ---------------- CALLBACK HANDLER (ADMIN FIRST!) ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    logging.info(f"[ADMIN HANDLER TRIGGERED] Callback data: {data}")
    await query.answer()

    # Format: payment_received_ABC12345 or payment_not_received_ABC12345
    if "_" not in data:
        return

    try:
        _, status_word, ticket = data.split("_")
    except ValueError:
        logging.warning("Invalid admin callback format")
        return

    payment_ok = status_word == "received"

    # Find escrow by ticket
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        logging.warning(f"No escrow found for ticket {ticket}")
        return

    if payment_ok:
        # Payment confirmed
        escrow["status"] = "payment_confirmed"
        escrow["buyer_confirmed"] = payment_ok

        # Notify admin group
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Admin confirmed payment for Escrow {ticket}: RECEIVED."
        )

        # Notify trade group (buyer and seller)
        await context.bot.send_message(
            escrow["group_id"],
            "Status: In Escrow 🔐\nAction: Payment of £{amount} / {crypto_amount} {crypto} received ✅ "
            "Action: Seller can now send the buyer the goods/services, and press below to confirm when done 👇",
            reply_markup=create_buttons([
                ("I've sent the goods/services ✅", "seller_sent_goods")
            ])
        )
    else:
        # Payment not received
        escrow["status"] = "payment_not_received"
        escrow["buyer_confirmed"] = False

        # Notify admin group
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Admin confirmed payment for Escrow {ticket}: NOT RECEIVED."
        )

        # Notify trade group (buyer and seller) that payment is not confirmed
        await context.bot.send_message(
            escrow["group_id"],
            "Payment has not yet been received in escrow. You will receive an update when payment is confirmed.",
            reply_markup=create_buttons([
                ("Cancel Escrow", "cancel_escrow")
            ])
        )

# ---------------- CALLBACK HANDLER (ALL OTHER BUTTONS) ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data

    logging.info(f"[MAIN HANDLER] Callback data: {data} from {username} in {chat_id}")

    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = create_new_escrow(chat_id)

    # Cancel Escrow
    if data == "cancel_escrow":
        escrows.pop(chat_id, None)
        await query.message.reply_text("Escrow cancelled. Use /escrow to start again.")
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']} was cancelled in group {chat_id}."
        )
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"Status: New Trade\nAction: @{username} joined as Buyer\nResponse: Waiting for Seller.\nTicket Number: {escrow['ticket']}🎟️"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Status: New Trade\nAction:Buyer @{username} joined escrow Ticket Number: {escrow['ticket']} 🎟️"
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"Status: New Trade\nAction: @{username} joined as Seller\nResponse: Waiting for Buyer.\nTicket Number: {escrow['ticket']}🎟️"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Status: New Trade\nAction: Seller @{username} joined escrow\n Ticket Number: {escrow['ticket']}🎟️"
        )

    # Both joined
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(
            chat_id,
            "Status: Both parties joined ✅ \n"
            "\n"
            "Action: Buyer, select payment method 👇",
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
            f"Status: Payment Method\nPayment Method: {crypto}. \nNow type the amount in GBP using /amount\n (E.G /amount 100)"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Buyer paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"

        await query.message.reply_text("Status: Awaiting Payment ⏳\nAction: Buyer payment marked as sent.\nResponse: Please wait whilst we check for the transaction on our network...")

        # Remove Cancel button, add Dispute button
        await query.message.edit_reply_markup(create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Dispute this trade 🛑", "dispute_trade")
        ]))

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Buyer @{username} marked payment sent for Escrow {escrow['ticket']}.\nConfirm?",
            reply_markup=create_buttons([
                ("Yes", f"payment_received_{escrow['ticket']}"),
                ("No", f"payment_not_received_{escrow['ticket']}")
            ])
        )

    # Seller has sent goods
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True
        escrow["status"] = "awaiting_buyer_confirmation"

        await query.message.reply_text("Status: In Escrow 🔐\nAction: Seller has marked the goods/services as sent 📦 "
                                      )
        await context.bot.send_message(
            escrow["group_id"],
            "Status: In Escrow 🔐\nAction: If happy with order you received, press to release funds below 👇"
            "Response: If not happy with trade, dispute below for manual review 🛑",
            reply_markup=create_buttons([
                ("I've received the goods/services ✅", "buyer_received_goods"),
                ("Dispute this trade ❌", "dispute_trade")
            ])
        )

    # Dispute the trade
    if data == "dispute_trade":
        escrow["disputed"] = True
        escrow["status"] = "disputed"

        # Notify the buyer and seller that the trade has been disputed
        await query.message.reply_text(
            "You've raised a dispute for this trade. Please add admin @uptownk1 to this group chat to resolve the issue."
        )

        # Notify the admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']} has been disputed by {'Buyer' if user_id == escrow['buyer_id'] else 'Seller'}. "
            "Please review the case."
        )

        # Optionally, you can remove the dispute button after it’s pressed so no more disputes can be raised:
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

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
        await update.message.reply_text("Unable to fetch the price for the selected cryptocurrency. Try again later.")
        return

    # Calculate crypto amount and round to 8 decimal places
    crypto_amount = round(amount / price, 8)

    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"

    wallet = ESCROW_WALLETS.get(crypto)

    await update.message.reply_text(
        f"Status: Awaiting Payment ⏳\n"
        f"Amount 💷: £{amount}\n"
        f"Amount In {crypto} 🪙:  {crypto_amount} {crypto}\n"
        "\n"
        f"Send exact amount to:\n\n{wallet}\n\n"
        "Tap on wallet to copy 📋"
        "Confirm below when sent 👇",
        reply_markup=create_buttons([
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow")
        ])
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    app = ApplicationBuilder().token(TOKEN).build()

    # 🔥 ORDER MATTERS — ADMIN FIRST!!!
    app.add_handler(CallbackQueryHandler(
        handle_admin_payment_confirmation,
        pattern=r"^payment_(received|not_received)_[A-Z0-9]+$"
    ))

    # Then all other callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
