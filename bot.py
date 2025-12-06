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
FIAT_LABEL = "GBP"

# ---------------- DATA ----------------
escrows = {}        # ticket -> escrow dict
group_escrows = {}  # chat_id -> list of tickets

# ---------------- HELPERS ----------------
def create_new_escrow(chat_id):
    ticket = str(uuid4())[:8].upper()
    escrow = {
        "group_id": chat_id,
        "ticket": ticket,
        "buyer_id": None,
        "seller_id": None,
        "status": None,
        "crypto": None,
        "fiat_amount": None,
        "crypto_amount": None,
        "wallet_address": None,
        "buyer_confirmed": False,
        "seller_confirmed": False,
        "goods_sent": False,
        "goods_received": False,
        "disputed": False,
        "latest_message_id": None
    }
    escrows[ticket] = escrow
    group_escrows.setdefault(chat_id, []).append(ticket)
    return escrow

def create_escrow_buttons(escrow):
    buttons = []
    ticket = escrow["ticket"]
    if not escrow["buyer_id"]:
        buttons.append([InlineKeyboardButton("Join as Buyer 💷", callback_data=f"join_buyer_{ticket}")])
    if not escrow["seller_id"]:
        buttons.append([InlineKeyboardButton("Join as Seller 📦", callback_data=f"join_seller_{ticket}")])
    if escrow["status"] is None:
        buttons.append([InlineKeyboardButton("Cancel ❌", callback_data=f"cancel_escrow_{ticket}")])
    return InlineKeyboardMarkup(buttons)

def create_buttons(items):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=cb)] for text, cb in items])

def get_crypto_price(symbol):
    symbol_mapping = {"BTC":"bitcoin","ETH":"ethereum","LTC":"litecoin","SOL":"solana"}
    coingecko_symbol = symbol_mapping.get(symbol.upper())
    if not coingecko_symbol: return None
    try:
        data = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_symbol}&vs_currencies={FIAT_CURRENCY}").json()
        return data.get(coingecko_symbol, {}).get(FIAT_CURRENCY)
    except: return None

def fmt_auto(number):
    try: n=float(number)
    except: return str(number)
    return f"{int(round(n))}" if abs(n-round(n))<1e-9 else f"{n:.2f}"

def fmt_crypto(number):
    try: n=float(number)
    except: return str(number)
    s=f"{n:.8f}".rstrip('0').rstrip('.')
    if '.' not in s: return s
    dec=s.split('.',1)[1]
    if len(dec)==1: return f"{s}0"
    return s

async def clear_previous_buttons(context: ContextTypes.DEFAULT_TYPE, escrow: dict):
    if escrow.get("latest_message_id"):
        try:
            await context.bot.edit_message_reply_markup(chat_id=escrow["group_id"], message_id=escrow["latest_message_id"], reply_markup=None)
        except: pass

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Welcome To K1 Escrow Bot! 🤖\n1) Add bot to group\n2) /escrow to start a trade")

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id=update.message.chat_id
    escrow=create_new_escrow(chat_id)
    await update.message.reply_text(f"New Escrow 🎟️ Ticket: {escrow['ticket']}\nSelect role 👇", reply_markup=create_escrow_buttons(escrow))

