import asyncio
import aiohttp
import json
import os
import time
import re
import logging
from datetime import datetime
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import threading
import html

# Fix Unicode encoding issue
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Bot Configuration
BOT_TOKEN = "8053858576:AAGnZh5JxUiILEHu5VnFP0mW8ba1ZInNQRg"
ADMIN_ID = 1612918900
CHANNEL_ID = -1003903319155

# Store user Firebase configurations
user_firebase_config = None
USER_CONNECTION_FILE = "user_connection.json"

# Create bot
bot = AsyncTeleBot(BOT_TOKEN)

# Store user sessions
user_sessions = {}

# Setup logging
LOG_FILE = "sms_log.txt"
CHANNEL_LOG_FILE = "channel_log.txt"
MONITOR_LOG_FILE = "monitor_log.txt"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

channel_logger = logging.getLogger('channel')
channel_handler = logging.FileHandler(CHANNEL_LOG_FILE, encoding='utf-8')
channel_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
channel_logger.addHandler(channel_handler)
channel_logger.setLevel(logging.INFO)

monitor_logger = logging.getLogger('monitor')
monitor_handler = logging.FileHandler(MONITOR_LOG_FILE, encoding='utf-8')
monitor_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
monitor_logger.addHandler(monitor_handler)
monitor_logger.setLevel(logging.INFO)

# Global flag for monitoring
monitoring_active = False

