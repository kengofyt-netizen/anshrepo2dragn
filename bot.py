import os
import sys
import gc
import ctypes
import asyncio
import logging
import shutil
import html
import signal
from datetime import datetime

# --- FORCE LINUX GLIBC TO RELEASE MEMORY TO OS ---
def drop_memory():
    """Forces Linux kernel to drop unused heap/cache immediately"""
    gc.collect()
    try:
        # Calls malloc_trim from C-Standard Library
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass

# Set aggressive memory reclaim
os.environ["MALLOC_ARENA_MAX"] = "2"
drop_memory()

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, Message
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- CONFIGURATION ---
BOT_TOKEN = "9278681199:AAELNvPpbqJsn3_dcic-sB3f8XKzAnf9dLM" 
OWNER_ID = 5621481813 

# --- CONSTANTS ---
BASE_DIR = os.path.abspath("user_bots")

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- GLOBAL STORAGE ---
active_sessions = {}
BOT_INSTANCE = None

# --- STATES ---
class Host(StatesGroup):
    ask_name = State()
    upload_script = State()
    upload_reqs = State()

class PipInstall(StatesGroup):
    ask_package = State()

class ReplaceFile(StatesGroup):
    upload_new = State()

# --- HELPERS ---
def get_main_keyboard():
    kb = [
        [KeyboardButton(text="📂 My Scripts"), KeyboardButton(text="☁️ Host New")],
        [KeyboardButton(text="🛑 Stop Scripts"), KeyboardButton(text="🗑 Delete Folder")],
        [KeyboardButton(text="📦 Pip Install")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def read_last_logs(project_path, max_lines=25):
    log_file = os.path.join(project_path, "bot.log")
    if not os.path.exists(log_file):
        return "No logs generated yet."
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return "".join(lines[-max_lines:]).strip() or "Empty logs."
    except Exception as e:
        return f"Error: {e}"

def kill_process_tree(proc):
    try:
        if sys.platform == "win32":
            import subprocess
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try: proc.kill()
        except: pass

def stop_project(uid, project_name):
    if uid in active_sessions and project_name in active_sessions[uid]:
        entry = active_sessions[uid][project_name]
        proc = entry.get('proc')
        log_handle = entry.get('log_file')
        
        if proc: kill_process_tree(proc)
        if log_handle and not log_handle.closed:
            try: log_handle.close()
            except: pass
            
        del active_sessions[uid][project_name]
        drop_memory()
        return True
    return False

# --- ROUTER ---
router = Router()

@router.message(Command("start"))
async def start_command(message: Message):
    if message.from_user.id != OWNER_ID:
        return await message.reply("🚫 <b>Access Denied</b>", parse_mode=ParseMode.HTML)
    
    os.makedirs(os.path.join(BASE_DIR, str(message.from_user.id)), exist_ok=True)
    drop_memory()
    await message.reply("<b>⚡ XNS HOSTING : ONLINE</b>", reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

@router.message(Command("cancel"))
@router.message(F.text == "❌ Cancel")
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    drop_memory()
    await message.reply("❌ Cancelled", reply_markup=get_main_keyboard())

# --- HOSTING NEW BOT ---
@router.message(F.text == "☁️ Host New")
async def host_new(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID: return
    await message.reply("📂 <b>Enter Project Name:</b>", parse_mode=ParseMode.HTML)
    await state.set_state(Host.ask_name)

@router.message(Host.ask_name)
async def receive_folder_name(message: Message, state: FSMContext):
    folder_name = "".join(x for x in message.text if x.isalnum() or x in "_-").strip()
    uid = message.from_user.id
    path = os.path.join(BASE_DIR, str(uid), folder_name)
    
    if os.path.exists(path):
        return await message.reply("⚠️ <b>Exists.</b> Try another name.", parse_mode=ParseMode.HTML)
    
    os.makedirs(path, exist_ok=True)
    await state.update_data(host_path=path, host_name=folder_name)
    await message.reply(f"✅ <b>{folder_name}</b> created.\n☁️ Send <b>.py</b> file:", parse_mode=ParseMode.HTML)
    await state.set_state(Host.upload_script)

@router.message(Host.upload_script, F.document)
async def handle_script_upload(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.endswith('.py'):
        return await message.reply("❌ Only .py files allowed")

    data = await state.get_data()
    file_info = await message.bot.get_file(doc.file_id)
    await message.bot.download_file(file_info.file_path, os.path.join(data['host_path'], doc.file_name))
    await state.update_data(main_script=doc.file_name)
    drop_memory()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏩ Skip Requirements", callback_data="skip_reqs")]])
    await message.reply("📄 Saved. Send <code>requirements.txt</code> or Skip.", reply_markup=kb, parse_mode=ParseMode.HTML)
    await state.set_state(Host.upload_reqs)

@router.message(Host.upload_reqs, F.document)
async def handle_reqs_upload(message: Message, state: FSMContext):
    doc = message.document
    if doc.file_name != "requirements.txt": return 
    
    data = await state.get_data()
    file_info = await message.bot.get_file(doc.file_id)
    await message.bot.download_file(file_info.file_path, os.path.join(data['host_path'], "requirements.txt"))
    drop_memory()
    await finalize_setup(message, state, has_reqs=True)

@router.callback_query(Host.upload_reqs, F.data == "skip_reqs")
async def skip_reqs_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await finalize_setup(callback.message, state, has_reqs=False)

async def finalize_setup(message, state, has_reqs):
    data = await state.get_data()
    await state.clear()
    
    msg = await message.reply("⏳ <b>Finalizing...</b>", parse_mode=ParseMode.HTML)
    
    if has_reqs:
        await msg.edit_text("🛠 <b>Installing Dependencies...</b>", parse_mode=ParseMode.HTML)
        req_path = os.path.join(data['host_path'], "requirements.txt")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", req_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.communicate()
        drop_memory()

    proj = data['host_name']
    script = data['main_script']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="▶️ Run Now", callback_data=f"run_{proj}|{script}")]])
    await msg.edit_text(f"✅ <b>Deployed:</b> <code>{proj}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)

# --- PIP INSTALL ---
@router.message(F.text == "📦 Pip Install")
async def start_pip_install(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID: return
    await message.reply("📦 <b>Pip Install</b>\nEnter package name:", parse_mode=ParseMode.HTML)
    await state.set_state(PipInstall.ask_package)

@router.message(PipInstall.ask_package)
async def run_pip_install(message: Message, state: FSMContext):
    packages = message.text.strip().split()
    await state.clear()
    msg = await message.reply(f"⏳ Installing <code>{' '.join(packages)}</code>...", parse_mode=ParseMode.HTML)
    
    cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir"] + packages
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        out = (stdout.decode() or stderr.decode()).strip()
        if len(out) > 2500: out = out[-2500:]
        status = "✅ Success" if proc.returncode == 0 else "❌ Failed"
        await msg.edit_text(f"{status}\n<pre>{html.escape(out)}</pre>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    finally:
        drop_memory()

# --- MENUS ---
@router.message(F.text == "📂 My Scripts")
async def my_scripts_list(message: Message):
    if message.from_user.id != OWNER_ID: return
    await show_project_list(message, "view_proj_")

@router.message(F.text == "🛑 Stop Scripts")
async def stop_scripts_menu(message: Message):
    if message.from_user.id != OWNER_ID: return
    running = active_sessions.get(message.from_user.id, {})
    if not running:
        return await message.reply("❌ <b>No Active Scripts</b>", parse_mode=ParseMode.HTML)
    
    kb = [[InlineKeyboardButton(text=f"🛑 Stop {p}", callback_data=f"confirm_stop_{p}")] for p in running]
    kb.append([InlineKeyboardButton(text="❌ Close", callback_data="close_menu")])
    await message.reply("<b>Select Script to Stop:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)

@router.message(F.text == "🗑 Delete Folder")
async def delete_folder_menu(message: Message):
    if message.from_user.id != OWNER_ID: return
    await show_project_list(message, "confirm_del_")

async def show_project_list(message: Message, prefix, user_id=None):
    uid = user_id or message.from_user.id
    path = os.path.join(BASE_DIR, str(uid))
    projects = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))] if os.path.exists(path) else []
    
    if not projects:
        return await message.answer("📂 <b>Empty.</b>", parse_mode=ParseMode.HTML)

    kb = []
    sessions = active_sessions.get(uid, {})
    for proj in projects:
        is_running = proj in sessions and sessions[proj].get('proc') and sessions[proj]['proc'].returncode is None
        status = "🟢" if is_running else "🔴" if prefix == "view_proj_" else "🗑"
        kb.append([InlineKeyboardButton(text=f"{status} {proj}", callback_data=f"{prefix}{proj}")])
    kb.append([InlineKeyboardButton(text="❌ Close", callback_data="close_menu")])
    await message.answer("<b>Select Project:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)

# --- PROJECT DETAILS ---
@router.callback_query(F.data.startswith("view_proj_"))
async def view_project_ui(callback: CallbackQuery):
    proj = callback.data.split("view_proj_", 1)[1]
    await show_project_details(callback, proj)

async def show_project_details(callback: CallbackQuery, proj):
    uid = callback.from_user.id
    path = os.path.join(BASE_DIR, str(uid), proj)
    
    if not os.path.exists(path):
        stop_project(uid, proj)
        return await callback.answer("Not found", show_alert=True)

    session = active_sessions.get(uid, {}).get(proj)
    is_running = session and session.get('proc') and session['proc'].returncode is None
    
    files = [f for f in os.listdir(path) if f.endswith(".py")]
    text = f"<b>📂 {proj}</b>\nStatus: {'🟢 Running' if is_running else '🔴 Stopped'}"
    
    kb = [[InlineKeyboardButton(text=f"📄 {f}", callback_data=f"opts_{proj}|{f}")] for f in files]
    
    ctrl = []
    if is_running:
        ctrl.append(InlineKeyboardButton(text="🔄 Restart", callback_data=f"restart_{proj}"))
        ctrl.append(InlineKeyboardButton(text="🛑 Stop", callback_data=f"confirm_stop_{proj}"))
    else:
        ctrl.append(InlineKeyboardButton(text="▶️ Start", callback_data=f"start_folder_{proj}"))
    
    if ctrl: kb.append(ctrl)
    kb.append([InlineKeyboardButton(text="📋 Read Logs", callback_data=f"logs_{proj}")])
    kb.append([InlineKeyboardButton(text="🔄 Replace File", callback_data=f"replace_{proj}")])
    kb.append([InlineKeyboardButton(text="🗑 Delete", callback_data=f"confirm_del_{proj}")])
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="goto_list")])
    
    try: await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)
    except: pass

