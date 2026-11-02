import asyncio
import logging
import os
import base64
import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from datetime import datetime
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- SOZLAMALAR ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8897760213:AAFvcSo11EzLnyJQcECbFRSFbM6kXnZDVXI")
SHEET_NAME = os.getenv("SHEET_NAME", "MalinachiBaza")
ORDERS_CHANNEL_ID = -5437468163   # Yangi buyurtmalar kanali ID
ZAKLAD_CHANNEL_ID = -5517056199   # To'lov cheklari va tasdiqlash guruhi ID
ADMIN_IDS = [6150118850]          # Admin Telegram ID raqamlari

# Google Sheets ulanishi
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

creds_base64 = os.getenv("GSPREAD_CREDENTIALS_BASE64")

if creds_base64:
    # Render muhitida Base64 orqali to'g'ridan-to'g'ri o'qish
    creds_dict = json.loads(base64.b64decode(creds_base64).decode("utf-8"))
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
else:
    # Mahalliy kompyuterda fayldan o'qish
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

client = gspread.authorize(creds)
spreadsheet = client.open(SHEET_NAME)
sheet = spreadsheet.sheet1

# 'Zakladlar' nomli varaqni tekshirish yoki ochish
try:
    zaklad_sheet = spreadsheet.worksheet("Zakladlar")
except gspread.exceptions.WorksheetNotFound:
    zaklad_sheet = spreadsheet.add_worksheet(title="Zakladlar", rows=1000, cols=10)
    zaklad_sheet.append_row(["Telegram ID", "Ko'chatlar soni", "Har bir narxi", "Zaklad summasi", "Sana", "Holati"])

# Botni ishga tushirish (Default HTML parse mode bilan)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

logging.basicConfig(level=logging.INFO)

# --- FSM (STATE) HOLATLAR ---
class Form(StatesGroup):
    name = State()
    phone = State()
    address = State()
    quantity = State()
    goal = State()
    experience = State()
    tariff = State()
    confirm_yes_set_1 = State()
    confirm_yes_set_2 = State()
    confirm_yes_set_3 = State()
    final_plus = State()
    waiting_for_broadcast = State()

class ZakladState(StatesGroup):
    waiting_for_receipt = State()

# --- MENYULAR ---
def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👤 Men haqimda")]],
        resize_keyboard=True
    )

def get_phone_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📞 Telefon raqamni ulashish", request_contact=True)],
            [KeyboardButton(text="👤 Men haqimda")]
        ],
        resize_keyboard=True
    )

# --- ADMIN: XABAR TARQATISH (/broadcast) ---
@router.message(Command("broadcast"))
async def admin_broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await message.answer("📢 Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni kiriting:")
    await state.set_state(Form.waiting_for_broadcast)

@router.message(Form.waiting_for_broadcast)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        all_records = sheet.get_all_records()
        user_ids = set()
        for row in all_records:
            uid = row.get("Telegram ID")
            if uid:
                user_ids.add(int(uid))
        
        success = 0
        failed = 0
        
        await message.answer(f"⏳ Xabar tarqatish boshlandi... Jami foydalanuvchilar: {len(user_ids)} ta")
        
        for uid in user_ids:
            try:
                await message.send_copy(chat_id=uid)
                success += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
                
        await message.answer(f"✅ Xabar tarqatish yakunlandi!\n\nYetib bordi: {success} ta\nXatolik: {failed} ta")
    except Exception as e:
        await message.answer(f"Tarqatishda xatolik yuz berdi: {e}")
        
    await state.clear()