def log_monitor(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    monitor_logger.info(log_entry)
    print(f"🔍 MONITOR LOG: {log_entry}")

# Store configurations
auto_fetch_tokens = {}
chat_configs = {}
channel_status_messages = {}

# Store processed messages - ONLY for channel posts, NOT for new messages
PROCESSED_MESSAGES = set()

# Store message tracking for each client
client_message_tracking = {}

# Rate limiting
last_message_time = {}
RATE_LIMIT_DELAY = 1

# Monitoring
monitor_check_interval = 2

def rate_limit(chat_id):
    current_time = time.time()
    if chat_id in last_message_time:
        elapsed = current_time - last_message_time[chat_id]
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
    last_message_time[chat_id] = time.time()

async def safe_edit_message(chat_id, message_id, text, reply_markup=None, max_retries=3):
    for attempt in range(max_retries):
        try:
            rate_limit(chat_id)
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return True
        except Exception as e:
            if "429" in str(e):
                retry_after = 1
                try:
                    match = re.search(r'retry after (\d+)', str(e))
                    if match:
                        retry_after = int(match.group(1)) + 1
                except:
                    retry_after = 2 ** attempt
                print(f"Rate limited. Waiting {retry_after} seconds...")
                await asyncio.sleep(retry_after)
                continue
            elif "message is not modified" in str(e):
                return True
            else:
                print(f"Error editing message: {e}")
                return False
    return False

async def safe_send_message(chat_id, text, reply_markup=None, parse_mode=None, max_retries=3):
    for attempt in range(max_retries):
        try:
            rate_limit(chat_id)
            return await bot.send_message(
                chat_id, 
                text, 
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        except Exception as e:
            if "429" in str(e):
                retry_after = 1
                try:
                    match = re.search(r'retry after (\d+)', str(e))
                    if match:
                        retry_after = int(match.group(1)) + 1
                except:
                    retry_after = 2 ** attempt
                print(f"Rate limited. Waiting {retry_after} seconds...")
                await asyncio.sleep(retry_after)
                continue
            else:
                print(f"Error sending message: {e}")
                return None
    return None

def log_sms(message_type, data):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message_type}"
    if data:
        log_entry += f"\n{json.dumps(data, indent=2, ensure_ascii=False)}"
    logger.info(log_entry)

def log_channel(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    channel_logger.info(log_entry)
    print(f"📢 CHANNEL LOG: {log_entry}")

def save_user_connection():
    global user_firebase_config
    try:
        if user_firebase_config:
            with open(USER_CONNECTION_FILE, 'w') as f:
                json.dump(user_firebase_config, f, indent=2)
            print(f"✅ User connection saved to {USER_CONNECTION_FILE}")
            return True
        else:
            if os.path.exists(USER_CONNECTION_FILE):
                os.remove(USER_CONNECTION_FILE)
                print(f"🗑️ Removed connection file")
            return True
    except Exception as e:
        print(f"❌ Error saving user connection: {e}")
        return False

def load_user_connection():
    global user_firebase_config
    try:
        if os.path.exists(USER_CONNECTION_FILE):
            with open(USER_CONNECTION_FILE, 'r') as f:
                user_firebase_config = json.load(f)
            print(f"✅ Loaded user connection from {USER_CONNECTION_FILE}")
            return True
        return False
    except Exception as e:
        print(f"❌ Error loading user connection: {e}")
        return False

def get_user_connection():
    return user_firebase_config

def set_user_connection(url, key, name=""):
    global user_firebase_config
    user_firebase_config = {
        'url': url,
        'key': key,
        'name': name or "Firebase Connection",
        'added_at': datetime.now().isoformat()
    }
    save_user_connection()

def delete_user_connection():
    global user_firebase_config
    user_firebase_config = None
    if os.path.exists(USER_CONNECTION_FILE):
        os.remove(USER_CONNECTION_FILE)
    return True

async def check_firebase_connection(url, key):
    try:
        if not url.endswith('/'):
            url += '/'
        
        test_url = f"{url}.json?auth={key}&shallow=true"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return True, "✅ Firebase connection successful!"
                else:
                    return False, f"❌ Connection failed: HTTP {response.status}"
    except Exception as e:
        return False, f"❌ Connection error: {str(e)}"

def format_phone(phone):
    phone = re.sub(r'[\s\-\(\)]', '', phone)
    phone = re.sub(r'[^\d+]', '', phone)
    
    if phone.startswith('0'):
        phone = phone[1:]
    if phone.startswith('+'):
        phone = re.sub(r'[^\d+]', '', phone)
        return phone
    if len(phone) == 10 and phone.isdigit():
        phone = '+91' + phone
    elif len(phone) == 12 and phone.startswith('91'):
        phone = '+' + phone
    elif len(phone) == 11 and phone.startswith('91'):
        phone = '+' + phone
    else:
        if len(phone) > 10 and phone.isdigit():
            if phone.startswith('91'):
                phone = '+' + phone
            else:
                if len(phone) == 10:
                    phone = '+91' + phone
                else:
                    phone = '+' + phone
        else:
            phone = '+' + phone
    phone = re.sub(r'[^\d+]', '', phone)
    if len(phone) > 15:
        match = re.search(r'(\d{10})', phone)
        if match:
            phone = '+91' + match.group(1)
    return phone

async def check_client_in_connection(client_id):
    global user_firebase_config
    
    if not user_firebase_config:
        return {
            'found': False,
            'conn_name': 'No connection'
        }
    
    try:
        firebase_url = user_firebase_config['url']
        firebase_key = user_firebase_config['key']
        conn_name = user_firebase_config.get('name', 'Unknown')
        
        if not firebase_url.endswith('/'):
            firebase_url += '/'
        
        url = f"{firebase_url}clients/{client_id}.json?auth={firebase_key}&shallow=true"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        url_full = f"{firebase_url}clients/{client_id}.json?auth={firebase_key}"
                        async with session.get(url_full, timeout=aiohttp.ClientTimeout(total=5)) as response_full:
                            if response_full.status == 200:
                                full_data = await response_full.json()
                                if full_data:
                                    return {
                                        'found': True,
                                        'connection': user_firebase_config,
                                        'data': full_data,
                                        'conn_name': conn_name
                                    }
        return {
            'found': False,
            'connection': user_firebase_config,
            'conn_name': conn_name
        }
    except Exception as e:
        return {
            'found': False,
            'connection': user_firebase_config,
            'conn_name': user_firebase_config.get('name', 'Unknown'),
            'error': str(e)
        }

async def send_sms_via_firebase(connection, client_id, phone, message, sim_number=1):
    try:
        url = connection['url']
        key = connection['key']
        conn_name = connection.get('name', 'Unknown')
        
        if not url.endswith('/'):
            url += '/'
        
        api_url = f"{url}clients/{client_id}/webhookEvent/sendSms.json?auth={key}"
        formatted_phone = format_phone(phone)
        
        payload = {
            "from": sim_number,
            "to": formatted_phone,
            "message": message,
            "isSended": True
        }
        
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:5501",
            "Pragma": "no-cache",
            "Referer": "http://127.0.0.1:5501/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        }
        
        log_sms("SMS REQUEST", {
            "connection": conn_name,
            "client_id": client_id,
            "sim": sim_number,
            "to": formatted_phone,
            "message": message[:100] + "..." if len(message) > 100 else message,
            "url": api_url,
            "payload": payload
        })
        
        start_time = time.time()
        
        async with aiohttp.ClientSession() as session:
            async with session.put(api_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
                response_time = round((time.time() - start_time) * 1000, 2)
                response_text = await response.text()
                
                log_sms("SMS RESPONSE", {
                    "connection": conn_name,
                    "client_id": client_id,
                    "sim": sim_number,
                    "to": formatted_phone,
                    "status_code": response.status,
                    "response_time_ms": response_time,
                    "response": response_text[:500] if response_text else "Empty"
                })
                
                if response.status == 200:
                    return True, "SMS sent successfully!"
                else:
                    return False, f"HTTP {response.status}: {response_text[:200]}"
            
    except Exception as e:
        log_sms("SMS ERROR", {
            "connection": conn_name,
            "client_id": client_id,
            "sim": sim_number,
            "to": formatted_phone,
            "error": str(e)
        })
        return False, str(e)

OTP_PATTERNS = [
    r'(?i)<#>\s*(\d{4,8})\s+is\s+your\s+otp',
    r'(?i)\b(\d{4,8})\b\s+is\s+your\s+otp',
    r'(?i)\botp\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\bverification\s+code\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\bsecurity\s+code\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\bauthentication\s+code\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\bconfirmation\s+code\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\bactivation\s+code\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\bpasscode\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\blogin\s+code\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\bsign[\s-]?in\s+code\b\s*[:\-]?\s*(\d{4,8})',
    r'(?i)\bone[\s-]?time\s+password\b\s*[:\-]?\s*(\d{4,8})',
]

OTP_CONTEXT = [
    "otp", "verification code", "security code", "authentication code",
    "confirmation code", "activation code", "passcode", "login code",
    "sign in code", "one time password", "one-time password",
]

def is_otp_message(message_text):
    if not message_text:
        return False
    text = " ".join(message_text.split())
    lower = text.lower()
    for pattern in OTP_PATTERNS:
        if re.search(pattern, text):
            return True
    numbers = re.finditer(r'\b\d{4,8}\b', text)
    for match in numbers:
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + 60)
        context = lower[start:end]
        if any(keyword in context for keyword in OTP_CONTEXT):
            return True
    return False

async def get_client_messages(connection, client_id):
    try:
        url = connection['url']
        key = connection['key']
        
        if not url.endswith('/'):
            url += '/'
        
        messages_url = f"{url}messages/{client_id}.json?auth={key}"
        
        log_monitor(f"🔍 Checking messages: {messages_url[:100]}...")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(messages_url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        log_monitor(f"📊 Found {len(data)} total messages")
                        return True, data
                    else:
                        log_monitor(f"ℹ️ No messages found")
                        return True, {}
                else:
                    log_monitor(f"❌ Failed to fetch messages: {response.status}")
                    return False, None
            
    except Exception as e:
        log_monitor(f"❌ Error checking messages: {str(e)}")
        return False, None

def extract_otp_code(message_text):
    if not message_text:
        return None
    for pattern in OTP_PATTERNS:
        match = re.search(pattern, message_text, re.IGNORECASE)
        if match:
            return match.group(1)
    numbers = re.finditer(r'\b(\d{4,8})\b', message_text)
    for match in numbers:
        start = max(0, match.start() - 30)
        end = min(len(message_text), match.end() + 30)
        context = message_text[start:end].lower()
        if any(keyword in context for keyword in OTP_CONTEXT):
            return match.group(1)
    return None

def escape_html(text):
    if not text:
        return text
    escaped = html.escape(text)
    escaped = escaped.replace('&lt;#&gt;', '&lt;#&gt;')
    return escaped

def create_copy_button(full_message, otp_code=None):
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = []
    if otp_code:
        buttons.append(InlineKeyboardButton("🔑 Copy OTP", callback_data=f"copy_otp_{otp_code}"))
    import hashlib
    msg_hash = hashlib.md5(full_message.encode()).hexdigest()[:10]
    buttons.append(InlineKeyboardButton("📋 Copy Full SMS", callback_data=f"copy_full_{msg_hash}"))
    keyboard.add(*buttons)
    return keyboard

message_store = {}

async def check_and_display_new_messages():
    global monitoring_active
    
    if not monitoring_active:
        log_monitor("⏸️ Monitoring is stopped, skipping check")
        return
    
    try:
        for chat_id, config in chat_configs.items():
            client_id = config.get('client_id')
            connection = config.get('connection')
            client_name = config.get('client_name', 'Unknown')
            conn_name = config.get('conn_name', 'Unknown')
            
            if not client_id or not connection:
                continue
            
            if client_id not in client_message_tracking:
                log_monitor(f"🔧 Initializing tracking for client {client_id}")
                success = await init_message_tracking(client_id, connection)
                if not success:
                    continue
            
            success, current_messages = await get_client_messages(connection, client_id)
            if not success or current_messages is None:
                continue
            
            initial_messages = client_message_tracking[client_id].get('initial_messages', {})
            initial_count = len(initial_messages)
            current_count = len(current_messages)
            
            log_monitor(f"📊 Initial count: {initial_count}, Current count: {current_count}")
            
            if current_count <= initial_count:
                log_monitor(f"ℹ️ No new messages (count unchanged)")
                continue
            
            # Find ALL new messages - ONLY compare with initial_messages
            new_messages = {}
            initial_ids = set(initial_messages.keys())
            
            for msg_id, msg_data in current_messages.items():
                if msg_id not in initial_ids:
                    msg_type = msg_data.get('type', '')
                    if msg_type == 'incoming':
                        new_messages[msg_id] = msg_data
                        log_monitor(f"📩 New message found: ID={msg_id}, from={msg_data.get('sender', 'Unknown')}")
            
            if not new_messages:
                # Don't update tracking here! This was the main issue
                log_monitor(f"ℹ️ No new incoming messages found (count increased by {current_count - initial_count} non-incoming messages)")
                continue
            
            log_monitor(f"✅ Found {len(new_messages)} new incoming messages to display")
            
            if chat_id in channel_status_messages:
                status_msg_id = channel_status_messages[chat_id]
                try:
                    await safe_edit_message(
                        chat_id,
                        status_msg_id,
                        f"📩 Found {len(new_messages)} New Messages! Displaying in channel..."
                    )
                except:
                    pass
            
            # Process ALL new messages
            for msg_id, msg_data in new_messages.items():
                original_message = msg_data.get('message', '')
                sender = msg_data.get('sender', '')
                date_time = msg_data.get('dateTime', '')
                
                otp_code = extract_otp_code(original_message)
                
                log_monitor(f"📩 New message from {sender}: {original_message[:100]}...")
                log_monitor(f"📤 Displaying message in channel")
                
                import hashlib
                msg_hash = hashlib.md5(original_message.encode()).hexdigest()[:10]
                message_store[msg_hash] = original_message
                
                msg_type_label = "OTP Received!" if otp_code else "New SMS Received!"
                
                escaped_message = escape_html(original_message)
                escaped_sender = escape_html(sender)
                escaped_client_name = escape_html(client_name)
                escaped_conn_name = escape_html(conn_name)
                
                display_text = (
                    f"📱 <b>{msg_type_label}</b>\n\n"
                    f"📞 <b>From:</b> {escaped_sender}\n"
                    f"🕐 <b>Time:</b> {date_time}\n"
                    f"📱 <b>Client:</b> {client_id} ({escaped_client_name})\n"
                    f"🔗 <b>Connection:</b> {escaped_conn_name}\n\n"
                    f"📝 <b>Message:</b>\n"
                    f"<code>{escaped_message}</code>"
                )
                
                if otp_code:
                    display_text += f"\n\n🔑 <b>OTP Code:</b> <code>{otp_code}</code>"
                
                display_text += f"\n\n👇 <i>Click the buttons below to copy:</i>"
                
                sent_msg = await safe_send_message(
                    chat_id,
                    display_text,
                    reply_markup=create_copy_button(original_message, otp_code),
                    parse_mode='HTML'
                )
                
                if sent_msg:
                    log_monitor(f"✅ Message displayed in channel with copy buttons")
                
                admin_msg = (
                    f"✅ New Message Displayed in Channel\n"
                    f"📞 From: {sender}\n"
                    f"📱 Client: {client_id} ({client_name})\n"
                    f"🕐 Time: {date_time}\n"
                    f"🔑 OTP: {otp_code if otp_code else 'No OTP found'}\n"
                    f"📝 Message: {original_message[:100]}..."
                )
                await safe_send_message(ADMIN_ID, admin_msg)
            
            # CRITICAL FIX: Only add the displayed messages to initial_messages
            # DO NOT replace the entire initial_messages with current_messages
            updated_initial = initial_messages.copy()
            
            # Add only the messages we actually displayed
            for msg_id, msg_data in new_messages.items():
                updated_initial[msg_id] = msg_data
            
            client_message_tracking[client_id]['initial_messages'] = updated_initial
            client_message_tracking[client_id]['initial_count'] = len(updated_initial)
            client_message_tracking[client_id]['last_checked'] = datetime.now().isoformat()
            log_monitor(f"📌 Updated tracking for client {client_id}, now {len(updated_initial)} messages (added {len(new_messages)} displayed messages)")
            
            if chat_id in channel_status_messages:
                status_msg_id = channel_status_messages[chat_id]
                try:
                    status_text = f"✅ Processed {len(new_messages)} new message(s)!\n"
                    status_text += f"📊 Total tracked: {client_message_tracking[client_id].get('initial_count', 0)}"
                    await safe_edit_message(
                        chat_id,
                        status_msg_id,
                        status_text
                    )
                except:
                    pass
            
    except Exception as e:
        log_monitor(f"❌ Error in check_and_display_new_messages: {str(e)}")
        import traceback
        traceback.print_exc()

async def init_message_tracking(client_id, connection):
    try:
        success, messages = await get_client_messages(connection, client_id)
        
        if success and messages is not None:
            client_message_tracking[client_id] = {
                'initial_messages': messages.copy(),
                'initial_count': len(messages),
                'last_checked': datetime.now().isoformat(),
                'active': True
            }
            log_monitor(f"✅ Initialized tracking for client {client_id} with {len(messages)} messages")
            return True
        else:
            log_monitor(f"❌ Failed to initialize tracking for client {client_id}")
            return False
    except Exception as e:
        log_monitor(f"❌ Error initializing tracking: {str(e)}")
        return False

async def monitor_loop():
    global monitoring_active
    log_monitor("🔄 Message Monitor started - Displaying ALL new messages in channel")
    
    while True:
        try:
            await check_and_display_new_messages()
            await asyncio.sleep(monitor_check_interval)
        except Exception as e:
            log_monitor(f"❌ Monitor loop error: {str(e)}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(monitor_check_interval)

def get_sim_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📱 SIM 1", callback_data="sim_1"),
        InlineKeyboardButton("📱 SIM 2", callback_data="sim_2")
    )
    keyboard.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))
    return keyboard

def get_channel_sim_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📱 SIM 1", callback_data="channel_sim_1"),
        InlineKeyboardButton("📱 SIM 2", callback_data="channel_sim_2")
    )
    keyboard.add(InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))
    return keyboard