# ---------------- CALLBACK HANDLERS ----------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    chat_id=query.message.chat.id
    user_id=query.from_user.id
    username=query.from_user.username or query.from_user.first_name
    data=query.data

    parts=data.rsplit("_",1)
    action=parts[0]
    ticket=parts[1] if len(parts)>1 else None
    escrow=escrows.get(ticket)
    if not escrow:
        await query.message.reply_text("Escrow not found."); return

    # ---------------- Cancel ----------------
    if action=="cancel_escrow":
        if escrow["status"] not in [None,"crypto_selection","awaiting_amount"]:
            await query.message.reply_text("⛔ Cannot cancel escrow at this stage."); return
        escrows.pop(ticket); group_escrows[chat_id].remove(ticket)
        await query.message.reply_text(f"Escrow {ticket} cancelled."); await clear_previous_buttons(context, escrow); return

    # ---------------- Join Buyer ----------------
    if action=="join_buyer" and not escrow["buyer_id"]:
        escrow["buyer_id"]=user_id
        await query.message.reply_text(f"🤝 @{username} joined as Buyer 💷\n🎟️ Ticket: {ticket}")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID,f"🤝 Buyer Joined\n🎟️ Ticket: {ticket}\n👤 Buyer: @{username}")

    # ---------------- Join Seller ----------------
    if action=="join_seller" and not escrow["seller_id"]:
        escrow["seller_id"]=user_id
        await query.message.reply_text(f"🤝 @{username} joined as Seller 📦\n🎟️ Ticket: {ticket}")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))
        await context.bot.send_message(ADMIN_GROUP_ID,f"🤝 Seller Joined\n🎟️ Ticket: {ticket}\n👤 Seller: @{username}")

    # ---------------- Both Joined -> Crypto ----------------
    if escrow["buyer_id"] and escrow["seller_id"] and escrow["status"] is None:
        escrow["status"]="crypto_selection"
        msg=await context.bot.send_message(chat_id,
            f"🎟️ Ticket: {ticket}\nBoth joined ✅\nBuyer select payment 👇",
            reply_markup=create_buttons([(c,f"crypto_{c}_{ticket}") for c in ["BTC","ETH","LTC","SOL"])]
        ); escrow["latest_message_id"]=msg.message_id

    # ---------------- Crypto ----------------
    if action.startswith("crypto") and user_id==escrow["buyer_id"]:
        crypto=action.split("_")[1]; escrow["crypto"]=crypto; escrow["status"]="awaiting_amount"
        await context.bot.send_message(ADMIN_GROUP_ID,f"🎟️ Ticket: {ticket}\nCrypto selected: {crypto}\n👤 Buyer: @{username}")
        await query.message.reply_text(f"Select amount: `/amount {ticket} 100`",parse_mode="Markdown")
        await query.message.edit_reply_markup(create_escrow_buttons(escrow))

    # ---------------- Seller Sent Goods ----------------
    if action=="seller_sent_goods" and user_id==escrow["seller_id"]:
        escrow["goods_sent"]=True; escrow["status"]="awaiting_buyer_action"
        buyer_username=(await context.bot.get_chat_member(chat_id,escrow['buyer_id'])).user.username
        msg_text=(f"🎟️ Ticket: {ticket}\nSeller sent goods 📦\nBuyer confirm or dispute")
        buttons=create_buttons([("Release Funds ✅",f"buyer_release_funds_{ticket}"),("Dispute ⚠️",f"dispute_{ticket}")])
        msg=await context.bot.send_message(chat_id,msg_text,parse_mode="Markdown",reply_markup=buttons)
        escrow["latest_message_id"]=msg.message_id

# ---------------- AMOUNT HANDLER ----------------
async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text=update.message.text.strip(); chat_id=update.message.chat_id; user_id=update.message.from_user.id
    try: _, ticket, amount_text=text.split(); amount=float(amount_text)
    except: await update.message.reply_text("Format: /amount <ticket> 50"); return
    escrow=escrows.get(ticket)
    if not escrow: await update.message.reply_text("Escrow not found."); return
    if escrow["buyer_id"]!=user_id or escrow["status"]!="awaiting_amount":
        await update.message.reply_text("Cannot set amount now."); return
    crypto=escrow["crypto"]; price=get_crypto_price(crypto)
    if price is None: await update.message.reply_text("Cannot fetch price"); return
    crypto_amount=round(amount/price,8); escrow["fiat_amount"]=amount; escrow["crypto_amount"]=crypto_amount; escrow["status"]="awaiting_payment"
    wallet=ESCROW_WALLETS.get(crypto)
    await clear_previous_buttons(context,escrow)
    await update.message.reply_text(
        f"🎟️ Ticket: {ticket}\nSend {fmt_crypto(crypto_amount)} {crypto} to wallet `{wallet}`\nMark as paid",
        parse_mode="Markdown", reply_markup=create_buttons([("I've Paid ✅",f"buyer_paid_{ticket}"),("Cancel ❌",f"cancel_escrow_{ticket}")])
    )

# ---------------- WALLET ----------------
async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id=update.message.chat_id; user_id=update.message.from_user.id
    text=update.message.text.strip()
    try: _, ticket, wallet=text.split(maxsplit=2)
    except: await update.message.reply_text("Use: /wallet <ticket> <address>"); return
    escrow=escrows.get(ticket)
    if not escrow: await update.message.reply_text("Escrow not found."); return
    if escrow.get("status")!="awaiting_seller_wallet": await update.message.reply_text("Cannot set wallet now."); return
    if user_id!=escrow.get("seller_id"): await update.message.reply_text("Only seller can set wallet."); return
    escrow["wallet_address"]=wallet
    amount_fiat=escrow['fiat_amount']; amount_crypto=escrow['crypto_amount']; coin=escrow['crypto']
    fee=amount_fiat*FEE_RATE; payout=amount_fiat-fee
    buyer_username=(await context.bot.get_chat_member(chat_id,escrow['buyer_id'])).user.username
    seller_username=(await context.bot.get_chat_member(chat_id,escrow['seller_id'])).user.username
    await context.bot.send_message(ADMIN_GROUP_ID,
        f"🎟️ Ticket: {ticket}\n💷 {fmt_auto(amount_fiat)} ({FIAT_LABEL}) ({fmt_crypto(amount_crypto)} {coin})\n💸 Fee: {fmt_auto(fee)}\nSend To Seller: {fmt_auto(payout)}\nWallet: `{wallet}`",
        parse_mode="Markdown", reply_markup=create_buttons([("Mark as Sent ✅",f"admin_sent_{ticket}")])
    )
    await update.message.reply_text(f"🎟️ Ticket: {ticket}\nProcessing payment...")

