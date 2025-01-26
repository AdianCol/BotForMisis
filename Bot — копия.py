import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from datetime import datetime
import os
import psycopg2  # Import PostgreSQL library

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Connect to PostgreSQL database
def connect_db():
    try:
        conn = psycopg2.connect(
            dbname=os.environ.get("PGDATABASE"),
            user=os.environ.get("PGUSER"),
            password=os.environ.get("PGPASSWORD"),
            host=os.environ.get("PGHOST"),
            port=os.environ.get("PGPORT")
        )
        return conn
    except Exception as e:
        logger.error("Error connecting to the database: %s", e)
        return None

conn = connect_db()
cursor = conn.cursor()
cursor.execute("SET search_path TO your_schema;")  # Set the schema if needed

# Create tables for users and notes
def create_tables():
    try:
        cursor.executescript(''' 
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT
        );
        CREATE TABLE IF NOT EXISTS notes (
            note_id SERIAL PRIMARY KEY,
            user_id INTEGER,
            text TEXT,
            media_type TEXT,
            media_url TEXT,
            date TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        ''')
        conn.commit()
    except psycopg2.Error as e:
        logger.error("Error creating tables: %s", e)

# Check if a note exists
def note_exists(note_id, user_id):
    cursor.execute('SELECT 1 FROM notes WHERE note_id = %s AND user_id = %s', (note_id, user_id))
    return cursor.fetchone() is not None

# Command /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    username = update.message.from_user.username

    # Add user to the database if not already present
    cursor.execute('INSERT INTO users (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING', (user_id, username))
    conn.commit()

    keyboard = [
    [InlineKeyboardButton("Добавить заметку", callback_data='add')],
    [InlineKeyboardButton("Редактировать заметку", callback_data='edit')],
    [InlineKeyboardButton("Удалить заметку", callback_data='delete')],
    [InlineKeyboardButton("Список заметок", callback_data='list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Привет! Я бот для ведения заметок. Выберите действие:', reply_markup=reply_markup)

# Handle button presses
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == 'add':
        await query.message.reply_text('Введите текст заметки или отправьте голосовое сообщение/фото:')
        context.user_data['action'] = 'add'
    elif query.data == 'edit':
        await query.message.reply_text('Введите номер заметки и новый текст, разделенные пробелом:')
        context.user_data['action'] = 'edit'
    elif query.data == 'delete':
        await query.message.reply_text('Введите номер заметки для удаления:')
        context.user_data['action'] = 'delete'
    elif query.data == 'list':
        await list_notes(update, context)

# Handle text messages, voice messages, and photos
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    action = context.user_data.get('action')

    try:
        if action == 'add':
            # Ensure user exists before adding a note
            cursor.execute('SELECT 1 FROM users WHERE user_id = %s', (user_id,))
            if cursor.fetchone() is None:
                await update.message.reply_text('Пользователь не найден. Пожалуйста, начните с команды /start.')
                return

            if update.message.text:  # Handle text notes
                text = update.message.text
                cursor.execute('INSERT INTO notes (user_id, text, media_type, media_url, date) VALUES (%s, %s, %s, %s, %s)', 
                               (user_id, text, 'text', None, datetime.now()))
                conn.commit()
                await update.message.reply_text('Заметка добавлена.')
            elif update.message.voice:  # Handle voice messages
                voice_file_id = update.message.voice.file_id
                cursor.execute('INSERT INTO notes (user_id, text, media_type, media_url, date) VALUES (%s, %s, %s, %s, %s)', 
                               (user_id, None, 'voice', voice_file_id, datetime.now()))
                conn.commit()
                await update.message.reply_text('Голосовая заметка добавлена.')
            elif update.message.photo:  # Handle photos
                photo_file_id = update.message.photo[-1].file_id  # Get the highest resolution photo
                cursor.execute('INSERT INTO notes (user_id, text, media_type, media_url, date) VALUES (%s, %s, %s, %s, %s)', 
                               (user_id, None, 'photo', photo_file_id, datetime.now()))
                conn.commit()
                await update.message.reply_text('Фотозаметка добавлена.')
            await button_handler(update, context)  # Show buttons after action
        elif action == 'edit':
            note_id, new_text = update.message.text.split(maxsplit=1)
            note_id = int(note_id)
            if note_exists(note_id, user_id):
                cursor.execute('UPDATE notes SET text = %s WHERE note_id = %s AND user_id = %s', 
                               (new_text, note_id, user_id))
                conn.commit()
                await update.message.reply_text(f'Заметка {note_id} обновлена.')
            else:
                await update.message.reply_text(f'Заметка {note_id} не найдена.')
            await button_handler(update, context)  # Show buttons after action
        elif action == 'delete':
            note_id = int(update.message.text)
            if note_exists(note_id, user_id):
                cursor.execute('DELETE FROM notes WHERE note_id = %s AND user_id = %s', (note_id, user_id))
                conn.commit()
                await update.message.reply_text(f'Заметка {note_id} удалена.')
            else:
                await update.message.reply_text(f'Заметка {note_id} не найдена.')
            await button_handler(update, context)  # Show buttons after action
    except psycopg2.Error as e:
        logger.error("Ошибка при обработке текста: %s", e)
        await update.message.reply_text('Ошибка при обработке запроса.')

# Command to display the list of notes
async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        cursor.execute('SELECT note_id, text, media_type, media_url, date FROM notes WHERE user_id = %s', (user_id,))
        notes = cursor.fetchall()

        if not notes:
            await query.message.reply_text('Нет доступных заметок.')
            return

        message_text = 'Список заметок:\n'
        for index, note in enumerate(notes, start=1):  # User-specific numbering
            message_text += f"{index}: {note[1] if note[1] else 'Media'} (Создано: {note[4]})\n"

        await query.message.reply_text(message_text)

        # Send media files separately in the order they were added
        for note in notes:
            if note[2] == 'voice':
                await context.bot.send_voice(chat_id=user_id, voice=note[3])  # Send voice message
            elif note[2] == 'photo':
                await context.bot.send_photo(chat_id=user_id, photo=note[3])  # Send photo

    except psycopg2.Error as e:
        logger.error(f"Ошибка при получении списка заметок: {e}")
        await query.message.reply_text('Ошибка при получении списка заметок.')

# Main function
def main() -> None:
    create_tables()  # Create tables on startup

    application = ApplicationBuilder().token(os.environ.get("7403578084:AAHo7TNmbJ_ACyrgpfBPvg6ugH8qcwWjWqY")).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.VOICE, text_handler))  # Handle voice messages
    application.add_handler(MessageHandler(filters.PHOTO, text_handler))  # Handle photos

if __name__ == '__main__':
    main()