def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔗 Add Firebase Connection", callback_data="add_connection"),
        InlineKeyboardButton("📊 My Connection", callback_data="list_connections"),
        InlineKeyboardButton("📨 Send SMS", callback_data="send_sms"),
        InlineKeyboardButton("📊 Status", callback_data="check_status"),
        InlineKeyboardButton("🤖 Auto-Fetch Mode", callback_data="auto_fetch_toggle")
    )
    return keyboard

@bot.message_handler(commands=['start'])
async def send_welcome(message):
    user_id = message.from_user.id
    
    if message.chat.type == 'private' and user_id != ADMIN_ID:
        await safe_send_message(message.chat.id, "❌ Access Denied! Admin only.")
        return
    
    connection = get_user_connection()
    has_connection = connection is not None
    
    welcome_text = (
        "🤖 Firebase SMS Bot with Message Monitoring\n\n"
        f"👤 User: {message.from_user.first_name}\n"
        f"🔗 Firebase Connection: {'✅ Connected' if has_connection else '❌ Not Configured'}\n"
        f"📢 Monitoring Channel: {CHANNEL_ID}\n\n"
        "How it works:\n"
        "1. Add your Firebase connection: /addfirebase\n"
        "2. Setup channel monitoring: /forcechannel\n"
        "3. Enter Client ID\n"
        "4. Select SIM\n"
        "5. Post SMS in channel: phone | message\n"
        "6. Bot sends SMS via client\n"
        "7. Bot monitors for ALL new messages\n"
        "8. Each new message is displayed in channel with copy buttons\n\n"
        "Commands:\n"
        "/addfirebase - Add your Firebase URL and Key (One-time setup)\n"
        "/removefirebase - Remove your Firebase connection\n"
        "/forcechannel - Setup channel monitoring\n"
        "/stop - ⛔ STOP EVERYTHING (removes connection & monitoring)\n"
        "/start - Restart the bot\n"
        "/status - Check bot status\n\n"
        "Phone Auto-Format:\n"
        "7510077046 -> +917510077046"
    )
    
    await safe_send_message(message.chat.id, welcome_text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=['addfirebase'])
async def add_firebase_command(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(message.chat.id, "❌ Access Denied! Admin only.")
        return
    
    if get_user_connection():
        await safe_send_message(
            message.chat.id,
            "⚠️ You already have a Firebase connection configured!\n\n"
            "To replace it, use /removefirebase first.\n"
            "Or use /stop to remove everything."
        )
        return
    
    user_sessions[user_id] = {
        'step': 'awaiting_firebase_url',
        'chat_id': message.chat.id
    }
    
    await safe_send_message(
        message.chat.id,
        "🔗 **Add Firebase Connection (One-time Setup)**\n\n"
        "Please enter your **Firebase URL**:\n"
        "Example: https://your-project.firebaseio.com\n\n"
        "⚠️ Make sure the URL ends with .firebaseio.com or similar"
    )

@bot.message_handler(commands=['removefirebase'])
async def remove_firebase_command(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(message.chat.id, "❌ Access Denied! Admin only.")
        return
    
    if not get_user_connection():
        await safe_send_message(message.chat.id, "❌ No Firebase connection found!")
        return
    
    if delete_user_connection():
        await safe_send_message(
            message.chat.id,
            "✅ **Firebase Connection Removed!**\n\n"
            "Your Firebase configuration has been deleted.\n"
            "Use /addfirebase to add a new one."
        )
    else:
        await safe_send_message(message.chat.id, "❌ Failed to remove connection.")

@bot.message_handler(commands=['stop'])
async def stop_everything(message):
    global monitoring_active, auto_fetch_tokens, chat_configs, client_message_tracking, PROCESSED_MESSAGES, message_store, user_firebase_config
    
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(message.chat.id, "❌ Access Denied! Admin only.")
        return
    
    monitoring_active = False
    
    auto_fetch_tokens.clear()
    chat_configs.clear()
    client_message_tracking.clear()
    PROCESSED_MESSAGES.clear()
    channel_status_messages.clear()
    message_store.clear()
    
    delete_user_connection()
    user_firebase_config = None
    
    if os.path.exists(USER_CONNECTION_FILE):
        try:
            os.remove(USER_CONNECTION_FILE)
            print(f"🗑️ Removed connection file: {USER_CONNECTION_FILE}")
        except:
            pass
    
    stop_text = (
        f"🛑 **Bot STOPPED - Everything Removed!**\n\n"
        f"✅ Monitoring stopped\n"
        f"✅ All configurations cleared\n"
        f"✅ Firebase connection removed\n"
        f"✅ Connection file deleted\n"
        f"✅ All message tracking cleared\n"
        f"✅ All processed messages cleared\n\n"
        f"📊 Status:\n"
        f"• Firebase Connection: ❌ Removed\n"
        f"• Auto-Fetch: ❌ Disabled\n"
        f"• Message Monitoring: ❌ Stopped\n"
        f"• Configured Chats: 0\n"
        f"• Tracking Clients: 0\n\n"
        f"🔄 To start fresh, use:\n"
        f"/addfirebase - Add Firebase connection\n"
        f"/forcechannel - Setup channel\n\n"
        f"ℹ️ Bot is fully reset. All data removed."
    )
    
    await safe_send_message(
        message.chat.id,
        stop_text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔄 Start Fresh", callback_data="start_fresh"),
            InlineKeyboardButton("📊 Check Status", callback_data="check_status")
        )
    )
    
    try:
        await safe_send_message(CHANNEL_ID, 
            f"🛑 **Bot STOPPED - Everything Removed!**\n\n"
            f"Firebase connection removed.\n"
            f"All monitoring stopped.\n"
            f"Use /addfirebase to start fresh."
        )
    except:
        pass
    
    log_monitor("🛑 Bot stopped - All data removed (connection, configs, monitoring)")

@bot.callback_query_handler(func=lambda call: call.data == "start_fresh")
async def start_fresh_callback(call):
    user_id = call.from_user.id
    
    if user_id != ADMIN_ID:
        await bot.answer_callback_query(call.id, "Access Denied!")
        return
    
    await bot.answer_callback_query(call.id, "✅ Starting Fresh!")
    
    await safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        f"🔄 **Starting Fresh!**\n\n"
        f"Everything has been reset.\n\n"
        f"Next steps:\n"
        f"1. /addfirebase - Add your Firebase connection\n"
        f"2. /forcechannel - Setup channel monitoring\n\n"
        f"Bot is ready for fresh setup!",
        parse_mode='HTML'
    )

@bot.message_handler(commands=['forcechannel'])
async def force_channel_setup(message):
    global monitoring_active
    
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(message.chat.id, "❌ Access Denied! Admin only.")
        return
    
    connection = get_user_connection()
    if not connection:
        await safe_send_message(
            message.chat.id,
            "❌ No Firebase connection found!\n\n"
            "Please add your Firebase connection first:\n"
            "/addfirebase\n\n"
            "Then use /forcechannel again."
        )
        return
    
    if not monitoring_active:
        monitoring_active = True
        log_monitor("🔄 Monitoring auto-started by /forcechannel")
    
    user_sessions[user_id] = {
        'step': 'awaiting_channel_client_id',
        'chat_id': message.chat.id
    }
    
    await safe_send_message(message.chat.id, 
        f"📨 Channel Setup for {CHANNEL_ID}\n\n"
        "Please enter the Client ID for the channel:\n"
        "Example: 366d2648739b8a58\n\n"
        "Bot will monitor and display ALL new messages in the channel!"
    )