# --- ADMIN: KELISHUV YUBORISH (/kelishuv user_id count price deposit hours) ---
@router.message(Command("kelishuv"))
async def admin_send_agreement(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = message.text.split()
    # Format: /kelishuv [user_id] [soni] [dona_narxi] [zaklad] [muddat_soat]
    # Agar muddat berilmasa, avtomatik 24 soat hisoblanadi
    if len(args) < 5 or len(args) > 6:
        await message.answer(
            "⚠️ <b>Format:</b> <code>/kelishuv [user_id] [kochat_soni] [dona_narxi] [zaklad_summasi] [soat]</code>\n\n"
            "<b>Misol (24 soatlik muddat):</b>\n<code>/kelishuv 6150118850 150 25000 100000 24</code>\n"
            "<b>Misol (5 kun = 120 soat):</b>\n<code>/kelishuv 6150118850 150 25000 100000 120</code>"
        )
        return

    user_id = args[1]
    count = args[2]
    price = args[3]
    deposit = args[4]
    hours = int(args[5]) if len(args) == 6 else 24

    total_sum = int(count) * int(price)
    
    # Belgilangan muddatni hisoblash (hozirgi vaqtga soatni qo'shish)
    deadline_time = datetime.now() + timedelta(hours=hours)
    deadline_str = deadline_time.strftime("%d.%m.%Y soat %H:%M")

    text = (
        "🤝 <b>Kelishuv shartlari va navbatingiz tasdiqlandi!</b>\n\n"
        "Siz bilan telefon orqali gaplashib, quyidagi shartlarga kelishib oldik:\n\n"
        f"🌱 Ko'chatlar soni: <b>{count} tup</b>\n"
        f"💵 Bir dona narxi: <b>{int(price):,} so'm</b>\n"
        f"💰 Jami summa: <b>{total_sum:,} so'm</b>\n"
        f"🔒 Joyni band qilish (zaklad): <b>{int(deposit):,} so'm</b>\n"
        f"⏳ <b>Zaxirada saqlanish muddati:</b> <code>{deadline_str}</code> gacha ({hours} soat)\n\n"
        "— — —\n\n"
        "💡 <b>Do‘stona eslatma va adolatli navbat tartibi:</b>\n\n"
        "Bahorgi mavsum uchun saralangan navlarning soni cheklanganligi sababli, tizim siz ajratgan joyni ko‘rsatilgan vaqtgacha <b>zaxirada qat'iy ushlab turadi</b>.\n\n"
        "Agar belgilangan vaqt ichida zaklad kiritilmasa, xafa bo‘lish yo‘q — tizim bu joyni navbatda kutib turgan keyingi xaridorga avtomatik tarzda ochib beradi va sizning so‘rovingiz bekor bo‘ladi.\n\n"
        "Keyinroq to‘lov qilsangiz, navbatga qaytadan yozilasiz, lekin ungacha o‘z o‘rnini oldindan mustahkamlab qo‘yganlar bahorda eng birinchi bo‘lib ko‘chatlarni qabul qilib olishadi.\n\n"
        "O‘z o‘rningizni band qilish uchun pastdagi <b>'Zaklad kiritish'</b> tugmasini bosing:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Zaklad kiritish", callback_data=f"paydep_{count}_{price}_{deposit}")]
    ])

    try:
        await bot.send_message(chat_id=int(user_id), text=text, reply_markup=keyboard)
        await message.answer(f"✅ Kelishuv ID: <code>{user_id}</code> ga muvaffaqiyatli yuborildi. Muddat: {deadline_str}")
    except Exception as e:
        await message.answer(f"❌ Xatolik yuz berdi: {e}")

# --- MIJOZ: ZAKLAD TUGMASI VA REKVIZITLAR ---
@router.callback_query(F.data.startswith("paydep_"))
async def process_deposit_click(callback: CallbackQuery, state: FSMContext):
    _, count, price, deposit = callback.data.split("_")
    await state.update_data(count=count, price=price, deposit=deposit)
    
    text = (
        "💳 <b>To'lov rekvizitlari:</b>\n\n"
        f"Zaklad summasi: <b>{int(deposit):,} so'm</b>\n"
        "Karta: <code>9860 3501 4618 4337</code> (Borliyeva Ch.)\n\n"
        "To'lovni amalga oshirgach, to'lov kvitansiyasini (chek skrinshotini) shu yerga rasm ko'rinishida yuboring 📥"
    )
    await callback.message.answer(text)
    await state.set_state(ZakladState.waiting_for_receipt)
    await callback.answer()

