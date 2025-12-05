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
        buttons.append([InlineKeyboardButton("Join as Buyer", callback_data="join_buyer")])
    if not escrow["seller_id"]:
        buttons.append([InlineKeyboardButton("Join as Seller", callback_data="join_seller")])
    
    # Only show Cancel button if the Buyer has not yet marked as paid
    if escrow["status"] != "awaiting_admin_confirmation":
        buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel_escrow")])
    
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
        "🤖Welcome to K1 Escrow Bot!🤖\n"
         "NOTE: This bot deducts a 5% fee from each trade ₿\n"
        "/rules for rules+terms/conditions 🫡\n"
        "1) Make a group with Buyer, Seller and Bot ✅\n"
        "2) Run /escrow in group to begin a trade 📦\n"
    )

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id not in escrows:
        create_new_escrow(chat_id)
    escrow = escrows[chat_id]
    await update.message.reply_text(
        "Select your role to start escrow:",
        reply_markup=create_escrow_buttons(escrow)
    )

# ---------------- CALLBACK HANDLER (ADMIN FIRST!) ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    logging.info(f"[ADMIN HANDLER TRIGGERED] Callback data: {data}")
    await query.answer()

    # Ensure data has the correct format: 'payment_received_<ticket>' or 'payment_not_received_<ticket>'
    if data.startswith("payment_"):
        try:
            _, status_word, ticket = data.split("_")
        except ValueError:
            logging.warning("Invalid admin callback format")
            return

        # Find the escrow by ticket
        escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
        if not escrow:
            logging.warning(f"No escrow found for ticket {ticket}")
            return

        # Determine whether the payment was received or not
        if status_word == "received":
            payment_ok = True
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
                "This payment has been confirmed and is currently held safely in escrow. ✅"
                "Seller can now safely send the goods/services, and press below to confirm when done. ⏳",
                reply_markup=create_buttons([
                    ("I've sent the goods/services", "seller_sent_goods")
                ])
            )

        elif status_word == "not_received":
            payment_ok = False
            escrow["status"] = "payment_not_received"
            escrow["buyer_confirmed"] = payment_ok

            # Notify admin group
            await context.bot.send_message(
                ADMIN_GROUP_ID,
                f"Admin confirmed payment for Escrow {ticket}: NOT RECEIVED."
            )

            # Notify trade group (buyer and seller) that payment is not confirmed
            await context.bot.send_message(
                escrow["group_id"],
                "Status: Funds Not Received. ❌\nThe bot will update shortly when payment has been received into escrow. ⏳",
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
        await query.message.reply_text("Escrow cancelled.✅\n Use /escrow to start again.💯")
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']} 🎟️ \n Status: Cancelled by @{username}❌ \nGroup ID:{chat_id}.🆔"
        )
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(
            f"Escrow Ticket: {escrow['ticket']}.🎟️\n @{username} has joined as Buyer.\nWaiting for Seller.⏳"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']}.🎟️ \nStatus: Buyer @{username} has joined.\n Trade Type: New"
        )

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(
            f"Escrow Ticket: {escrow['ticket']}.🎟️\n @{username} has joined as Seller.\nWaiting for Buyer.⏳"
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow Ticket: {escrow['ticket']}.🎟️ \nStatus: Seller @{username} has joined.\n Trade Type: New"
        )

    # Both joined
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(
            chat_id,
            "Both parties joined. Buyer, select the crypto:",
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
            f"You selected {crypto}. Now send the amount in GBP using /amount eg /amount 100."
        )
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Buyer paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"

        await query.message.reply_text("Payment marked as sent. Please wait, checking if payment has been received into escrow...")

        # Remove Cancel button, add Dispute button
        await query.message.edit_reply_markup(create_buttons([
            ("✅ I've Paid ✅", "buyer_paid"),
            ("⛔️ Dispute Trade ⛔️", "dispute_trade")
        ]))

        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Buyer @{username} marked payment sent Escrow Ticket: {escrow['ticket']} 🎟️\nConfirm?",
            reply_markup=create_buttons([
                ("Yes ✅", f"payment_received_{escrow['ticket']}"),
                ("No ❌", f"payment_not_received_{escrow['ticket']}")
            ])
        )

    # Seller has sent goods
    if data == "seller_sent_goods" and user_id == escrow["seller_id"]:
        escrow["goods_sent"] = True
        escrow["status"] = "awaiting_buyer_confirmation"

        await query.message.reply_text("Seller has marked the goods/services as sent 📤"
                                      )
        await context.bot.send_message(
            escrow["group_id"],
            "Buyer, when received the goods/services press received below 👇"
            "If you're not happy, dispute this trade below and an admin will review manually.",
            reply_markup=create_buttons([
                ("✅ Received ✅", "buyer_received_goods"),
                ("⛔️ Dispute Trade ⛔️", "dispute_trade")
            ])
        )

    # Dispute the trade
    if data == "dispute_trade":
        escrow["disputed"] = True
        escrow["status"] = "disputed"

        # Notify the buyer and seller that the trade has been disputed
        await query.message.reply_text(
            "@{username} rasied a dispute, escrow is now paused. Add admin @uptownk1 to this group chat to resolve the issue."
        )

        # Notify the admin
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow Ticket: {escrow['ticket']}🎟️\n Status: Disputed by: {'Buyer' if user_id == escrow['buyer_id'] else 'Seller'}. "
            "Waiting for resolution."
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
        await update.message.reply_text("Unable to fetch the live price for the selected cryptocurrency. use /escrow to cancel trade and try again.")
        return

    # Calculate crypto amount and round to 8 decimal places
    crypto_amount = round(amount / price, 8)

    escrow["fiat_amount"] = amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"

    wallet = ESCROW_WALLETS.get(crypto)

    await update.message.reply_text(
        f"£{amount} \n {crypto}: {crypto_amount} \n"
        f"Send exact amount to:\n{wallet}\n\n"
        "Press 'I've Paid' once complete.",
        reply_markup=create_buttons([
            ("✅ I've Paid ✅", "buyer_paid"),
            ("❌ Cancel ❌", "cancel_escrow")
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