@bot.message_handler(commands=['send'])
async def send_sms_command(message):
    global monitoring_active
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(chat_id, "❌ Access Denied! Admin only.")
        return
    
    connection = get_user_connection()
    if not connection:
        await safe_send_message(
            chat_id,
            "❌ No Firebase connection found!\n\n"
            "Please add your Firebase connection first:\n"
            "/addfirebase\n\n"
            "Then use /send again."
        )
        return
    
    if not monitoring_active:
        monitoring_active = True
        log_monitor("🔄 Monitoring auto-started by /send")
    
    user_sessions[user_id] = {
        'step': 'awaiting_client_id',
        'chat_id': chat_id
    }
    
    await safe_send_message(chat_id, 
        "📨 Enter Client ID\n\n"
        "Please enter the Client ID to send SMS to:\n"
        "Example: 366d2648739b8a58\n\n"
        "⚡ Bot will search your Firebase connection for the client!"
    )

@bot.callback_query_handler(func=lambda call: call.data == "add_connection")
async def add_connection_callback(call):
    user_id = call.from_user.id
    
    if user_id != ADMIN_ID:
        await bot.answer_callback_query(call.id, "Access Denied!")
        return
    
    if get_user_connection():
        await safe_edit_message(
            call.message.chat.id,
            call.message.message_id,
            "⚠️ You already have a Firebase connection configured!\n\n"
            "To replace it, use /removefirebase first.\n"
            "Or use /stop to remove everything.",
            InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
            )
        )
        await bot.answer_callback_query(call.id)
        return
    
    user_sessions[user_id] = {
        'step': 'awaiting_firebase_url',
        'chat_id': call.message.chat.id
    }
    
    await safe_edit_message(
        call.message.chat.id,
        call.message.message_id,
        "🔗 **Add Firebase Connection (One-time Setup)**\n\n"
        "Please enter your **Firebase URL**:\n"
        "Example: https://your-project.firebaseio.com\n\n"
        "⚠️ Make sure the URL ends with .firebaseio.com or similar"
    )
    await bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "auto_fetch_toggle")
async def toggle_auto_fetch(call):
    user_id = call.from_user.id
    
    if user_id != ADMIN_ID:
        await bot.answer_callback_query(call.id, "Access Denied!")
        return
    
    chat_id = call.message.chat.id
    
    if chat_id not in auto_fetch_tokens:
        auto_fetch_tokens[chat_id] = {
            'enabled': True,
            'monitor': True
        }
        status = "✅ ENABLED"
        auto_fetch_tokens[chat_id]['enabled'] = True
    else:
        current = auto_fetch_tokens[chat_id]['enabled']
        auto_fetch_tokens[chat_id]['enabled'] = not current
        status = "✅ ENABLED" if auto_fetch_tokens[chat_id]['enabled'] else "❌ DISABLED"
    
    if chat_id in chat_configs and auto_fetch_tokens[chat_id]['enabled']:
        config = chat_configs[chat_id]
        extra_info = f"\n\n📱 Client: {config.get('client_id')}\n📱 Client Name: {config.get('client_name', 'Unknown')}\n📱 SIM: {config.get('sim_number', 1)}"
    else:
        extra_info = "\n\n⚠️ No client configured! Use /send to setup."
    
    await safe_edit_message(
        chat_id,
        call.message.message_id,
        f"🤖 Auto-Fetch Mode: {status}\n\n"
        f"📱 Bot will {'now' if auto_fetch_tokens[chat_id]['enabled'] else 'no longer'} monitor this chat for SMS patterns.{extra_info}\n\n"
        f"To toggle again, use the button below.",
        InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔄 Toggle Auto-Fetch", callback_data="auto_fetch_toggle"),
            InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
        )
    )
    
    await bot.answer_callback_query(call.id, f"Auto-Fetch {status}")

@bot.channel_post_handler(func=lambda message: True)
async def handle_channel_post(message):
    global monitoring_active
    
    if not monitoring_active:
        log_channel("⏸️ Monitoring is stopped, ignoring channel post")
        return
    
    try:
        chat_id = message.chat.id
        text = message.text.strip() if message.text else ""
        
        log_channel(f"📩 Channel post received from {chat_id}")
        log_channel(f"📝 Message: {text[:200]}...")
        
        print(f"\n{'='*60}")
        print(f"📢 CHANNEL POST DETECTED")
        print(f"📢 Channel ID: {chat_id}")
        print(f"📢 Our Channel: {CHANNEL_ID}")
        print(f"📝 Message: {text}")
        print(f"{'='*60}\n")
        
        if chat_id != CHANNEL_ID:
            log_channel(f"⚠️ Channel {chat_id} is not monitored (expected {CHANNEL_ID})")
            return
        
        if message.message_id in PROCESSED_MESSAGES:
            log_channel(f"⏭️ Message {message.message_id} already processed")
            return
        PROCESSED_MESSAGES.add(message.message_id)
        
        if len(PROCESSED_MESSAGES) > 1000:
            PROCESSED_MESSAGES.clear()
        
        if chat_id not in auto_fetch_tokens:
            log_channel(f"⚠️ Auto-fetch not configured for channel {chat_id}")
            if chat_id in chat_configs:
                auto_fetch_tokens[chat_id] = {
                    'enabled': True,
                    'monitor': True
                }
                log_channel(f"✅ Auto-fetch auto-enabled for channel {chat_id}")
            else:
                log_channel(f"❌ No config found for channel {chat_id}")
                await safe_send_message(chat_id, 
                    "⚠️ Bot is not configured for this channel!\n"
                    "Please contact the admin to setup the bot."
                )
                return
        
        if not auto_fetch_tokens[chat_id]['enabled']:
            log_channel(f"⏸️ Auto-fetch is disabled for channel {chat_id}")
            return
        
        if chat_id not in chat_configs:
            log_channel(f"❌ No config found for channel {chat_id}")
            await safe_send_message(ADMIN_ID, 
                f"⚠️ Auto-Fetch is enabled for channel {CHANNEL_ID} but no client configured!\n"
                f"Please use /forcechannel to setup client for this channel."
            )
            return
        
        config = chat_configs[chat_id]
        log_channel(f"✅ Config found: Client={config.get('client_id')}, SIM={config.get('sim_number', 1)}")
        
        phone, msg_text = extract_phone_message(text)
        
        if phone and msg_text:
            log_channel(f"✅ Extracted: Phone={phone}, Message={msg_text[:50]}...")
            
            client_id = config['client_id']
            client_name = config.get('client_name', 'Unknown')
            connection = config['connection']
            sim_number = config.get('sim_number', 1)
            conn_name = connection.get('name', 'Unknown')
            
            log_channel(f"🔄 Sending SMS via SIM {sim_number} to {phone}")
            
            loading_msg = await safe_send_message(chat_id, 
                f"🔄 Sending SMS via SIM {sim_number}...\n"
                f"📱 To: {phone}\n"
                f"📱 Client: {client_id}\n"
                f"📱 Client Name: {client_name}\n"
                f"🔗 Connection: {conn_name}\n"
                f"📝 Message: {msg_text[:50]}...\n"
                "⏳ Please wait..."
            )
            
            if loading_msg:
                formatted_phone = format_phone(phone)
                success, result = await send_sms_via_firebase(connection, client_id, formatted_phone, msg_text, sim_number)
                
                if success:
                    status_msg = await safe_send_message(
                        chat_id,
                        f"🔍 Monitoring for new messages...\n"
                        f"📱 Client: {client_id}\n"
                        f"📱 Client Name: {client_name}\n"
                        f"⏳ Waiting for new messages...\n"
                        f"🔄 Checking every {monitor_check_interval} seconds..."
                    )
                    
                    if status_msg:
                        channel_status_messages[chat_id] = status_msg.message_id
                    
                    await safe_edit_message(
                        chat_id,
                        loading_msg.message_id,
                        f"✅ SMS Sent Successfully!\n\n"
                        f"📱 From Client: {client_id}\n"
                        f"📱 Client Name: {client_name}\n"
                        f"🔗 Connection: {conn_name}\n"
                        f"📱 SIM: {sim_number}\n"
                        f"📱 To: {formatted_phone}\n"
                        f"📝 Message: {msg_text[:100]}...\n\n"
                        f"🔍 Monitoring for new messages...\n"
                        f"📌 Initialized tracking for client {client_id}"
                    )
                    
                    log_monitor(f"🔧 Initializing message tracking for client {client_id}")
                    success_init = await init_message_tracking(client_id, connection)
                    
                    if success_init:
                        log_monitor(f"✅ Message tracking initialized for client {client_id}")
                    else:
                        log_monitor(f"❌ Failed to initialize tracking for client {client_id}")
                    
                    await safe_send_message(ADMIN_ID,
                        f"✅ SMS Sent from Channel\n"
                        f"📱 From Client: {client_id} ({client_name})\n"
                        f"📱 To: {formatted_phone}\n"
                        f"📝 Message: {msg_text[:100]}...\n"
                        f"🔍 Message Monitoring active!"
                    )
                    
                else:
                    await safe_edit_message(
                        chat_id,
                        loading_msg.message_id,
                        f"❌ SMS Failed!\n\n"
                        f"📱 From Client: {client_id}\n"
                        f"📱 To: {formatted_phone}\n"
                        f"Error: {result}\n\n"
                        f"Please check client status."
                    )
        else:
            log_channel(f"⚠️ Could not extract phone and message from channel post")
            await safe_send_message(chat_id, 
                "⚠️ Could not detect phone number and message.\n\n"
                "Please use format:\n"
                "phone | message\n"
                "Example: 8207884154 | Your message here"
            )
            
    except Exception as e:
        log_channel(f"❌ Error in channel post handler: {str(e)}")
        import traceback
        traceback.print_exc()