# --- MIJOZ: TO'LOV CHEKINI YUBORGANIDA ---
@router.message(ZakladState.waiting_for_receipt, F.photo)
async def process_receipt_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    photo_id = message.photo[-1].file_id
    user = message.from_user

    count = data.get("count", "0")
    price = data.get("price", "0")
    deposit = data.get("deposit", "0")

    caption = (
        "📥 <b>YANGI TO'LOV CHEKI!</b>\n\n"
        f"👤 Mijoz: {user.full_name} (@{user.username or 'yoq'})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"🌱 Ko'chatlar: <b>{count} tup</b>\n"
        f"💵 Dona narxi: <b>{price} so'm</b>\n"
        f"💰 Belgilangan zaklad: <b>{int(deposit):,} so'm</b>\n"
        f"📅 Sana: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    verify_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"adm_ok_{user.id}_{count}_{price}_{deposit}"),
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"adm_no_{user.id}_{count}_{price}_{deposit}")
        ]
    ])

    await bot.send_photo(
        chat_id=ZAKLAD_CHANNEL_ID,
        photo=photo_id,
        caption=caption,
        reply_markup=verify_keyboard
    )

    await message.answer("✅ To'lov chekingiz qabul qilindi! Mutaxassis chekni tasdiqlashi bilan sizga rasmiy bildirishnoma boradi.")
    await state.clear()

# --- KANALDA ADMIN TASDIQLASHI YOKI RAD ETISHI ---
@router.callback_query(F.data.startswith("adm_ok_"))
async def admin_verify_approve(callback: CallbackQuery, bot: Bot):
    _, _, user_id, count, price, deposit = callback.data.split("_")
    now_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 1. Google Sheets 'Zakladlar' varag'iga yozish
    try:
        zaklad_sheet.append_row([str(user_id), str(count), str(price), str(deposit), now_date, "Tasdiqlandi"])
    except Exception as e:
        logging.error(f"Zaklad Sheets xatosi: {e}")

    # 2. Asosiy varaqdagi (Sheet1) 'Zaklad' ustunini yangilash (12-ustun)
    try:
        cell = sheet.find(str(user_id))
        if cell:
            sheet.update_cell(cell.row, 13, f"To'landi ({int(deposit):,} so'm)")
    except Exception as e:
        logging.error(f"Asosiy varaqda zaklad ustunini yangilashda xato: {e}")

    # 3. Foydalanuvchiga rasmiy tabrik xabari
    congrat_text = (
        "🎉 <b>To'lovingiz qabul qilindi va tasdiqlandi!</b>\n\n"
        "Siz bahorgi mavsum uchun saralangan Atlant navini yetkazib berish ro'yxatiga <b>rasman kiritildingiz</b>.\n\n"
        "Buyurtmangiz bazada qat'iy saqlandi. Ko'chatlar yetkazib berilguncha sizga agrotexnik yangiliklar va parvarish bo'yicha maslahatlar yuborib turiladi. 🌿"
    )
    try:
        await bot.send_message(chat_id=int(user_id), text=congrat_text)
    except Exception as e:
        logging.error(f"Userga tabrik jo'natishda xatolik: {e}")

    await callback.message.edit_caption(
        caption=callback.message.caption + f"\n\n🟢 <b>TASDIQLANDI</b> (Admin: @{callback.from_user.username})",
        reply_markup=None
    )
    await callback.answer("Zaklad tasdiqlandi!")