@router.callback_query(F.data.startswith("logs_"))
async def read_logs_callback(callback: CallbackQuery):
    proj = callback.data.split("logs_", 1)[1]
    uid = callback.from_user.id
    logs = read_last_logs(os.path.join(BASE_DIR, str(uid), proj))
    if len(logs) > 3000: logs = logs[-3000:]
    
    text = f"📋 <b>Recent Logs: {proj}</b>\n\n<pre>{html.escape(logs)}</pre>"
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data=f"view_proj_{proj}")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)

@router.callback_query(F.data == "goto_list")
async def back_to_list(callback: CallbackQuery):
    await callback.message.delete()
    await show_project_list(callback.message, "view_proj_", user_id=callback.from_user.id)

@router.callback_query(F.data == "close_menu")
async def close_menu(callback: CallbackQuery):
    await callback.message.delete()
    drop_memory()

@router.callback_query(F.data.startswith("opts_"))
async def file_options(callback: CallbackQuery):
    _, payload = callback.data.split("opts_", 1)
    proj, file = payload.split("|")
    text = f"<b>📄 File:</b> {file}\n<b>Folder:</b> {proj}\n"
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data=f"view_proj_{proj}")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)

# --- EXECUTION LOGIC ---
@router.callback_query(F.data.startswith("start_folder_"))
async def start_folder(callback: CallbackQuery):
    proj = callback.data.split("start_folder_", 1)[1]
    uid = callback.from_user.id
    path = os.path.join(BASE_DIR, str(uid), proj)
    files = [f for f in os.listdir(path) if f.endswith(".py")]
    target = "main.py" if "main.py" in files else files[0] if files else None
    
    if target: await run_script(callback.message, uid, proj, target, callback)
    else: await callback.answer("No .py files found", show_alert=True)