@bot.message_handler(func=lambda message: True)
async def handle_messages(message):
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""
    chat_id = message.chat.id
    chat_type = message.chat.type
    
    print("====================")
    print("Sender ID:", message.from_user.id)
    print("Is Bot:", message.from_user.is_bot)
    print("Name:", message.from_user.first_name)
    print("Text:", message.text[:200] if message.text else "None")    
        
    if message.message_id in PROCESSED_MESSAGES:
        return
    PROCESSED_MESSAGES.add(message.message_id)
    
    if len(PROCESSED_MESSAGES) > 1000:
        PROCESSED_MESSAGES.clear()
    
    if chat_type == 'private' and user_id != ADMIN_ID:
        await safe_send_message(chat_id, "❌ Access Denied! Admin only.")
        return
    
    if text.startswith('/'):
        return
    
    if user_id == ADMIN_ID:
        if text == "📋 List Connections":
            await list_connections(message)
            return
        elif text == "📨 Send SMS":
            await send_sms_command(message)
            return
        elif text == "📊 Status":
            await check_status(message)
            return
    
    if user_id in user_sessions:
        session = user_sessions[user_id]
        step = session.get('step')
        
        if step == 'awaiting_firebase_url':
            url = text.strip()
            
            if not url.startswith('http://') and not url.startswith('https://'):
                await safe_send_message(chat_id, 
                    "❌ Invalid URL!\n\n"
                    "URL must start with http:// or https://\n"
                    "Example: https://your-project.firebaseio.com\n\n"
                    "Please try again:"
                )
                return
            
            if url.endswith('/'):
                url = url[:-1]
            
            user_sessions[user_id]['firebase_url'] = url
            user_sessions[user_id]['step'] = 'awaiting_firebase_key'
            
            await safe_send_message(
                chat_id,
                "🔑 **Enter Firebase Secret Key**\n\n"
                "Please enter your Firebase Secret Key:\n"
                "Example: AIzaSyDmN8Lz...\n\n"
                "⚠️ This is the auth key for your Firebase database."
            )
            return
        
        elif step == 'awaiting_firebase_key':
            key = text.strip()
            
            if len(key) < 10:
                await safe_send_message(
                    chat_id,
                    "❌ Invalid Key!\n\n"
                    "The key seems too short. Please enter a valid Firebase Secret Key.\n\n"
                    "Please try again:"
                )
                return
            
            url = session.get('firebase_url')
            
            loading_msg = await safe_send_message(chat_id, "🔄 Testing Firebase connection...")
            success, message_text = await check_firebase_connection(url, key)
            
            if success:
                set_user_connection(url, key)
                
                await safe_edit_message(
                    chat_id,
                    loading_msg.message_id,
                    f"✅ **Firebase Connection Added!**\n\n"
                    f"🔗 URL: {url}\n"
                    f"🔑 Key: {key[:10]}...{key[-5:]}\n\n"
                    f"✅ Connection test: {message_text}\n\n"
                    f"📢 You can now use:\n"
                    f"/forcechannel - Setup channel monitoring\n"
                    f"/send - Send SMS\n"
                    f"/status - Check status\n\n"
                    f"⚠️ This is a ONE-TIME setup. To remove, use /stop",
                    get_main_keyboard()
                )
            else:
                await safe_edit_message(
                    chat_id,
                    loading_msg.message_id,
                    f"❌ **Connection Failed!**\n\n"
                    f"{message_text}\n\n"
                    f"Please check your URL and Key and try again.\n"
                    f"Use /addfirebase to restart."
                )
            
            if user_id in user_sessions:
                del user_sessions[user_id]
            return
        
        elif step == 'awaiting_client_id':
            client_id = text
            
            connection = get_user_connection()
            if not connection:
                await safe_send_message(chat_id, "❌ No Firebase connection found! Use /addfirebase first.")
                if user_id in user_sessions:
                    del user_sessions[user_id]
                return
            
            loading_msg = await safe_send_message(chat_id, "🔄 Searching for client in your Firebase connection...")
            if not loading_msg:
                return
            
            result = await check_client_in_connection(client_id)
            
            if result['found']:
                data = result['data']
                client_name = data.get('modelName', data.get('name', client_id))
                conn_name = result['conn_name']
                
                chat_id_for_config = session.get('chat_id', chat_id)
                
                chat_configs[chat_id_for_config] = {
                    'client_id': client_id,
                    'connection': connection,
                    'client_name': client_name,
                    'conn_name': conn_name,
                    'sim_number': 1
                }
                
                await safe_edit_message(
                    chat_id,
                    loading_msg.message_id,
                    f"✅ Client Found!\n\n"
                    f"🆔 ID: {client_id}\n"
                    f"📱 Name: {client_name}\n"
                    f"🔗 Connection: {conn_name}\n"
                    f"📊 Status: {'✅ Online' if data.get('status') else '❌ Offline'}\n"
                    f"🔋 Battery: {data.get('battery', 'Unknown')}\n\n"
                    f"📱 Select SIM:",
                    get_sim_keyboard()
                )
                
                user_sessions[user_id]['client_id'] = client_id
                user_sessions[user_id]['client_name'] = client_name
                user_sessions[user_id]['connection'] = connection
                user_sessions[user_id]['step'] = 'awaiting_sim_selection'
                
            else:
                await safe_edit_message(
                    chat_id,
                    loading_msg.message_id,
                    f"❌ Client Not Found!\n\n"
                    f"Client ID: {client_id}\n"
                    f"Please check the Client ID and try again.\n\n"
                    f"Use /send to start over."
                )
                if user_id in user_sessions:
                    del user_sessions[user_id]
            return
        
        elif step == 'awaiting_sim_selection':
            return
        
        elif step == 'awaiting_sms' and chat_type == 'private':
            phone, msg_text = extract_phone_message(text)
            
            if not phone or not msg_text:
                await safe_send_message(chat_id, 
                    "❌ Invalid format!\n\n"
                    "Please use:\n"
                    "phone | message\n\n"
                    "Example:\n"
                    "8207884154 | Your SMS message here"
                )
                return
            
            client_id = session.get('client_id')
            client_name = session.get('client_name', 'Unknown')
            connection = session.get('connection')
            sim_number = session.get('sim_number', 1)
            conn_name = connection.get('name', 'Unknown')
            
            if not connection:
                await safe_send_message(chat_id, "❌ No connection found! Please start over with /send")
                if user_id in user_sessions:
                    del user_sessions[user_id]
                return
            
            loading_msg = await safe_send_message(chat_id, 
                f"🔄 Sending SMS via SIM {sim_number}...\n"
                f"📱 Client: {client_id}\n"
                f"📱 Client Name: {client_name}\n"
                f"🔗 Connection: {conn_name}\n"
                "⏳ Please wait..."
            )
            if not loading_msg:
                return
            
            formatted_phone = format_phone(phone)
            success, result = await send_sms_via_firebase(connection, client_id, formatted_phone, msg_text, sim_number)
            
            if success:
                result_text = (
                    f"✅ SMS Sent Successfully!\n\n"
                    f"📱 From Client: {client_id}\n"
                    f"📱 Client Name: {client_name}\n"
                    f"🔗 Connection: {conn_name}\n"
                    f"📱 SIM: {sim_number}\n"
                    f"📱 To: {formatted_phone}\n"
                    f"📝 Message: {msg_text[:50]}...\n\n"
                    f"📨 Send another SMS?\n"
                    f"Enter another Client ID or use /send"
                )
            else:
                result_text = (
                    f"❌ SMS Failed!\n\n"
                    f"📱 From Client: {client_id}\n"
                    f"📱 To: {formatted_phone}\n"
                    f"Error: {result}\n\n"
                    f"📨 Try again with /send"
                )
            
            await safe_edit_message(
                chat_id,
                loading_msg.message_id,
                result_text,
                get_main_keyboard()
            )
            
            if user_id in user_sessions:
                del user_sessions[user_id]
            return
        
        elif step == 'awaiting_channel_client_id':
            client_id = text
            
            connection = get_user_connection()
            if not connection:
                await safe_send_message(chat_id, "❌ No Firebase connection found! Use /addfirebase first.")
                if user_id in user_sessions:
                    del user_sessions[user_id]
                return
            
            loading_msg = await safe_send_message(chat_id, "🔄 Searching for client...")
            if not loading_msg:
                return
            
            result = await check_client_in_connection(client_id)
            
            if result['found']:
                data = result['data']
                client_name = data.get('modelName', data.get('name', client_id))
                conn_name = result['conn_name']
                
                user_sessions[user_id]['client_id'] = client_id
                user_sessions[user_id]['client_name'] = client_name
                user_sessions[user_id]['connection'] = connection
                user_sessions[user_id]['conn_name'] = conn_name
                user_sessions[user_id]['step'] = 'awaiting_channel_sim_selection'
                
                await safe_edit_message(
                    chat_id,
                    loading_msg.message_id,
                    f"✅ Client Found!\n\n"
                    f"🆔 ID: {client_id}\n"
                    f"📱 Name: {client_name}\n"
                    f"🔗 Connection: {conn_name}\n"
                    f"📊 Status: {'✅ Online' if data.get('status') else '❌ Offline'}\n"
                    f"🔋 Battery: {data.get('battery', 'Unknown')}\n\n"
                    f"📱 Select SIM for channel setup:",
                    get_channel_sim_keyboard()
                )
                
            else:
                await safe_edit_message(
                    chat_id,
                    loading_msg.message_id,
                    f"❌ Client Not Found!\n\n"
                    f"Client ID: {client_id}\n"
                    f"Please check the client ID and try again.\n\n"
                    f"Use /forcechannel to start over."
                )
                if user_id in user_sessions:
                    del user_sessions[user_id]
            return