# Rad etish tugmasi bosilganda
@router.callback_query(F.data.startswith("adm_no_"))
async def admin_verify_reject(callback: CallbackQuery, bot: Bot):
    # callback_data dan ma'lumotlarni ajratib olamiz
    _, _, user_id, count, price, deposit = callback.data.split("_")
    user_id_int = int(user_id)

    # Foydalanuvchining FSM holatini masofadan turib qayta faollashtiramiz
    user_state = FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_id_int, user_id=user_id_int)
    )
    # Eski kelishuv ma'lumotlarini saqlab, yana chek kutish holatiga qo'yamiz
    await user_state.update_data(count=count, price=price, deposit=deposit)
    await user_state.set_state(ZakladState.waiting_for_receipt)

    reject_text = (
        "⚠️ <b>To'lov chekida xatolik aniqlandi.</b>\n\n"
        "Yuborilgan to'lov cheki tasdiqlanmadi (chek xira, summa noto'g'ri yoki to'lov o'tmagan bo'lishi mumkin).\n\n"
        "Iltimos, to'g'ri to'lov kvitansiyasini (skrinshot yoki chek rasmini) <b>qaytadan shu yerga rasm ko'rinishida yuboring</b> 📥"
    )
    try:
        await bot.send_message(chat_id=user_id_int, text=reject_text)
    except Exception as e:
        logging.error(f"Userga rad xabarini jo'natishda xatolik: {e}")

    await callback.message.edit_caption(
        caption=callback.message.caption + f"\n\n🔴 <b>RAD ETILDI</b> (Admin: @{callback.from_user.username})\n<i>Mijozdan yangi chek kutilmoqda...</i>",
        reply_markup=None
    )
    await callback.answer("Chek rad etildi, mijozga qayta yuborish imkoni berildi!")


import html

# --- "MEN HAQIMDA" ---
@router.message(F.text == "👤 Men haqimda")
async def show_my_info_from_sheets(message: Message):
    user_id = str(message.from_user.id).strip()
    
    try:
        all_rows = sheet.get_all_values()
        
        # Baza bo'sh bo'lsa
        if len(all_rows) <= 1:
            await message.answer(
                "⚠️ <b>Siz hali ro'yxatda yo'qsiz!</b>\n\n"
                "Bahorgi Atlant malina ko'chatlari uchun navbatga yozilish va o'z joyingizni band qilish uchun /start buyrug'ini bosing va qisqa so'rovnomadan o'ting. 🌿"
            )
            return

        headers = all_rows[0]
        data_rows = all_rows[1:]
        
        found_user = None
        # Qatorlarni pastdan tepaga qarab tekshirish
        for row in reversed(data_rows):
            # Qatordagi kataklar ichida user_id borligini aniqlash
            if user_id in [str(cell).strip() for cell in row]:
                # Sarlavhalarga mos lug'at tuzish (yetmagan kataklarni bo'sh string bilan to'ldirish)
                row_padded = row + [""] * (len(headers) - len(row))
                found_user = dict(zip(headers, row_padded))
                break
        
        # 1-REJIM: Bazada topilsa
        if found_user:
            zaklad_val = str(found_user.get("Zaklad") or "").strip()
            
            if zaklad_val and zaklad_val.lower() not in ["", "yo'q", "none", "-", "0"]:
                zaklad_status = (
                    f"🎉 <b>Xavotir olmang! Siz zaklad to'lovini muvaffaqiyatli amalga oshirgansiz</b> ({html.escape(zaklad_val)}).\n"
                    "Bahorgi yetkazib berish bo'yicha joyingiz toshga o'yilgandek qat'iy band qilingan! 🌿"
                )
            else:
                zaklad_status = (
                    "⚠️ <b>Zaklad to'lovi:</b> Hali to'lov qilinmagan.\n"
                    "Eslatma: Ko'chatlar bahorda aynan zaklad kiritilgan navbat tartibida yetkaziladi."
                )

            username_val = str(found_user.get("Username") or "").strip()
            if not username_val or username_val.isdigit():
                username_val = f"@{message.from_user.username}" if message.from_user.username else "Mavjud emas"

            # HTML xatosini oldini olish uchun barcha matnlarni tozalash
            name_txt = html.escape(str(found_user.get('Ism', 'Nomaʼlum')))
            user_txt = html.escape(str(username_val))
            phone_txt = html.escape(str(found_user.get('Telefon', 'Nomaʼlum')))
            addr_txt = html.escape(str(found_user.get('Manzil', 'Nomaʼlum')))
            qty_txt = html.escape(str(found_user.get('Soni', 'Nomaʼlum')))
            goal_txt = html.escape(str(found_user.get('Maqsad', 'Nomaʼlum')))
            tariff_txt = html.escape(str(found_user.get('Tarif', 'Nomaʼlum')))

            await message.answer(
                "👤 <b>Sizning bazadagi ma'lumotlaringiz:</b>\n\n"
                f"• Ism: {name_txt}\n"
                f"• Username: {user_txt}\n"
                f"• Tel: {phone_txt}\n"
                f"• Manzil: {addr_txt}\n"
                f"• Ko‘chat soni: {qty_txt}\n"
                f"• Maqsad: {goal_txt}\n"
                f"• Tarif: {tariff_txt}\n\n"
                f"{zaklad_status}"
            )
        else:
            # 2-REJIM: Bazada umuman bo'lmasa
            await message.answer(
                "⚠️ <b>Siz hali ro'yxatda yo'qsiz!</b>\n\n"
                "Bahorgi Atlant malina ko'chatlari uchun navbatga yozilish va o'z joyingizni band qilish uchun /start buyrug'ini bosing va qisqa so'rovnomadan o'ting. 🌿"
            )

    except Exception as e:
        logging.error(f"Men haqimda xatosi: {e}")
        await message.answer("Ma'lumotlarni o'qishda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")


