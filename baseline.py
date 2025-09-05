import telebot
from telebot import types
import requests
from bs4 import BeautifulSoup
import json
import os
import random
import re
import nltk
from nltk.stem import WordNetLemmatizer

nltk.download('wordnet')
nltk.download('omw-1.4')

with open('Token.txt', 'r') as file:
    TOKEN = file.read().strip()

bot = telebot.TeleBot(TOKEN)

lemmatizer = WordNetLemmatizer()

LEARNED_FILE = 'learned_words.json'
LEARNING_FILE = 'words_in_learning.json'

# Инициализация файлов
for file in [LEARNED_FILE, LEARNING_FILE]:
    if not os.path.exists(file):
        with open(file, 'w', encoding='utf-8') as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

def load_json(file):
    if not os.path.exists(file) or os.path.getsize(file) == 0:
        return {}
    with open(file, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_json(file, data):
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clean_and_lemmatize(text):
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    return [lemmatizer.lemmatize(w) for w in words]

def get_new_words(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        return None, str(e)
    
    soup = BeautifulSoup(r.text, 'html.parser')
    text = soup.get_text(separator=' ')
    words = clean_and_lemmatize(text)
    
    learned = load_json(LEARNED_FILE)
    learning = load_json(LEARNING_FILE)
    
    new_words = []
    seen = set(list(learned.keys()) + list(learning.keys()))
    for w in words:
        if w not in seen:
            new_words.append(w)
            seen.add(w)
        if len(new_words) >= 50:
            break
    return new_words, None

def create_translation_prompt(words):
    prompt = (
        "You are translating a list of English words into Russian. Keep in mind that most of the words are related to engineering, technical, or scientific fields: programming, physics, mathematics, IT, and related areas. Follow these rules:\n"
        "1. Response format: a JSON dictionary in the form {\"word\": \"translation\"}.\n"
        "2. Exclude words that are different forms of the same root — all forms of one root should have a single translation.\n"
        "3. Exclude all non-English words.\n"
        "4. Mark meaningless words (e.g., articles, prepositions, single letters, random character strings) as \"nonsense(бессмыслица)\".\n"
        "5. Include only clean words for translation.\n"
        "6. Translate from English to Russian.\n\n"
        f"Below is the list of words to translate (the first 50 new words from the page):\n{words}\n\n"
    )
    return f"```\n{prompt}\n```"

# --- Handlers ---

user_state = {}

@bot.message_handler(commands=['start'])
def start_handler(message):
    chat_id = message.chat.id
    # Очищаем words_in_learning.json
    save_json(LEARNING_FILE, {})
    user_state[chat_id] = {'words': [], 'current_word': None, 'url': None}
    bot.send_message(chat_id, "Привет! Отправь URL страницы, с которой нужно собрать слова.")

@bot.message_handler(commands=['help'])
def help_handler(message):
    bot.send_message(message.chat.id, "/start - начать работу с ботом")

@bot.message_handler(func=lambda m: True)
def url_or_translation_handler(message):
    chat_id = message.chat.id
    state = user_state.get(chat_id, {})
    
    # Если у нас нет слов, считаем, что пользователь прислал URL
    if not state.get('words'):
        url = message.text.strip()
        new_words, error = get_new_words(url)
        if error:
            bot.send_message(chat_id, f"Ошибка при загрузке страницы: {error}")
            return
        if not new_words:
            bot.send_message(chat_id, "Новых слов не найдено.")
            return
        
        user_state[chat_id]['words'] = new_words
        user_state[chat_id]['url'] = url
        prompt = create_translation_prompt(new_words)
        bot.send_message(chat_id, "Вот промпт для перевода (скопируй и вставь в LLM):")
        bot.send_message(chat_id, prompt)
        bot.send_message(chat_id, "После перевода пришли мне JSON со словарём.")
    else:
        # Принимаем JSON со словами
        try:
            translated = json.loads(message.text)
            if not isinstance(translated, dict):
                raise ValueError()
        except:
            bot.send_message(chat_id, "Ошибка: пришлите корректный JSON словарь с переводом.")
            return
        
        # Сохраняем в words_in_learning.json
        learning = load_json(LEARNING_FILE)
        learning.update(translated)
        save_json(LEARNING_FILE, learning)
        
        # Начинаем Quizlet
        user_state[chat_id]['words'] = list(translated.keys())
        random.shuffle(user_state[chat_id]['words'])
        bot.send_message(chat_id, "Начинаем тренировку слов! Нажми 'Next card', чтобы увидеть первое слово.")
        send_next_card(chat_id)

def send_next_card(chat_id):
    state = user_state[chat_id]
    words_list = state['words']
    if not words_list:
        bot.send_message(chat_id, "Поздравляю! Все слова выучены!")
        bot.send_message(chat_id, "Ты можешь прислать ту же ссылку или новую для следующей порции слов.")
        return
    word = words_list[0]
    state['current_word'] = word
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Flip card", callback_data="flip"))
    bot.send_message(chat_id, word, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    state = user_state.get(chat_id)
    if not state or not state.get('current_word'):
        return
    word = state['current_word']
    
    if call.data == "flip":
        # Показываем перевод и кнопки Learned / Next card
        learning = load_json(LEARNING_FILE)
        translation = learning.get(word, "(перевод отсутствует)")
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("Learned", callback_data="learned"),
            types.InlineKeyboardButton("Next card", callback_data="next")
        )
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id,
                              text=f"{word} — {translation}", reply_markup=markup)
    elif call.data == "next":
        # Переходим к следующему слову
        words_list = state['words']
        if words_list:
            words_list.append(words_list.pop(0))  # сдвигаем текущее слово в конец
            send_next_card(chat_id)
    elif call.data == "learned":
        # Перемещаем слово в learned_words.json и удаляем из words_in_learning.json
        words_list = state['words']
        learned = load_json(LEARNED_FILE)
        learning = load_json(LEARNING_FILE)
        learned[word] = learning[word]
        save_json(LEARNED_FILE, learned)
        del learning[word]
        save_json(LEARNING_FILE, learning)
        words_list.pop(0)
        send_next_card(chat_id)

bot.infinity_polling()