@bot.callback_query_handler(func=lambda call: True)
async def handle_callback(call):
    user_id = call.from_user.id
    
    if user_id != ADMIN_ID:
        await bot.answer_callback_query(call.id, "Access Denied!")
        return
    
    data = call.data
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if data.startswith("copy_otp_"):
        try:
            otp_code = data.replace("copy_otp_", "", 1)
            
            await safe_send_message(
                chat_id,
                f"🔑 <b>OTP Code:</b> <code>{otp_code}</code>\n\n"
                f"✅ Click on the message and copy the OTP code!",
                parse_mode='HTML'
            )
            
            await bot.answer_callback_query(
                call.id, 
                f"✅ OTP copied: {otp_code}",
                show_alert=True
            )
            return
        except Exception as e:
            await bot.answer_callback_query(call.id, f"Error copying: {str(e)}")
            return
    
    if data.startswith("copy_full_"):
        try:
            import hashlib
            msg_hash = data.replace("copy_full_", "", 1)
            
            full_message = message_store.get(msg_hash, "")
            
            if not full_message:
                message_text = call.message.text
                match = re.search(r'<code>(.*?)</code>', message_text, re.DOTALL)
                if match:
                    full_message = match.group(1)
                else:
                    full_message = "Could not retrieve full message"
            
            await safe_send_message(
                chat_id,
                f"📝 <b>Full SMS:</b>\n\n"
                f"<code>{escape_html(full_message)}</code>\n\n"
                f"✅ Click on the message and copy the full SMS!",
                parse_mode='HTML'
            )
            
            await bot.answer_callback_query(
                call.id, 
                f"📋 Full SMS copied!",
                show_alert=True
            )
            return
        except Exception as e:
            await bot.answer_callback_query(call.id, f"Error copying: {str(e)}")
            return
    
    if data == "start_fresh":
        await bot.answer_callback_query(call.id, "✅ Starting Fresh!")
        await safe_edit_message(
            chat_id,
            message_id,
            f"🔄 **Starting Fresh!**\n\n"
            f"Everything has been reset.\n\n"
            f"Next steps:\n"
            f"1. /addfirebase - Add your Firebase connection\n"
            f"2. /forcechannel - Setup channel monitoring\n\n"
            f"Bot is ready for fresh setup!",
            parse_mode='HTML'
        )
        return
    
    if data == "back_to_menu":
        await safe_edit_message(
            chat_id,
            message_id,
            "🔙 Back to Main Menu",
            get_main_keyboard()
        )
        await bot.answer_callback_query(call.id)
        return
    
    if data == "list_connections":
        connection = get_user_connection()
        if not connection:
            text = "📭 No Firebase connection found\n\nUse /addfirebase to add one."
        else:
            text = f"📋 Your Firebase Connection\n\n"
            text += f"🔗 URL: {connection.get('url')}\n"
            text += f"🔑 Key: {connection.get('key')[:10]}...{connection.get('key')[-5:]}\n"
            text += f"📛 Name: {connection.get('name', 'Unnamed')}\n"
            text += f"📅 Added: {connection.get('added_at', 'Unknown')[:19]}\n\n"
            text += f"💡 To remove: /stop (removes everything)"
        
        await safe_edit_message(
            chat_id,
            message_id,
            text,
            InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
            )
        )
        await bot.answer_callback_query(call.id)
        return
    
    if data == "send_sms":
        connection = get_user_connection()
        if not connection:
            await safe_edit_message(
                chat_id,
                message_id,
                "❌ No Firebase connection found!\n\n"
                "Please add your Firebase connection first:\n"
                "/addfirebase\n\n"
                "Then use /send again.",
                InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
                )
            )
            await bot.answer_callback_query(call.id)
            return
        
        try:
            await bot.delete_message(chat_id, message_id)
        except:
            pass
        msg = await safe_send_message(
            chat_id,
            "📨 Starting SMS Send Flow...\n\n"
            "Please enter the Client ID:"
        )
        if msg:
            user_sessions[user_id] = {
                'step': 'awaiting_client_id',
                'chat_id': chat_id,
                'msg_id': msg.message_id
            }
        await bot.answer_callback_query(call.id)
        return
    
    if data == "check_status":
        connection = get_user_connection()
        has_connection = connection is not None
        
        status_text = (
            f"📊 Bot Status\n\n"
            f"🔗 Firebase Connection: {'✅ Connected' if has_connection else '❌ Not Configured'}\n"
            f"👤 Admin ID: {ADMIN_ID}\n"
            f"📢 Channel ID: {CHANNEL_ID}\n"
            f"🤖 Bot Status: {'✅ Running' if monitoring_active else '⏸️ Stopped'}\n"
            f"📝 Log File: {LOG_FILE}\n"
            f"📝 Channel Log: {CHANNEL_LOG_FILE}\n"
            f"📝 Monitor Log: {MONITOR_LOG_FILE}\n\n"
            f"📱 Auto-Fetch Chats: {len(auto_fetch_tokens)}\n"
            f"📱 Configured Chats: {len(chat_configs)}\n"
            f"🔍 Message Monitoring: {'✅ Active' if len(client_message_tracking) > 0 else '❌ Inactive'}\n"
            f"📊 Tracking Clients: {len(client_message_tracking)}\n"
            f"📝 Processed Messages: 0 (tracking by ID only)\n"
            f"🔄 Monitoring Status: {'🟢 ACTIVE' if monitoring_active else '🔴 STOPPED'}\n"
        )
        
        if connection:
            status_text += f"\n📡 Your Firebase Connection:\n"
            status_text += f"🔗 URL: {connection.get('url')}\n"
            status_text += f"🔑 Key: {connection.get('key')[:10]}...\n"
        
        if CHANNEL_ID in chat_configs:
            config = chat_configs[CHANNEL_ID]
            status_text += f"\n\n📢 Channel {CHANNEL_ID} is configured!\n"
            status_text += f"📱 Client: {config.get('client_id')}\n"
            status_text += f"📱 Client Name: {config.get('client_name', 'Unknown')}\n"
            status_text += f"📱 SIM: {config.get('sim_number', 1)}\n"
            status_text += f"🔄 Auto-Fetch: {'✅ Active' if auto_fetch_tokens.get(CHANNEL_ID, {}).get('enabled', False) else '❌ Inactive'}"
            
            client_id = config.get('client_id')
            if client_id in client_message_tracking:
                tracking = client_message_tracking[client_id]
                status_text += f"\n📊 Message Count: {tracking.get('initial_count', 0)}"
                status_text += f"\n🕐 Last Checked: {tracking.get('last_checked', 'Never')}"
        else:
            status_text += f"\n\n📢 Channel {CHANNEL_ID} is NOT configured!\n"
            status_text += f"Use /forcechannel to setup client for the channel."
        
        await safe_edit_message(
            chat_id,
            message_id,
            status_text,
            InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
            )
        )
        await bot.answer_callback_query(call.id)
        return
    
    if data.startswith("sim_") and not data.startswith("channel_sim_"):
        sim_number = int(data.split("_")[1])
        
        if user_id not in user_sessions:
            await bot.answer_callback_query(call.id, "Session expired! Use /send to start over.")
            return
        
        session = user_sessions[user_id]
        
        if session.get('step') != 'awaiting_sim_selection':
            await bot.answer_callback_query(call.id, "Please use /send to start over.")
            return
        
        user_sessions[user_id]['sim_number'] = sim_number
        user_sessions[user_id]['step'] = 'awaiting_sms'
        
        chat_id_for_config = session.get('chat_id', chat_id)
        if chat_id_for_config in chat_configs:
            chat_configs[chat_id_for_config]['sim_number'] = sim_number
        
        if chat_id_for_config not in auto_fetch_tokens:
            auto_fetch_tokens[chat_id_for_config] = {
                'enabled': True,
                'monitor': True
            }
        
        await safe_edit_message(
            chat_id,
            message_id,
            f"✅ Setup Complete!\n\n"
            f"📱 Client: {session.get('client_id')}\n"
            f"📱 Client Name: {session.get('client_name')}\n"
            f"🔗 Connection: {session.get('connection', {}).get('name', 'Unknown')}\n"
            f"📱 SIM: {sim_number}\n\n"
            f"🤖 Auto-Fetch is now ACTIVE!\n\n"
            f"📨 Post in channel with:\n"
            f"phone | message\n\n"
            f"🔍 Bot will monitor for ALL new messages and display them in the channel!",
            InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu"),
                InlineKeyboardButton("🔄 Toggle Auto-Fetch", callback_data="auto_fetch_toggle")
            )
        )
        
        if user_id in user_sessions:
            del user_sessions[user_id]
        
        await bot.answer_callback_query(call.id, f"Setup Complete! SIM {sim_number}")
        return
    
    if data.startswith("channel_sim_"):
        sim_number = int(data.split("_")[2])
        
        if user_id not in user_sessions:
            await bot.answer_callback_query(call.id, "Session expired! Use /forcechannel to start over.")
            return
        
        session = user_sessions[user_id]
        
        if session.get('step') != 'awaiting_channel_sim_selection':
            await bot.answer_callback_query(call.id, "Please use /forcechannel to start over.")
            return
        
        client_id = session.get('client_id')
        client_name = session.get('client_name')
        connection = session.get('connection')
        conn_name = session.get('conn_name')
        
        chat_configs[CHANNEL_ID] = {
            'client_id': client_id,
            'connection': connection,
            'client_name': client_name,
            'conn_name': conn_name,
            'sim_number': sim_number
        }
        
        auto_fetch_tokens[CHANNEL_ID] = {
            'enabled': True,
            'monitor': True
        }
        
        success_init = await init_message_tracking(client_id, connection)
        
        await safe_edit_message(
            chat_id,
            message_id,
            f"✅ Channel Setup Complete!\n\n"
            f"📢 Channel ID: {CHANNEL_ID}\n"
            f"📱 Client ID: {client_id}\n"
            f"📱 Client Name: {client_name}\n"
            f"🔗 Connection: {conn_name}\n"
            f"📱 SIM: {sim_number}\n"
            f"🔄 Auto-Fetch: ✅ ACTIVE\n"
            f"🔍 Message Monitoring: {'✅ ACTIVE' if success_init else '⚠️ INITIALIZING'}\n\n"
            f"📝 How it works:\n"
            f"1. Post SMS in channel: phone | message\n"
            f"2. Bot sends the SMS via client\n"
            f"3. Bot stores current message count\n"
            f"4. Bot monitors for ALL new messages continuously\n"
            f"5. Each new message is displayed with full SMS and copy buttons!\n\n"
            f"🔄 Bot will now automatically check for new messages every {monitor_check_interval} seconds.\n\n"
            f"🛑 Use /stop to remove EVERYTHING (connection + monitoring).",
            InlineKeyboardMarkup().add(
                InlineKeyboardButton("📊 Check Status", callback_data="check_status"),
                InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")
            )
        )
        
        try:
            await safe_send_message(CHANNEL_ID, 
                f"✅ Bot is now configured and monitoring!\n"
                f"📱 Client: {client_id} ({client_name})\n"
                f"📝 Post SMS format: phone | message\n"
                f"🔍 Monitoring for ALL new messages continuously...\n"
                f"🛑 Use /stop to remove EVERYTHING."
            )
        except:
            pass
        
        if user_id in user_sessions:
            del user_sessions[user_id]
        
        await bot.answer_callback_query(call.id, f"Channel configured! Message Monitoring active")
        return