# --- 1. START VA MA'LUMOT YIG'ISH ---
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Assalomu alaykum. Malinachi_ rasmiy botiga xush kelibsiz. 🌿\n\nTanishib olishimiz uchun ism-sharifingizni kiriting:",
        reply_markup=get_main_menu()
    )
    await state.set_state(Form.name)

@router.message(Form.name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(
        "Rahmat. Bog‘lanishimiz uchun pastdagi tugmani bosing:",
        reply_markup=get_phone_menu()
    )
    await state.set_state(Form.phone)

@router.message(Form.phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await message.answer(
        "Yaxshi. Yetkazib berish uchun yashash manzilingizni (Viloyat / Tuman) yozib yuboring:",
        reply_markup=get_main_menu()
    )
    await state.set_state(Form.address)

@router.message(Form.phone)
async def process_phone_text(message: Message, state: FSMContext):
    if message.text == "👤 Men haqimda":
        return
    await state.update_data(phone=message.text)
    await message.answer(
        "Yaxshi. Yetkazib berish uchun yashash manzilingizni (Viloyat / Tuman) yozib yuboring:",
        reply_markup=get_main_menu()
    )
    await state.set_state(Form.address)

@router.message(Form.address)
async def process_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text)
    await message.answer("Nechta ko‘chat olmoqchisiz? (Masalan: 1500):")
    await state.set_state(Form.quantity)

@router.message(Form.quantity)
async def process_quantity(message: Message, state: FSMContext):
    await state.update_data(quantity=message.text)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏡 Uy uchun / Oila sog'lig'i (< 1000)", callback_data="goal_home")],
        [InlineKeyboardButton(text="🧪 Probaga / Sinab ko'rish (< 1000)", callback_data="goal_test")],
        [InlineKeyboardButton(text="📈 Katta biznes / Daromad (> 1000)", callback_data="goal_business")]
    ])
    await message.answer("Bu ko‘chatlarni asosan qanday maqsadda olyapsiz?", reply_markup=keyboard)
    await state.set_state(Form.goal)

# --- 2. MAQSADNI TANLASH ---
@router.callback_query(Form.goal, F.data.startswith("goal_"))
async def process_goal(callback: CallbackQuery, state: FSMContext):
    goal_map = {
        "goal_home": "Uy uchun (< 1000)",
        "goal_test": "Probaga (< 1000)",
        "goal_business": "Katta biznes (> 1000)"
    }
    selected_goal = goal_map.get(callback.data, "Boshqa")
    await state.update_data(goal=selected_goal)
    await callback.answer()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Ha, oldin ekkanman, afsuski kuyib qolgan", callback_data="exp_bad")],
        [InlineKeyboardButton(text="🌱 Yo‘q, birinchi marta ekyapman", callback_data="exp_new")]
    ])
    await callback.message.edit_text("Oldin malina ekib ko‘rganmisiz yoki birinchi marta ekmoqchimisiz?", reply_markup=keyboard)
    await state.set_state(Form.experience)

