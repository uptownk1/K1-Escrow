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
    }
    escrows[chat_id] = escrow
    return escrow

def create_escrow_buttons(escrow):
    buttons = []
    if not escrow["buyer_id"]:
        buttons.append([InlineKeyboardButton("Join as Buyer", callback_data="join_buyer")])
    if not escrow["seller_id"]:
        buttons.append([InlineKeyboardButton("Join as Seller", callback_data="join_seller")])
    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel_escrow")])
    return InlineKeyboardMarkup(buttons)

def create_buttons(items):
    """Generic button creator: items = list of tuples (text, callback_data)"""
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items])

def get_crypto_price(symbol):
    # Mapping user-friendly symbol to CoinGecko ID
    symbol_mapping = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "LTC": "litecoin",
        "SOL": "solana"
    }

    # Convert symbol to lowercase and map to CoinGecko ID
    coingecko_symbol = symbol_mapping.get(symbol.upper())
    if not coingecko_symbol:
        logging.error(f"Unsupported cryptocurrency symbol: {symbol}")
        return None

    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_symbol}&vs_currencies={FIAT_CURRENCY}"
        response = requests.get(url)
        response.raise_for_status()  # Raise an error if the response code is not 2xx
        data = response.json()
        return data[coingecko_symbol][FIAT_CURRENCY]
    except Exception as e:
        logging.error(f"Error fetching crypto price for {symbol}: {e}")
        return None

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Thank you for using K1 Escrow Bot!\n\n"
        "Add this bot into a group with the Buyer and Seller, then type /escrow to start a trade."
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

# ---------------- CALLBACKS ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Answer the callback query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data

    # Log to check the callback data
    logging.info(f"Callback data received: {data} from user: {username} (ID: {user_id}) in chat: {chat_id}")

    # Retrieve the escrow for the current chat
    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = create_new_escrow(chat_id)

    # --- Cancel Escrow ---
    if data == "cancel_escrow":
        if escrow:
            escrows.pop(chat_id)
        await query.message.reply_text(
            "Escrow has been closed, use /escrow to open a new trade."
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']} in group {chat_id} was cancelled."
        )
        return

    # --- Join Buyer ---
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"You have joined as Buyer. Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']}: Buyer @{username} joined group {chat_id}."
        )

    # --- Join Seller ---
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"You have joined as Seller. Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))
        await context.bot.send_message(
            ADMIN_GROUP_ID, f"Escrow {escrow['ticket']}: Seller @{username} joined group {chat_id}."
        )

    # --- Both parties joined ---
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(
            chat_id,
            f"Both parties have joined successfully! Escrow Ticket: {escrow['ticket']}\n\n"
            "Buyer, please select a cryptocurrency:",
            reply_markup=create_buttons([
                ("BTC", "crypto_BTC"),
                ("ETH", "crypto_ETH"),
                ("LTC", "crypto_LTC"),
                ("SOL", "crypto_SOL")
            ])
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']} started in group {chat_id} with Buyer ID {escrow['buyer_id']} "
            f"and Seller ID {escrow['seller_id']}."
        )

    # --- Crypto selection ---
    if data.startswith("crypto_") and escrow["status"] == "crypto_selection" and user_id == escrow["buyer_id"]:
        crypto = data.split("_")[1]  # Extract the selected cryptocurrency
        escrow["crypto"] = crypto  # Save the selected crypto
        escrow["status"] = "awaiting_amount"  # Update the status

        # Log the selected cryptocurrency
        logging.info(f"Buyer {username} selected {crypto} for Escrow {escrow['ticket']}.")

        await query.message.reply_text(
            f"Crypto selected: {crypto}. Please use the /amount <amount> command to specify the GBP amount you want to pay."
        )

        # Update the admin group about the crypto selection
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Escrow {escrow['ticket']}: Buyer @{username} selected crypto {crypto}."
        )

        # Send the updated reply markup with the selected crypto
        await query.message.edit_reply_markup(reply_markup=create_escrow_buttons(escrow))

    # --- Buyer Paid ---
    if data == "buyer_paid" and escrow["status"] == "awaiting_payment" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        await query.message.reply_text(
            "Buyer marked as paid, please wait for the admin to confirm payment."
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Buyer @{username} has marked as paid for Escrow {escrow['ticket']}. "
            "Please confirm if the payment was received.",
            reply_markup=create_buttons([("Yes", "payment_received"), ("No", "payment_not_received")])
        )

    # --- Admin confirmation for payment ---
    if data == "payment_received" or data == "payment_not_received":
        # Admin response (Yes or No)
        payment_received = data == "payment_received"
        escrow["status"] = "payment_confirmed" if payment_received else "payment_denied"

        # Respond back to the escrow group
        confirmation_message = (
            f"Admin has {'confirmed' if payment_received else 'denied'} the payment for Escrow {escrow['ticket']}.\n"
            f"{'Payment is confirmed, proceed with the trade.' if payment_received else 'Payment has not been confirmed, please resolve the issue.'}"
        )
        
        # Send confirmation to relevant escrow group
        await context.bot.send_message(
            chat_id,
            confirmation_message
        )

        # Log to admin group
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Admin confirmed payment for Escrow {escrow['ticket']}: {'Received' if payment_received else 'Not received'}."
        )