@router.callback_query(F.data.startswith("run_"))
async def run_specific(callback: CallbackQuery):
    _, payload = callback.data.split("run_", 1)
    proj, file = payload.split("|")
    await run_script(callback.message, callback.from_user.id, proj, file, callback)

async def run_script(message, uid, proj, file, callback=None, is_restart=False):
    path = os.path.join(BASE_DIR, str(uid), proj)
    
    existing = active_sessions.get(uid, {}).get(proj)
    if existing and existing.get('proc') and existing['proc'].returncode is None:
        if callback: await callback.answer("Already Running!")
        return

    msg = callback.message if callback else message
    await msg.edit_text(f"🚀 <b>Starting {file}...</b>", parse_mode=ParseMode.HTML)

    try:
        log_handle = open(os.path.join(path, "bot.log"), "w", encoding="utf-8", buffering=1)
        kwargs = {}
        if sys.platform != "win32":
            kwargs["preexec_fn"] = os.setsid
        else:
            import subprocess
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", file,
            cwd=path, stdout=log_handle, stderr=asyncio.subprocess.STDOUT, **kwargs
        )
        
        if uid not in active_sessions: active_sessions[uid] = {}
        active_sessions[uid][proj] = {'proc': proc, 'file': file, 'log_file': log_handle}
        
        await asyncio.sleep(1.5)
        
        if proc.returncode is not None and proc.returncode != 0:
            log_handle.close()
            error_log = read_last_logs(path, 15)
            await msg.edit_text(f"💥 <b>Script Failed on Startup!</b>\n\n<pre>{html.escape(error_log)}</pre>", parse_mode=ParseMode.HTML)
            stop_project(uid, proj)
        else:
            run_type = "🔄 Restarted" if is_restart else "✅ Started"
            await msg.bot.send_message(uid, f"{run_type} <b>{proj}</b> (PID: <code>{proc.pid}</code>)", parse_mode=ParseMode.HTML)
            if callback: await show_project_details(callback, proj)
            asyncio.create_task(monitor_process_exit(uid, proj, file, proc, log_handle))
    except Exception as e:
        stop_project(uid, proj)
        await msg.edit_text(f"❌ Error: {e}")
    finally:
        drop_memory()