# ---------------- ADMIN CONFIRM PAYMENT ----------------
async def handle_admin_payment_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; await query.answer()
    data=query.data
    parts=data.split("_")
    if len(parts)<3: return
    action=parts[1]; ticket="_".join(parts[2:])
    escrow=escrows.get(ticket)
    if not escrow: return
    payment_ok=action=="received"; chat_id=escrow["group_id"]
    buyer_id=escrow["buyer_id"]; seller_id=escrow["seller_id"]
    buyer_username=(await context.bot.get_chat_member(chat_id,buyer_id)).user.username
    seller_username=(await context.bot.get_chat_member(chat_id,seller_id)).user.username
    await clear_previous_buttons(context,escrow)
    if payment_ok:
        escrow["status"]="payment_confirmed"; escrow["buyer_confirmed"]=True
        await context.bot.send_message(chat_id,f"Payment confirmed for 🎟️ {ticket}", reply_markup=create_buttons([("I've sent the goods/services ✅",f"seller_sent_goods_{ticket}")]))
    else:
        escrow["status"]="awaiting_payment"; await context.bot.send_message(chat_id,f"Payment not received for 🎟️ {ticket}")

# ---------------- ADMIN RELEASE FUNDS ----------------
async def admin_sent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; await query.answer()
    data=query.data
    if query.message.chat.id!=ADMIN_GROUP_ID: return
    if not data.startswith("admin_sent_"): return
    ticket=data.split("_",2)[2]; escrow=escrows.get(ticket)
    if not escrow: await query.message.reply_text("Escrow not found."); return
    chat_id=escrow["group_id"]; escrow["status"]="completed"
    amount_fiat=escrow.get("fiat_amount",0); amount_crypto=escrow.get("crypto_amount",0); coin=escrow.get("crypto","N/A")
    fee=amount_fiat*FEE_RATE; payout=amount_fiat-fee
    await context.bot.send_message(chat_id,f"🎉 Trade Completed 🎟️ {ticket}\nFunds released: {fmt_auto(payout)} {FIAT_LABEL}")
    escrows.pop(ticket); group_escrows[chat_id].remove(ticket)

# ---------------- DISPUTE ----------------
async def dispute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; await query.answer()
    data=query.data; parts=data.rsplit("_",1); ticket=parts[1] if len(parts)>1 else None
    escrow=escrows.get(ticket); if not escrow: await query.message.reply_text("No active escrow."); return
    user_id=query.from_user.id; username=query.from_user.username or query.from_user.first_name
    if user_id not in [escrow.get("buyer_id"), escrow.get("seller_id")]: await query.message.reply_text("Only participants can dispute."); return
    if escrow.get("disputed"): await query.message.reply_text("Dispute already open."); return
    escrow["disputed"]=True; escrow["status"]="disputed"
    await context.bot.send_message(escrow["group_id"],f"Trade Disputed 🎟️ {ticket} by @{username}. Admin will review.")
    buyer_username=(await context.bot.get_chat_member(escrow['group_id'],escrow['buyer_id'])).user.username
    seller_username=(await context.bot.get_chat_member(escrow['group_id'],escrow['seller_id'])).user.username
    await context.bot.send_message(ADMIN_GROUP_ID,f"Dispute opened 🎟️ {ticket} by @{username}. Buyer: @{buyer_username}, Seller: @{seller_username}")

# ---------------- MAIN ----------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CallbackQueryHandler(admin_sent_callback, pattern=r"^admin_sent_.*$"))
    app.add_handler(CallbackQueryHandler(handle_admin_payment_confirmation, pattern=r"^payment_(received|notreceived)_.*$"))
    app.add_handler(CallbackQueryHandler(dispute_callback, pattern=r"^dispute_.*$"))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("escrow", escrow_command))
    app.add_handler(CommandHandler("wallet", wallet_command))
    app.add_handler(MessageHandler(filters.Regex(r'^/amount \S+ \d+(\.\d+)?$'), handle_amount))
    app.run_polling()

if __name__=="__main__":
    main()