# ---------------- MESSAGE HANDLERS ----------------

# Handle /amount command
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()

    logging.info(f"Received message: {text} from user: {user_id}")

    # Ensure that escrow exists
    escrow = escrows.get(chat_id)
    if not escrow:
        logging.warning(f"No escrow found for chat_id {chat_id}.")
        await update.message.reply_text("No active escrow found. Please use /escrow to start a new trade.")
        return

    # Ensure the status is "awaiting_amount" and that the buyer is sending the amount
    if escrow["status"] != "awaiting_amount" or escrow["buyer_id"] != user_id:
        logging.warning(f"Escrow {escrow['ticket']} not in awaiting_amount state or user {user_id} is not the buyer.")
        await update.message.reply_text("Please make sure you are the buyer and in the correct state.")
        return

    try:
        # Extract the fiat amount (assuming input format is `/amount <amount>`)
        fiat_amount = float(text.split()[1])
        logging.info(f"Amount extracted: {fiat_amount} GBP.")
    except (IndexError, ValueError):
        logging.warning(f"Invalid amount input from {user_id}: {text}")
        await update.message.reply_text("Please enter a valid amount after the /amount command (e.g., /amount 50).")
        return

    # Fetch the price for the selected cryptocurrency
    crypto_symbol = escrow.get("crypto")
    if not crypto_symbol:
        logging.error(f"Crypto symbol not set for escrow {escrow['ticket']}.")
        await update.message.reply_text("No cryptocurrency selected. Please select one first.")
        return

    logging.info(f"Fetching price for {crypto_symbol}.")
    price = get_crypto_price(crypto_symbol)

    # If price fetching fails
    if not price:
        logging.error(f"Failed to fetch crypto price for {crypto_symbol}.")
        await update.message.reply_text("Error fetching crypto price. Please try again later.")
        return

    # Calculate the crypto amount
    crypto_amount = round(fiat_amount / price, 8)
    logging.info(f"Calculated crypto amount: {crypto_amount} {crypto_symbol}.")

    # Update the escrow with the amount and status
    escrow["fiat_amount"] = fiat_amount
    escrow["crypto_amount"] = crypto_amount
    escrow["status"] = "awaiting_payment"

    # Get the wallet address for the selected cryptocurrency
    wallet_address = ESCROW_WALLETS.get(crypto_symbol)
    if not wallet_address:
        logging.error(f"Wallet address not found for {crypto_symbol}.")
        await update.message.reply_text(f"Sorry, we do not have a wallet address for {crypto_symbol}.")
        return

    # Send the response to the buyer
    await update.message.reply_text(
        f"Amount: £{fiat_amount} (~{crypto_amount} {crypto_symbol}) has been registered for this escrow.\n\n"
        f"Please send the crypto to the following wallet address:\n\n"
        f"{wallet_address}\n\nOnce you have sent the payment, press 'I’ve Paid' below to confirm.",
        reply_markup=create_buttons([
            ("I’ve Paid", "buyer_paid"),
            ("Cancel", "cancel_escrow")
        ])
    )
    
    # Log to the admin group about the escrow update
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"Escrow {escrow['ticket']} awaiting payment: Buyer @{update.message.from_user.username}, "
        f"Amount: £{fiat_amount} / {crypto_amount} {crypto_symbol}, Wallet: {wallet_address}"
    )

# ---------------- MAIN ----------------

def main():
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("escrow", escrow_command))
    application.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(CallbackQueryHandler(button_callback, pattern="buyer_paid"))

    application.run_polling()

if __name__ == "__main__":
    main()