def clean_message_text(text):
    """Clean and format message text"""
    if not text:
        return text
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Remove common prefixes
    text = re.sub(r'^(?:Message|Body)\s*:\s*', '', text, flags=re.IGNORECASE)
    
    # Remove "One-tap copy:" and everything after it (unless it's the only content)
    if 'One-tap copy:' in text:
        text = text.split('One-tap copy:')[0].strip()
    
    return text.strip()

def extract_phone_message(text):
    if not text:
        return None, None
    
    print(f"Extracting from: {text[:200]}...")
    
    # Clean the text first - remove common separators and emojis
    cleaned_text = re.sub(r'[━─═]', '', text)
    
    # 1. Try Intercepted Outgoing SMS pattern
    if re.search(r'Intercepted\s+Outgoing\s+SMS', cleaned_text, re.IGNORECASE):
        print("Found Intercepted Outgoing SMS pattern")
        
        # Extract phone
        to_match = re.search(r'To\s*(?:\([^)]*\))?\s*:\s*([\d\s\-\(\)\+]+)', cleaned_text, re.IGNORECASE)
        if to_match:
            phone = re.sub(r'[\s\-\(\)]', '', to_match.group(1))
            phone = re.sub(r'[^\d+]', '', phone)
            
            # Extract body
            body_match = re.search(r'Body\s*(?:\([^)]*\))?\s*:\s*(.+?)(?=\n|$)', cleaned_text, re.DOTALL)
            if body_match:
                msg_text = body_match.group(1).strip()
                msg_text = clean_message_text(msg_text)
                
                if len(phone) >= 10:
                    print(f"Found phone: {phone}")
                    print(f"Found body: {msg_text[:50]}...")
                    return phone, msg_text
    
    # 2. Try SMS Intercepted patterns
    sms_match = re.search(r'(?:📱\s*)?SMS\s*(?:@\w+\s*)?Intercepted', cleaned_text, re.IGNORECASE)
    if sms_match:
        print("Found SMS Intercepted pattern")
        
        # Extract phone - try multiple patterns
        phone = None
        
        # Pattern 1: Direct To: line
        to_match = re.search(r'(?:📞\s*)?To:\s*([\d\s\-\(\)\+]+)', cleaned_text)
        if to_match:
            phone = re.sub(r'[\s\-\(\)]', '', to_match.group(1))
            phone = re.sub(r'[^\d+]', '', phone)
        
        # Pattern 2: One-tap copy format within SMS Intercepted
        if not phone or len(phone) < 10:
            copy_match = re.search(r'One-tap copy:\s*([\d\s\-\(\)\+]+)\s*\|', cleaned_text)
            if copy_match:
                phone = re.sub(r'[\s\-\(\)]', '', copy_match.group(1))
                phone = re.sub(r'[^\d+]', '', phone)
        
        if phone and len(phone) >= 10:
            # Extract message - try multiple patterns
            msg_text = None
            
            # Pattern 1: Message: line
            msg_match = re.search(r'(?:💬\s*)?Message:\s*(.+?)(?=\n\s*(?:📋|One-tap|$))', cleaned_text, re.DOTALL)
            if msg_match:
                msg_text = msg_match.group(1).strip()
            else:
                # Pattern 2: Message: line with One-tap copy following
                msg_match = re.search(r'(?:💬\s*)?Message:\s*(.+?)(?=\s*One-tap copy:)', cleaned_text, re.DOTALL)
                if msg_match:
                    msg_text = msg_match.group(1).strip()
                else:
                    # Pattern 3: One-tap copy format
                    copy_match = re.search(r'One-tap copy:\s*[\d\s\-\(\)\+]+\s*\|\s*(.+)', cleaned_text, re.DOTALL)
                    if copy_match:
                        msg_text = copy_match.group(1).strip()
            
            if msg_text:
                msg_text = clean_message_text(msg_text)
                print(f"Found phone: {phone}")
                print(f"Found message: {msg_text[:50]}...")
                return phone, msg_text
    
    # 3. Try One-tap copy format (standalone)
    copy_match = re.search(r'One-tap copy:\s*([\d\s\-\(\)\+]+)\s*\|\s*(.+)', cleaned_text, re.DOTALL)
    if copy_match:
        phone = re.sub(r'[\s\-\(\)]', '', copy_match.group(1))
        phone = re.sub(r'[^\d+]', '', phone)
        msg_text = clean_message_text(copy_match.group(2).strip())
        
        if len(phone) >= 10:
            print(f"Found via One-tap copy: phone={phone}, msg={msg_text[:50]}...")
            return phone, msg_text
    
    # 4. Try phone | message format (simple pipe separator)
    if '|' in cleaned_text:
        parts = cleaned_text.split('|', 1)
        phone_part = parts[0].strip()
        msg_text = parts[1].strip()
        
        # Look for phone number pattern
        phone_match = re.search(r'([\+]?\d{10,15})', phone_part)
        if phone_match:
            phone = phone_match.group(1)
            msg_text = clean_message_text(msg_text)
            print(f"Found via pipe format: phone={phone}, msg={msg_text[:50]}...")
            return phone, msg_text
    
    # 5. Try format: phone number at start of line
    phone_match = re.search(r'^(\d{10})\s+(.+)', cleaned_text)
    if phone_match:
        phone = phone_match.group(1)
        msg_text = clean_message_text(phone_match.group(2).strip())
        print(f"Found via start format: phone={phone}, msg={msg_text[:50]}...")
        return phone, msg_text
    
    # 6. Try to find phone number anywhere and extract it with following text
    phone_match = re.search(r'([\+]?\d{10,15})\s+(.+)', cleaned_text)
    if phone_match:
        phone = phone_match.group(1)
        msg_text = clean_message_text(phone_match.group(2).strip())
        if len(phone) >= 10:
            print(f"Found via general pattern: phone={phone}, msg={msg_text[:50]}...")
            return phone, msg_text
    
    print("No phone number found")
    return None, None

