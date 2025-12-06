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
FEE_RATE = 0.05  # 5% fee
FIAT_SYMBOL = "£"
FIAT_LABEL = "GBP"  # will display as "£amount (GBP)"

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
        "2) /escrow to start a trade\n\n\n"
        "⬇ TERMS+CONDITIONS ⬇/n"
        "Buyer: 🤑\n"
        "- Only send to the escrow wallet provided by the bot\n"
        "- Ensure you send the correct amount\n"
        "- Only open a dispute if you think you are being scammed  or cant resolve between you\n"
        "- If you do not mark goods as received or open a dispute within 30 minutes, funds are released to seller so always be online when in active trade\n"
        "- Optional, but we advise you screen record or screenshot through your trade, incase need of evidence during a dispute\n"

        "Seller: 🤑\n"
        "- Do NOT send any goods/services until transaction is fully confirmed on our network and is marked "Payment Received\n"
        "- If there is any disagreement that can not be resolved, please open a dispute admin is here to help!\n"
        "- Optional, but we advise you screen record or screenshot through your trade, incase need of evidence during a dispute\n\n\n"
        "💰 This escrow service will deduct a 5% escrow fee from buyers deposit. This fee should be agreed between buyer/seller",
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
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {escrow['crypto']}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n📄 Action: Payment confirmed by admin",
            parse_mode="Markdown"
        )
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Payment Confirmed ✅\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {escrow['crypto']}\n"
            "📄 Action: Seller can now send goods/services to buyer\n"
            "👇 Response: Confirm below when done",
            reply_markup=create_buttons([
                ("I've sent the goods/services ✅", "seller_sent_goods")
            ])
        )
        escrow["latest_message_id"] = msg.message_id
    else:
        escrow["status"] = "awaiting_payment"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ❌\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {escrow['crypto']}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n📄 Action: Payment not received",
            parse_mode="Markdown"
        )
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ❌\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n"
            "📄 Response: Payment has not yet been received. You will receive a message once it has confirmed on our system."
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
            f"❌ Escrow {escrow['ticket']} was cancelled."
        )
        await clear_previous_buttons(context, escrow)
        return

    # Join Buyer
    if data == "join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"] = user_id
        await query.message.reply_text(f"🤝 Status: New Trade\n📄 Action: @{username} joined as Buyer 💷\n🎟️ Ticket: {escrow['ticket']}")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID, f"🤝 Status: Buyer Joined\n🎟️ Ticket: {escrow['ticket']}\n👤 Buyer: @{username}")

    # Join Seller
    if data == "join_seller" and not escrow["seller_id"]:
        escrow["seller_id"] = user_id
        await query.message.reply_text(f"🤝 Status: New Trade\n📄 Action: @{username} joined as Seller 📦\n🎟️ Ticket: {escrow['ticket']}")
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
        await query.message.reply_text(f"📄 Action: You selected {crypto} 🪙\n✍️ Response: Type the amount in GBP using: `/amount 100`", parse_mode="Markdown")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # Buyer Paid
    if data == "buyer_paid" and user_id == escrow["buyer_id"]:
        escrow["status"] = "awaiting_admin_confirmation"
        await clear_previous_buttons(context, escrow)
        msg = await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ⏳\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {escrow['crypto']}\n"
            "📄 Response: Please wait whilst we confirm this transaction..."
        )
        escrow["latest_message_id"] = msg.message_id
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Awaiting Payment ⏳\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {escrow['crypto']}\n"
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
        msg_text = (
            f"🎟️ Ticket: {escrow['ticket']}\n📌 Status: Seller marked goods as sent 📦\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{username}\n"
            "📄 Action: Buyer confirm and press **Release Funds**\nNOTE Only open dispute if:\n - You can not resolve it between you!\n- No response from buyer within 30 minutes.\n- You believe you are getting scammed. "
        )
        buttons = create_buttons([("Release Funds ✅", "buyer_release_funds"), ("Dispute ⚠️", "dispute")])
        msg = await context.bot.send_message(chat_id, msg_text, parse_mode="Markdown", reply_markup=buttons)
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
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {coin}\n"
            f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
            "📄 Action: Buyer confirmed goods received. Waiting for sellers wallet."
        )
        await context.bot.send_message(
            chat_id,
            f"🎟️ Ticket: {ticket}\n📌 Status: Awaiting Seller Wallet ⏳\n"
            f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(escrow['fiat_amount'])} ({FIAT_LABEL})\n🪙 Crypto: {fmt_crypto(escrow['crypto_amount'])} {coin}\n"
            f"📄 Action: Buyer confirmed goods were received.\n💬 Response: Seller type /wallet and then paste your {crypto} wallet address\n (E.G /wallet 0x1284k18493btc)",
            parse_mode="Markdown"
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
            ("I've Paid ✅", "buyer_paid"),
            ("Cancel ❌", "cancel_escrow"),
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
    amount_fiat = escrow['fiat_amount'] or 0
    amount_crypto = escrow['crypto_amount'] or 0

    # Fee calculations
    fee_fiat = amount_fiat * FEE_RATE
    payout_fiat = amount_fiat - fee_fiat

    # Notify admin with release button using new compact format:
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n📌 Status: Awaiting Admin Release ⏳\n\n"
        f"💷 Amount Sent: {FIAT_SYMBOL}{fmt_auto(amount_fiat)} ({FIAT_LABEL}) ({fmt_crypto(amount_crypto)} {coin})\n"
        f"💸 Escrow Fee (5%): {FIAT_SYMBOL}{fmt_auto(fee_fiat)} ({FIAT_LABEL})\n"
        f"🏦 Amount After Fee: {FIAT_SYMBOL}{fmt_auto(payout_fiat)} ({FIAT_LABEL})\n\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"👛 Seller Wallet: `{wallet_address}`\n\n📄 Response: Please confirm funds release",
        parse_mode="Markdown",
        reply_markup=create_buttons([("Mark as Sent ✅", f"admin_sent_{ticket}")])
    )

    # Notify escrow group (buyer/seller) with the same compact info (no wallet)
    await update.message.reply_text(
        f"🎟️ Ticket: {ticket}\n📌 Status: Processing Payment...⏳\n\n"
        f"💷 Amount Sending: {FIAT_SYMBOL}{fmt_auto(amount_fiat)} ({FIAT_LABEL}) ({fmt_crypto(amount_crypto)} {coin})\n"
        f"💸 Escrow Fee (5%): {FIAT_SYMBOL}{fmt_auto(fee_fiat)} ({FIAT_LABEL})\n"
        f"🏦 Amount After Fee: {FIAT_SYMBOL}{fmt_auto(payout_fiat)} ({FIAT_LABEL})\n\n"
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
    escrow = next((e for e in escrows.values() if e["ticket"] == ticket), None)
    if not escrow:
        await query.message.reply_text("Escrow not found.")
        return
    chat_id = escrow["group_id"]
    buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
    seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
    amount_fiat = escrow.get('fiat_amount', 0)
    amount_crypto = escrow.get('crypto_amount', 0)
    wallet = escrow.get('wallet_address', 'Not provided')
    coin = escrow.get('crypto', 'N/A')

    fee_fiat = amount_fiat * FEE_RATE
    payout_fiat = amount_fiat - fee_fiat

    await clear_previous_buttons(context, escrow)
    escrow["status"] = "completed"

    # Notify admin final
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n📌 Status: Trade Completed ✅\n\n"
        f"💷 Amount Sent: {FIAT_SYMBOL}{fmt_auto(amount_fiat)} ({FIAT_LABEL}) ({fmt_crypto(amount_crypto)} {coin})\n"
        f"💸 Escrow Fee (5%): {FIAT_SYMBOL}{fmt_auto(fee_fiat)} ({FIAT_LABEL})\n"
        f"🏦 Amount After Fee: {FIAT_SYMBOL}{fmt_auto(payout_fiat)} ({FIAT_LABEL})\n\n"
        "📄 Action: Funds have been released to sellers wallet.",
        parse_mode="Markdown"
    )

    # Notify escrow group final (seller/buyer)
    await context.bot.send_message(
        chat_id,
        f"🎉 Trade Completed!\n\n"
        f"🎟️ Ticket: {ticket}\n"
        f"💷 Amount Released: {FIAT_SYMBOL}{fmt_auto(payout_fiat)} ({FIAT_LABEL})\n"
        f"🪙 Crypto Amount: ({fmt_crypto(amount_crypto - (amount_crypto * FEE_RATE))} {coin})\n"
        f"💸 Escrow Fee Taken: {FIAT_SYMBOL}{fmt_auto(fee_fiat)} ({FIAT_LABEL})\n\n"
        "📄 Response: Funds have successfully been sent to seller.\n\n"
        "🫡 Thank you for using K1 Escrow Bot, see you soon! \n\nYou can now close this group.",
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

    # Remove all previous buttons immediately to pause escrow
    await clear_previous_buttons(context, escrow)

    # Only participants can open a dispute
    if user_id not in [escrow.get("buyer_id"), escrow.get("seller_id")]:
        await query.message.reply_text("Only participants can open a dispute.")
        return

    if escrow.get("disputed"):
        await query.message.reply_text("Dispute already open. Please wait for admin.")
        return

    escrow["disputed"] = True
    escrow["status"] = "disputed"
    ticket = escrow["ticket"]
    buyer_username = (await context.bot.get_chat_member(chat_id, escrow['buyer_id'])).user.username
    seller_username = (await context.bot.get_chat_member(chat_id, escrow['seller_id'])).user.username
    amount = escrow.get("fiat_amount", "N/A")
    crypto_amount = escrow.get("crypto_amount", 0)
    coin = escrow.get("crypto", "N/A")

    # Notify group that a dispute has been raised
    await context.bot.send_message(
        chat_id,
        f"🎟️ Ticket: {ticket}\n📌 Status: Trade Disputed ⚠️\n"
        f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(amount) if isinstance(amount, (int, float)) else amount} ({FIAT_LABEL}) ({fmt_crypto(crypto_amount)} {coin})\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"📄 Action: Trade disputed by @{username}. Escrow is now paused. Please wait for admin to review.",
        parse_mode="Markdown"
    )

    # Notify admin with instructions for manual review
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n📌 Status: Trade Disputed ⚠️\n"
        f"💷 Amount: {FIAT_SYMBOL}{fmt_auto(amount) if isinstance(amount, (int, float)) else amount} ({FIAT_LABEL}) ({fmt_crypto(crypto_amount)} {coin})\n"
        f"👤 Buyer: @{buyer_username}\n👤 Seller: @{seller_username}\n"
        f"📄 Action: Dispute opened by @{username}. "
        "Bot cannot generate an invite link. Please ask a participant to provide a manual invite link or add you to the group to review.",
        parse_mode="Markdown"
    )

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    app = ApplicationBuilder().token(TOKEN).build()

    # Order matters — specific handlers before generic.
    app.add_handler(CallbackQueryHandler(admin_sent_callback, pattern=r"^admin_sent_.*$"))
    app.add_handler(CallbackQueryHandler(handle_admin_payment_confirmation, pattern=r"^payment_(received|notreceived)_.*$"))
    app.add_handler(CallbackQueryHandler(dispute_callback, pattern=r"^dispute$"))
    app.add_handler(CallbackQueryHandler(button_callback))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("wallet", wallet_command))

    app.add_handler(MessageHandler(filters.Regex(r'^/amount \d+(\.\d+)?$'), handle_amount))

    app.run_polling()

if __name__ == "__main__":
    main()