async def monitor_process_exit(uid, proj, file, proc, log_handle):
    returncode = await proc.wait()
    try:
        if not log_handle.closed: log_handle.close()
    except: pass
    
    if uid in active_sessions and proj in active_sessions[uid]:
        del active_sessions[uid][proj]
        drop_memory()
        
        if returncode != 0 and BOT_INSTANCE:
            recent_log = read_last_logs(os.path.join(BASE_DIR, str(uid), proj), 15)
            text = f"💥 <b>Crash Detected!</b>\n\n📂 <b>{proj}</b> | Exit: <code>{returncode}</code>\n\n<pre>{html.escape(recent_log)}</pre>"
            try: await BOT_INSTANCE.send_message(uid, text, parse_mode=ParseMode.HTML)
            except: pass

@router.callback_query(F.data.startswith("restart_"))
async def restart_script(callback: CallbackQuery):
    proj = callback.data.split("restart_", 1)[1]
    uid = callback.from_user.id
    session = active_sessions.get(uid, {}).get(proj)
    if not session: return await callback.answer("Not running!")
    file = session.get('file')
    stop_project(uid, proj)
    await asyncio.sleep(0.3)
    await run_script(callback.message, uid, proj, file, callback, is_restart=True)

@router.callback_query(F.data.startswith("confirm_stop_"))
async def confirm_stop(callback: CallbackQuery):
    proj = callback.data.split("confirm_stop_", 1)[1]
    kb = [
        [InlineKeyboardButton(text="✅ Yes, Stop", callback_data=f"do_stop_{proj}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"view_proj_{proj}")]
    ]
    await callback.message.edit_text(f"❓ Stop {proj}?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("do_stop_"))
async def do_stop(callback: CallbackQuery):
    proj = callback.data.split("do_stop_", 1)[1]
    stop_project(callback.from_user.id, proj)
    await callback.answer("Stopped")
    await show_project_details(callback, proj)

@router.callback_query(F.data.startswith("confirm_del_"))
async def confirm_del(callback: CallbackQuery):
    proj = callback.data.split("confirm_del_", 1)[1]
    kb = [
        [InlineKeyboardButton(text="✅ DELETE", callback_data=f"do_del_{proj}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"view_proj_{proj}")]
    ]
    await callback.message.edit_text(f"⚠️ Delete {proj}?", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("do_del_"))
async def do_del(callback: CallbackQuery):
    proj = callback.data.split("do_del_", 1)[1]
    uid = callback.from_user.id
    stop_project(uid, proj)
    shutil.rmtree(os.path.join(BASE_DIR, str(uid), proj), ignore_errors=True)
    drop_memory()
    await callback.answer("🗑 Deleted")
    try: await callback.message.delete()
    except: pass
    await show_project_list(callback.message, "view_proj_", user_id=uid)

# --- REPLACE FILE ---
@router.callback_query(F.data.startswith("replace_"))
async def initiate_replace(callback: CallbackQuery, state: FSMContext):
    proj = callback.data.split("replace_", 1)[1]
    await state.update_data(replace_proj=proj)
    await callback.message.reply(f"🔄 Send replacement .py file for {proj}:")
    await state.set_state(ReplaceFile.upload_new)
  
@router.message(ReplaceFile.upload_new, F.document)
async def handle_replace_file(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.endswith(".py"): return await message.reply("❌ Only .py files allowed.")
    
    data = await state.get_data()
    proj = data['replace_proj']
    uid = message.from_user.id
    await state.clear()
    
    msg = await message.reply("⏳ Replacing...")
    was_running = proj in active_sessions.get(uid, {})
    stop_project(uid, proj)
    
    file_info = await message.bot.get_file(doc.file_id)
    await message.bot.download_file(file_info.file_path, os.path.join(BASE_DIR, str(uid), proj, doc.file_name))
    drop_memory()
    
    if was_running:
        await msg.edit_text(f"🔄 Starting {doc.file_name}...")
        await run_script(msg, uid, proj, doc.file_name)
    else:
        await msg.edit_text(f"✅ Replaced: <code>{doc.file_name}</code>", parse_mode=ParseMode.HTML)

# --- MAIN ---
async def main():
    global BOT_INSTANCE
    os.makedirs(BASE_DIR, exist_ok=True)
    
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_INSTANCE = bot
    dp = Dispatcher()
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    print("Aiogram Bot Started (Low RAM + Malloc Flush Active)")
    
    drop_memory()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot Stopped")