# --- 3. TAJRIBA, STATISTIKA VA TARIFLAR ---
@router.callback_query(Form.experience, F.data.startswith("exp_"))
async def process_experience(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_goal = data.get("goal")
    exp_text = "Oldin kuygan" if callback.data == "exp_bad" else "Birinchi marta"
    await state.update_data(experience=exp_text)
    await callback.answer()

    text = ""
    if "Katta biznes" in str(user_goal) or "Probaga" in str(user_goal):
        text += "📊 Yuqoridagi BMTning FAOSTAT rasmiy bazasida ko‘rib turganingizdek, O‘zbekiston malina yetishtirish va sotish salohiyati bo‘yicha dunyo miqyosidagi qator davlatlar safida turibdi.\n\n"

    if callback.data == "exp_bad":
        text += "O‘tgan yili sifatsiz ko‘chatlar sababli kuyganingizni tushunamiz. Bu safar xavfsiz va kafolatlangan tizim bilan ishlaymiz.\n\n"
    else:
        text += "Xavotir olmang. Biz sizga to‘liq agrotexnik qo‘llanma va maslahat beramiz.\n\n"

    text += (
        "Siz uchun 3 xil tarif mavjud:\n\n"
        "1️⃣ <b>Faqat ko‘chat</b>\n"
        "• Oddiy,sifatli kochat  — [20 - 25 ming so'm]\n\n"
        "2️⃣ <b>OPTIMAL PAKET</b> (Tavsiya etiladi):\n"
        "• Sof ko‘chat + Ozuqa va o‘g‘it (G‘o‘ng / Gumus / Ammofos) — [24 - 30 ming so'm]\n\n"
        "3️⃣ <b>VIP Agrotexnika paket:</b>\n"
        "• To‘liq agronom nazorati\n"
        "• Dugalar va tomchilatib sug‘orish tizimini qurish bo‘yicha loyiha\n"
        "• Tuproq tahlili va individual o‘g‘itlash grafigi — [35 - 45 ming so'm]"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 1-Tarif: Faqat ko'chat", callback_data="tariff_1")],
        [InlineKeyboardButton(text="🟢 2-Tarif: Optimal paket", callback_data="tariff_2")],
        [InlineKeyboardButton(text="🔴 3-Tarif: VIP Paket", callback_data="tariff_3")]
    ])

    try:
        await callback.message.delete()
    except Exception:
        pass

    try:
        await callback.message.answer_photo(
            photo=FSInputFile("22222.png"), 
            caption=text, 
            reply_markup=keyboard
        )
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard)

    await state.set_state(Form.tariff)

# --- 4. TARIFNI TANLASH ---
@router.callback_query(Form.tariff, F.data.startswith("tariff_"))
async def process_tariff(callback: CallbackQuery, state: FSMContext):
    tariff_map = {
        "tariff_1": "1-Tarif (Faqat ko'chat)",
        "tariff_2": "2-Tarif (Optimal paket)",
        "tariff_3": "3-Tarif (VIP Paket)"
    }
    selected_tariff = tariff_map.get(callback.data, "Optimal")
    await state.update_data(tariff=selected_tariff)
    await callback.answer()

    data = await state.get_data()
    msg = f"Tanlovingiz tasdiqlandi: <b>{selected_tariff}</b>. Xavfni noldan past qilamiz, barcha zarur sharoitlar yaratiladi.\n\nTasdiqlash uchun davom etamiz:"
    
    if "Uy uchun" in str(data.get("goal")):
        msg += "\nHovlingizdan unumli foydalanib, oilangizga foyda keltirish muhimmi?"
    else:
        msg += "\nYer-maydoningizdan maksimal foyda olib, daromadga chiqishga tayyorsizmi?"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ha, albatta ✅", callback_data="yes_1")]
    ])
    await callback.message.answer(msg, reply_markup=keyboard)
    await state.set_state(Form.confirm_yes_set_1)

