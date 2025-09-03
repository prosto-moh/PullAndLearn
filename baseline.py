import telebot
from telebot import types
import requests
from bs4 import BeautifulSoup
import json
import random
import string
import os

# Чтение токена из файла
with open('Token.txt', 'r') as f:
    TOKEN = f.read().strip()

bot = telebot.TeleBot(TOKEN)

LEARNED_FILE = 'learned_words.json'
IN_LEARNING_FILE = 'words_in_learning.json'

# Словарь сессий пользователей
sessions = {}

for file in ['learned_words.json', 'words_in_learning.json']:
    if not os.path.exists(file):
        with open(file, 'w', encoding='utf-8') as f:
            f.write('{}')


# =================== Работа с JSON ===================

def load_json(file):
    """
    Безопасно загружает JSON из файла. 
    Если файл пустой, не существует или некорректный, возвращает пустой словарь.
    """
    if not os.path.exists(file):
        return {}
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read().strip()
        if not content:
            return {}
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

def save_json(file, data):
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =================== Извлечение слов со страницы ===================
def extract_words_from_url(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
    except Exception as e:
        return None, str(e)

    soup = BeautifulSoup(response.text, 'html.parser')
    # убираем скрипты, стили и блоки навигации
    for tag in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
        tag.decompose()

    text = soup.get_text(separator=' ')
    words = text.lower().split()
    cleaned_words = []
    for word in words:
        word = word.strip(string.punctuation)
        if word.isalpha():
            cleaned_words.append(word)
    return cleaned_words, None

# =================== Генерация промтов ===================
def generate_translation_prompts(new_words, words_per_chunk=50):
    """
    Разбивает слова на промты с максимальным количеством слов words_per_chunk,
    чтобы каждый промт был точно меньше лимита Telegram.
    """
    prompts = []
    base_header = """```
Ты выступаешь в роли переводчика английских слов на русский язык.  
Твоя задача — вернуть чистый JSON в формате: {"english_word": "русский перевод"}  

### Правила:
1. Переводи только английские слова.  
2. Не добавляй слова, не являющиеся английскими (цифры, символы, латинизированные имена и пр.).  
3. Исключи служебные и бессмысленные слова (артикли, предлоги, союзы, местоимения: a, an, the, in, on, of, to, and, etc.).  
4. Исключи однокоренные слова — оставь только одну форму.  
5. Не используй дублирующиеся слова.  
6. Переводи слова максимально кратко и точно (одно-два слова на русском), ориентируясь на технический контекст статьи или документации.

### Список слов для перевода:
"""
    base_footer = "\n### Количество слов: {count}\n```"

    # делим новые слова на чанки
    for i in range(0, len(new_words), words_per_chunk):
        chunk = new_words[i:i+words_per_chunk]
        words_text = ' '.join(chunk)
        prompts.append(base_header + words_text + base_footer.format(count=len(chunk)))

    return prompts

# =================== /start ===================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    sessions[user_id] = {'current_words': [], 'review_words': [], 'current_index': 0, 'learned': []}
    save_json(IN_LEARNING_FILE, {})
    bot.send_message(message.chat.id, "Привет! Отправь мне URL страницы, с которой нужно собрать слова для изучения:")

# =================== Обработка URL ===================
@bot.message_handler(func=lambda message: message.text and message.text.startswith('http'))
def handle_url(message):
    user_id = message.from_user.id
    url = message.text
    bot.send_message(message.chat.id, "Собираю текст со страницы...")
    words, error = extract_words_from_url(url)
    if error:
        bot.send_message(message.chat.id, f"Ошибка при загрузке страницы: {error}")
        return

    learned_words = load_json(LEARNED_FILE)
    unique_words = list(set(words))
    new_words = [w for w in unique_words if w not in learned_words]

    if not new_words:
        bot.send_message(message.chat.id, "Нет новых слов для перевода.")
        return

prompts = generate_translation_prompts(new_words, words_per_chunk=50)
for i, prompt in enumerate(prompts, 1):
    bot.send_message(message.chat.id, f"Промт {i}/{len(prompts)}:\n{prompt}")


# =================== Получение переведённого JSON ===================
@bot.message_handler(func=lambda message: message.text.startswith('{') and message.text.endswith('}'))
def handle_translated_json(message):
    user_id = message.from_user.id
    try:
        translated = json.loads(message.text)
    except json.JSONDecodeError:
        bot.send_message(message.chat.id, "Ошибка: не удалось распознать JSON.")
        return

    if not translated:
        bot.send_message(message.chat.id, "Пустой словарь перевода.")
        return


    # Разбиваем на чанки по 50 слов
    chunk_size = 50
    keys = list(translated.keys())
    for i in range(0, len(keys), chunk_size):
        chunk_keys = keys[i:i+chunk_size]
        chunk_dict = {k: translated[k] for k in chunk_keys}

        # обновляем words_in_learning.json
        words_in_learning = load_json(IN_LEARNING_FILE)
        words_in_learning.update(chunk_dict)
        save_json(IN_LEARNING_FILE, words_in_learning)

    bot.send_message(message.chat.id, f"Слова добавлены в изучение: {len(translated)} слов.")
    start_review(message)


# =================== Начало обзора ===================
def start_review(message):
    user_id = message.from_user.id
    words_in_learning = load_json(IN_LEARNING_FILE)
    if not words_in_learning:
        bot.send_message(message.chat.id, "Слов для изучения нет. Отправь новый URL.")
        return

    words = list(words_in_learning.items())
    random.shuffle(words)
    sessions[user_id] = {
        'current_words': words[:100],
        'current_index': 0,
        'learned': []
    }
    send_next_card(message)

# =================== Отправка карточки ===================
def send_next_card(message):
    user_id = message.from_user.id
    session = sessions.get(user_id)
    if not session:
        return

    index = session['current_index']
    if index >= len(session['current_words']):
        finish_session(message)
        return

    word, translation = session['current_words'][index]
    markup = types.InlineKeyboardMarkup()
    flip_button = types.InlineKeyboardButton("Flip card", callback_data="flip")
    markup.add(flip_button)
    bot.send_message(message.chat.id, f"Слово: {word}", reply_markup=markup)

# =================== Обработка кнопок ===================
@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    user_id = call.from_user.id
    session = sessions.get(user_id)
    if not session:
        return

    index = session['current_index']
    if index >= len(session['current_words']):
        return

    word, translation = session['current_words'][index]

    if call.data == "flip":
        markup = types.InlineKeyboardMarkup()
        learned_btn = types.InlineKeyboardButton("Learned", callback_data="learned")
        next_btn = types.InlineKeyboardButton("Next card", callback_data="next")
        markup.add(learned_btn, next_btn)
        bot.edit_message_text(chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              text=f"{word} → {translation}",
                              reply_markup=markup)
    elif call.data == "learned":
        session['learned'].append(word)
        session['current_index'] += 1
        send_next_card(call.message)
    elif call.data == "next":
        session['current_index'] += 1
        send_next_card(call.message)

# =================== Завершение сессии ===================
def finish_session(message):
    user_id = message.from_user.id
    session = sessions.get(user_id)
    if not session:
        return

    learned_words = load_json(LEARNED_FILE)
    words_in_learning = load_json(IN_LEARNING_FILE)

    for word in session['learned']:
        learned_words[word] = words_in_learning[word]
        del words_in_learning[word]

    save_json(LEARNED_FILE, learned_words)
    save_json(IN_LEARNING_FILE, words_in_learning)
    bot.send_message(message.chat.id, f"Сессия завершена. Выучено {len(session['learned'])} слов.")

    sessions[user_id] = {'current_words': [], 'review_words': [], 'current_index': 0, 'learned': []}

# =================== Запуск бота ===================
bot.infinity_polling()
