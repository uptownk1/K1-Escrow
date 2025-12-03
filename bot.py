import logging
import re
import asyncio
from uuid import uuid4
from datetime import datetime, timedelta

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ---------------- CONFIG ----------------
TOKEN = "YOUR_BOT_TOKEN"
ADMIN_GROUP_ID = -1001234567890  # replace with your admin group ID
ESCROW_WALLETS = {
    "BTC": "your_btc_wallet_address",
    "ETH": "your_eth_wallet_address",
    "LTC": "your_ltc_wallet_address",
    "SOL": "your_sol_wallet_address"
}
FIAT_CURRENCY = "gbp"

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- ESCROW TRACKING ----------------
escrows = {}  # chat_id : escrow dict

# ---------------- HELPERS ----------------
def parse_amount(text: str):
    clean = re.sub(r"[^\d.]", "", text)
    try:
        return float(clean)
    except:
        return None

def get_crypto_price(coin: str):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin.lower()}&vs_currencies={FIAT_CURRENCY}"
    try:
        r = requests.get(url).json()
        return r[coin.lower()][FIAT_CURRENCY]
    except Exception as e:
        logger.error(f"Error fetching price: {e}")
        return None

def create_buttons(buttons):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data)] for text, data in buttons])

def generate_ticket():
    return str(uuid4())[:8]

async def start_escrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = create_buttons([("Join as Buyer", "join_buyer"), ("Join as Seller", "join_seller")])
    await update.message.reply_text("Select your role to start escrow:", reply_markup=buttons)