@bot.message_handler(commands=['list'])
async def list_connections(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(message.chat.id, "❌ Access Denied! Admin only.")
        return
    
    connection = get_user_connection()
    
    if not connection:
        await safe_send_message(message.chat.id, "📭 No Firebase connection found.\n\nUse /addfirebase to add one.")
        return
    
    text = f"📋 Your Firebase Connection\n\n"
    text += f"🔗 URL: {connection.get('url')}\n"
    text += f"🔑 Key: {connection.get('key')[:10]}...{connection.get('key')[-5:]}\n"
    text += f"📛 Name: {connection.get('name', 'Unnamed')}\n"
    text += f"📅 Added: {connection.get('added_at', 'Unknown')[:19]}\n\n"
    text += f"💡 To remove everything: /stop"
    
    await safe_send_message(message.chat.id, text)

@bot.message_handler(commands=['status'])
async def check_status(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(message.chat.id, "❌ Access Denied! Admin only.")
        return
    
    connection = get_user_connection()
    has_connection = connection is not None
    
    status_text = (
        f"📊 Bot Status\n\n"
        f"🔗 Firebase Connection: {'✅ Connected' if has_connection else '❌ Not Configured'}\n"
        f"👤 Admin ID: {ADMIN_ID}\n"
        f"📢 Channel ID: {CHANNEL_ID}\n"
        f"🤖 Bot Status: {'✅ Running' if monitoring_active else '⏸️ Stopped'}\n"
        f"📝 SMS Log: {LOG_FILE}\n"
        f"📝 Channel Log: {CHANNEL_LOG_FILE}\n"
        f"📝 Monitor Log: {MONITOR_LOG_FILE}\n"
        f"📱 Auto-Fetch Chats: {len(auto_fetch_tokens)}\n"
        f"📱 Configured Chats: {len(chat_configs)}\n"
        f"🔍 Message Monitoring: {'✅ Active' if len(client_message_tracking) > 0 else '❌ Inactive'}\n"
        f"📊 Tracking Clients: {len(client_message_tracking)}\n"
        f"📝 Processed Messages: 0 (tracking by ID only)\n"
        f"🔄 Monitoring Status: {'🟢 ACTIVE' if monitoring_active else '🔴 STOPPED'}\n\n"
    )
    
    if connection:
        status_text += f"📡 Your Firebase Connection:\n"
        status_text += f"🔗 URL: {connection.get('url')}\n"
        status_text += f"🔑 Key: {connection.get('key')[:10]}...\n"
    
    if CHANNEL_ID in chat_configs:
        config = chat_configs[CHANNEL_ID]
        status_text += f"\n\n📢 Channel {CHANNEL_ID} is configured!\n"
        status_text += f"📱 Client: {config.get('client_id')}\n"
        status_text += f"📱 Client Name: {config.get('client_name', 'Unknown')}\n"
        status_text += f"📱 SIM: {config.get('sim_number', 1)}\n"
        status_text += f"🔄 Auto-Fetch: {'✅ Active' if auto_fetch_tokens.get(CHANNEL_ID, {}).get('enabled', False) else '❌ Inactive'}"
        
        client_id = config.get('client_id')
        if client_id in client_message_tracking:
            tracking = client_message_tracking[client_id]
            status_text += f"\n📊 Message Count: {tracking.get('initial_count', 0)}"
            status_text += f"\n🕐 Last Checked: {tracking.get('last_checked', 'Never')}"
            status_text += f"\n🔄 Status: {'✅ Active' if tracking.get('active', False) else '❌ Inactive'}"
    else:
        status_text += f"\n\n📢 Channel {CHANNEL_ID} is NOT configured!\n"
        status_text += f"Use /forcechannel to setup client for the channel."
    
    await safe_send_message(message.chat.id, status_text)

@bot.message_handler(commands=['help'])
async def help_command(message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(message.chat.id, "❌ Access Denied! Admin only.")
        return
    
    help_text = (
        f"🤖 SMS Bot with Message Display\n\n"
        f"Commands:\n"
        f"/start - Show main menu\n"
        f"/addfirebase - Add your Firebase connection (ONE-TIME SETUP)\n"
        f"/removefirebase - Remove your Firebase connection\n"
        f"/send - Setup client\n"
        f"/forcechannel - Setup channel for message monitoring\n"
        f"/stop - ⛔ STOP & REMOVE EVERYTHING (connection + monitoring)\n"
        f"/list - List your connection\n"
        f"/status - Check bot status\n"
        f"/help - Show this help\n\n"
        f"📱 How it works:\n"
        f"1. Admin: /addfirebase (Add your Firebase URL and Key) - ONE TIME\n"
        f"2. Admin: /forcechannel\n"
        f"3. Enter Client ID\n"
        f"4. Select SIM\n"
        f"5. Post in channel: phone | message\n"
        f"6. Bot sends SMS via client\n"
        f"7. Bot stores current message count\n"
        f"8. Bot continuously monitors for ALL new messages\n"
        f"9. Each new message is displayed with full SMS and copy buttons!\n\n"
        f"🛑 To stop and remove EVERYTHING: /stop\n"
        f"🔄 To restart fresh: /start then /addfirebase\n\n"
        f"📝 Logs:\n"
        f"Channel logs: {CHANNEL_LOG_FILE}\n"
        f"SMS logs: {LOG_FILE}\n"
        f"Monitor logs: {MONITOR_LOG_FILE}"
    )
    await safe_send_message(message.chat.id, help_text)

@bot.message_handler(commands=['autofetch'])
async def auto_fetch_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    if user_id != ADMIN_ID:
        await safe_send_message(chat_id, "❌ Access Denied! Admin only.")
        return
    
    if chat_id not in auto_fetch_tokens:
        auto_fetch_tokens[chat_id] = {
            'enabled': True,
            'monitor': True
        }
        status = "✅ ENABLED"
    else:
        current = auto_fetch_tokens[chat_id]['enabled']
        auto_fetch_tokens[chat_id]['enabled'] = not current
        status = "✅ ENABLED" if auto_fetch_tokens[chat_id]['enabled'] else "❌ DISABLED"
    
    if chat_id in chat_configs and auto_fetch_tokens[chat_id]['enabled']:
        config = chat_configs[chat_id]
        extra = f"\n\n📱 Client: {config.get('client_id')}\n📱 Client Name: {config.get('client_name', 'Unknown')}\n📱 SIM: {config.get('sim_number', 1)}"
    else:
        extra = "\n\n⚠️ No client configured! Use /send to setup."
    
    await safe_send_message(chat_id, f"🤖 Auto-Fetch Mode: {status}{extra}\n\n"
        f"📱 Bot will {'now' if auto_fetch_tokens[chat_id]['enabled'] else 'no longer'} monitor this chat for SMS patterns.\n"
        f"To toggle again: /autofetch")

async def start_bot():
    try:
        print("✅ Successfully connected to Telegram API!")
        print(f"🤖 Bot is now running with Channel ID: {CHANNEL_ID}")
        print("📢 Monitoring private channel for SMS patterns...")
        print("🔍 Monitoring for ALL new messages...")
        print("📤 Displaying ALL new messages with full SMS and copy buttons...")
        print("📝 Channel logs will be written to:", CHANNEL_LOG_FILE)
        print("📝 SMS logs will be written to:", LOG_FILE)
        print("📝 Monitor logs will be written to:", MONITOR_LOG_FILE)
        print("🛑 Use /stop to remove EVERYTHING (connection + monitoring)")
        
        load_user_connection()
        if get_user_connection():
            print("✅ Firebase connection loaded from file")
        else:
            print("ℹ️ No saved Firebase connection found")
        
        asyncio.create_task(monitor_loop())
        print("🔍 Message Monitor started! Checking every 2 seconds")
        print("🔄 Bot will continuously monitor for ALL new messages")
        
        await bot.polling(non_stop=True, timeout=60)
        
    except Exception as e:
        print(f"❌ Bot error: {e}")
        raise

if __name__ == "__main__":
    print("=" * 60)
    print("🤖 SMS Bot with Message Display is starting...")
    print(f"👤 Admin ID: {ADMIN_ID}")
    print(f"📢 Channel ID: {CHANNEL_ID}")
    print(f"📁 User connection file: {USER_CONNECTION_FILE}")
    print(f"📝 SMS Log file: {LOG_FILE}")
    print(f"📝 Channel Log file: {CHANNEL_LOG_FILE}")
    print(f"📝 Monitor Log file: {MONITOR_LOG_FILE}")
    print("💡 Bot is running...")
    print("🔍 Message Monitoring: Checking every 2 seconds")
    print("📤 Displaying ALL new messages with copy buttons")
    print("🛑 Use /stop to remove EVERYTHING (connection + monitoring)")
    print("=" * 60)
    print("\n📚 How to use:")
    print("1. Admin: /addfirebase (ONE-TIME - Add your Firebase URL and Key)")
    print("2. Admin: /forcechannel")
    print("3. Enter Client ID")
    print("4. Select SIM")
    print("5. Post in channel: phone | message")
    print("6. Bot sends SMS and starts monitoring")
    print("7. Each new message is displayed with full SMS!")
    print("8. Click copy buttons to copy OTP or full SMS")
    print("9. Use /stop to remove EVERYTHING")
    print("=" * 60)
    
    async def main_with_retry():
        max_retries = 5
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                await start_bot()
                break
            except Exception as e:
                print(f"❌ Connection attempt {attempt + 1} failed: {e}")
                
                if attempt < max_retries - 1:
                    print(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    print("❌ All connection attempts failed.")
                    raise
    
    try:
        asyncio.run(main_with_retry())
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        print("Press Enter to exit...")
        input()