# --- 5. YES SET KASKADI ---
@router.callback_query(Form.confirm_yes_set_1, F.data == "yes_1")
async def yes_step_2(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Tushunarli 🟢", callback_data="yes_2")]
    ])
    await callback.message.edit_text("Bahorgi mavsum uchun ajratilgan limitlar cheklanganini va vaqt o‘tib tugashini bilasizmi?", reply_markup=keyboard)
    await state.set_state(Form.confirm_yes_set_2)

@router.callback_query(Form.confirm_yes_set_2, F.data == "yes_2")
async def yes_step_3(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ha, tayyorman 👍", callback_data="yes_3")]
    ])
    await callback.message.edit_text("Hozir hech qanday to‘lov qilinmaydi. Faqat o‘z joyingizni band qilishga tayyorsizmi?", reply_markup=keyboard)
    await state.set_state(Form.confirm_yes_set_3)

@router.callback_query(Form.confirm_yes_set_3, F.data == "yes_3")
async def final_cta_step(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "🔥 <b>Oxirgi qadam:</b>\n\n"
        "Hozir sizdan pul talab etilmaydi. Shunchaki chatga <b>'+'</b> belgisini yuboring va bazada o‘z joyingizni band qiling."
    )
    await state.set_state(Form.final_plus)

# --- 6. "+" QOLDIRISH VA BUYURTMANI YUBORISH ---
@router.message(Form.final_plus, F.text == "+")
async def process_plus(message: Message, state: FSMContext):
    data = await state.get_data()
    
    all_rows = sheet.get_all_values()
    next_id = len(all_rows) if len(all_rows) > 0 else 1

    tg_username = f"@{message.from_user.username}" if message.from_user.username else "yoq"
    try:
        sheet.append_row([
    str(next_id),
    datetime.now().strftime("%Y-%m-%d %H:%M"),
    tg_username,               # 3-ustun: Username
    str(message.from_user.id), # 4-ustun: Telegram ID
    data.get("name"),          # 5-ustun: Ism
    data.get("phone"),         # 6-ustun: Telefon
    data.get("address"),       # 7-ustun: Manzil
    data.get("quantity"),      # 8-ustun: Soni
    data.get("goal"),          # 9-ustun: Maqsad
    data.get("experience"),    # 10-ustun: Tajriba
    data.get("tariff"),        # 11-ustun: Tarif
    "+",                       # 12-ustun: Status
    ""                         # 13-ustun: Zaklad
])
    except Exception as e:
        logging.error(f"Google Sheets xatosi: {e}")

    await message.answer(
        "Tabriklaymiz. Siz muvaffaqiyatli ro‘yxatdan o‘tdingiz. 🌿\n\nMenejerimiz tez orada siz bilan bog‘lanadi.",
        reply_markup=get_main_menu()
    )

    channel_message = (
        f"🔔 <b>YANGI BUYURTMA!</b> [ID: #{next_id}]\n\n"
        f"• Ism: {data.get('name')}\n"
        f"• Tel: {data.get('phone')}\n"
        f"• Manzil: {data.get('address')}\n"
        f"• Soni: {data.get('quantity')} ta\n"
        f"• Maqsad: {data.get('goal')}\n"
        f"• Tajriba: {data.get('experience')}\n"
        f"• Tarif: {data.get('tariff')}\n"
        f"• Telegram: {tg_username} (ID: <code>{message.from_user.id}</code>)"
    )
    try:
        await bot.send_message(chat_id=ORDERS_CHANNEL_ID, text=channel_message)
    except Exception as e:
        logging.error(f"Kanalga xabar yuborishda xatolik: {e}")

    await state.clear()

@router.message(Form.final_plus)
async def wrong_plus_input(message: Message):
    await message.answer("Davom etish uchun shunchaki '+' belgisini yuboring.")

# --- ASOSIY FUNKSIYA ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())