# ---------------- CALLBACK HANDLER ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    data = query.data

    chat_id = query.message.chat_id
    escrow = escrows.get(chat_id)
    if not escrow:
        escrow = {
            "group_id": chat_id,
            "buyer_id": None,
            "seller_id": None,
            "status": None,
            "crypto": None,
            "fiat_amount": None,
            "crypto_amount": None,
            "wallet_address": None,
            "ticket_number": None,
            "buyer_confirmed": False,
            "seller_confirmed": False,
            "timeout_task": None
        }
        escrows[chat_id] = escrow

    # ---------------- JOIN ----------------
    if data == "join_buyer":
        if escrow["buyer_id"]:
            await query.message.reply_text("Buyer already joined.")
            return
        escrow["buyer_id"] = user_id
        await query.message.reply_text("You joined as Buyer.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"Buyer @{username} joined group {chat_id}.")
    elif data == "join_seller":
        if escrow["seller_id"]:
            await query.message.reply_text("Seller already joined.")
            return
        escrow["seller_id"] = user_id
        await query.message.reply_text("You joined as Seller.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"Seller @{username} joined group {chat_id}.")

    # ---------------- BOTH JOINED ----------------
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"] = "crypto_selection"
        await context.bot.send_message(chat_id,
            "Both parties have joined successfully!\nBuyer, please select a cryptocurrency:",
            reply_markup=create_buttons([("BTC","crypto_BTC"),("ETH","crypto_ETH"),("LTC","crypto_LTC"),("SOL","crypto_SOL")])
        )
        await context.bot.send_message(ADMIN_GROUP_ID,
            f"Escrow started in group {chat_id} with Buyer @{username} and Seller @{username}."
        )

    # ---------------- CRYPTO SELECTION ----------------
    if data.startswith("crypto_"):
        if user_id != escrow["buyer_id"]:
            await query.message.reply_text("Only the buyer can select the crypto.")
            return
        coin = data.split("_")[1]
        escrow["crypto"] = coin
        escrow["status"] = "awaiting_amount"
        await query.message.reply_text(f"You selected {coin}. Please enter the amount in fiat (e.g. 100, £100.00):")

    # ---------------- PAYMENT ----------------
    if data == "i_paid":
        if user_id != escrow["buyer_id"]:
            await query.message.reply_text("Only buyer can mark as paid.")
            return
        escrow["status"] = "awaiting_admin_confirmation"
        await query.message.reply_text("Thank you, please wait whilst we check the transaction on our server...")
        buyer_name = query.from_user.username or query.from_user.first_name
        await context.bot.send_message(ADMIN_GROUP_ID,
            f"Buyer @{buyer_name} claims to have paid {escrow['fiat_amount']} {FIAT_CURRENCY} in {escrow['crypto']} for escrow in group {chat_id}.\nPlease confirm funds received with /funds_received or /funds_not_received."
        )

    if data == "cancel_trade":
        if user_id != escrow["buyer_id"]:
            await query.message.reply_text("Only buyer can cancel trade.")
            return
        escrow["status"] = "cancelled"
        await query.message.reply_text("Escrow has been cancelled.")
        await context.bot.send_message(ADMIN_GROUP_ID, f"Escrow in group {chat_id} has been cancelled by buyer.")

    if data == "dispute":
        ticket = generate_ticket()
        escrow["ticket_number"] = ticket
        await query.message.reply_text(f"You have raised a dispute. Ticket #{ticket}\nPlease add the admin to the group to resolve the issue.")
        await context.bot.send_message(ADMIN_GROUP_ID,
            f"Dispute #{ticket} raised in group {chat_id} by @{username}."
        )

    # ---------------- SELLER PROVIDES GOODS ----------------
    if data == "seller_provided":
        if user_id != escrow["seller_id"]:
            await query.message.reply_text("Only seller can press this button.")
            return
        escrow["status"] = "awaiting_buyer_confirmation"
        await query.message.reply_text(
            "Seller has marked the goods/services as provided. Buyer, please press 'I’ve received my order' within 15 minutes."
        )
        buyer_buttons = create_buttons([("I’ve received my order", "buyer_received"),("Dispute","dispute")])
        await context.bot.send_message(escrow["buyer_id"], "Please confirm receipt:", reply_markup=buyer_buttons)

        # START TIMEOUT
        async def timeout_check():
            await asyncio.sleep(15*60)
            if not escrow.get("buyer_confirmed"):
                ticket = generate_ticket()
                escrow["ticket_number"] = ticket
                await context.bot.send_message(chat_id,
                    f"Buyer did not confirm receipt within 15 minutes. Dispute #{ticket} opened."
                )
                await context.bot.send_message(ADMIN_GROUP_ID,
                    f"Dispute #{ticket} auto-opened due to buyer timeout in group {chat_id}."
                )
        escrow["timeout_task"] = asyncio.create_task(timeout_check())

    # ---------------- BUYER CONFIRMS RECEIPT ----------------
    if data == "buyer_received":
        if user_id != escrow["buyer_id"]:
            await query.message.reply_text("Only buyer can confirm receipt.")
            return
        escrow["buyer_confirmed"] = True
        if escrow["timeout_task"]:
            escrow["timeout_task"].cancel()
        escrow["status"] = "awaiting_seller_wallet"
        await query.message.reply_text("Thank you! Seller, please reply in the group with your wallet address for fund release.")
        # Notify seller to provide wallet
        await context.bot.send_message(escrow["seller_id"], "Please reply in the group with your wallet address (same crypto as buyer paid).")

# ---------------- MESSAGE HANDLER ----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    escrow = escrows.get(chat_id)
    if not escrow:
        return

    # ---------------- AMOUNT ENTRY ----------------
    if escrow["status"] == "awaiting_amount" and user_id == escrow["buyer_id"]:
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text("Invalid amount. Please enter a numeric value (e.g. 100, £100.00).")
            return
        escrow["fiat_amount"] = amount
        coin = escrow["crypto"]
        price = get_crypto_price(coin)
        if price is None:
            await update.message.reply_text("Error fetching live price. Try again later.")
            return
        crypto_amount = round(amount / price, 8)
        escrow["crypto_amount"] = crypto_amount
        escrow["wallet_address"] = ESCROW_WALLETS[coin]
        await update.message.reply_text(
            f"Please send {crypto_amount} {coin} to the escrow wallet below:\n{ESCROW_WALLETS[coin]}\n"
            f"Live rate: £{price} per {coin}",
            reply_markup=create_buttons([("I have paid","i_paid"),("Cancel","cancel_trade"),("Dispute","dispute")])
        )

    # ---------------- SELLER PROVIDES WALLET ----------------
    if escrow["status"] == "awaiting_seller_wallet" and user_id == escrow["seller_id"]:
        wallet = text
        escrow["seller_wallet"] = wallet
        escrow["status"] = "awaiting_admin_release"
        await context.bot.send_message(ADMIN_GROUP_ID,
            f"Escrow in group {chat_id} ready for fund release.\n"
            f"Seller wallet: {wallet}\n"
            f"Amount: {escrow['crypto_amount']} {escrow['crypto']}\n"
            f"Buyer: {escrow['buyer_id']}, Seller: {escrow['seller_id']}\n"
            "Press 'Sent' when funds have been sent.",
            reply_markup=create_buttons([("Sent","admin_sent")])
        )
        await update.message.reply_text(f"Your wallet address has been forwarded to the admin for fund release.")

# ---------------- ADMIN SENT BUTTON ----------------
async def admin_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    # Find escrow by matching admin message text (simplified)
    for e_chat_id, escrow in escrows.items():
        if escrow.get("status") == "awaiting_admin_release":
            if data == "admin_sent":
                escrow["status"] = "completed"
                await context.bot.send_message(e_chat_id,
                    f"Your transaction is now being released to the wallet address provided: {escrow['seller_wallet']}\n"
                    f"Escrow completed successfully! Thank you for using this service."
                )
                await query.message.edit_text("Funds released. Escrow marked as completed.")

# ---------------- MAIN ----------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("escrow", start_escrow))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CallbackQueryHandler(admin_button_callback, pattern="admin_sent"))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
