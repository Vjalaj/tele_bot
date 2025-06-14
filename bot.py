import logging
import os
import json

# Import telegram modules
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler, CallbackQueryHandler
from PIL import Image
import pytesseract
from openai import OpenAI
from groq import Groq
import google.generativeai as genai
from dotenv import load_dotenv
import filetype
import asyncio

# === Load environment variables from .env ===
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize AI clients
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Store user preferences and pending questions (in production, use a database)
user_preferences = {}
pending_questions = {}

# Available AI models
AI_MODELS = {
    "openai_gpt35": {
        "name": "OpenAI GPT-3.5 Turbo",
        "client": "openai",
        "model": "gpt-3.5-turbo",
        "free": False,
        "description": "Fast and reliable, good for general questions"
    },
    "openai_gpt4": {
        "name": "OpenAI GPT-4",
        "client": "openai", 
        "model": "gpt-4",
        "free": False,
        "description": "Most capable, best for complex problems"
    },
    "groq_llama3": {
        "name": "Groq Llama 3 8B",
        "client": "groq",
        "model": "llama3-8b-8192",
        "free": True,
        "description": "Fast and free, great for most questions"
    },
    "groq_mixtral": {
        "name": "Groq Mixtral 8x7B",
        "client": "groq",
        "model": "mixtral-8x7b-32768",
        "free": True,
        "description": "Free with large context window"
    },
    "gemini": {
        "name": "Google Gemini 1.5 Flash",
        "client": "gemini",
        "model": "gemini-1.5-flash",
        "free": True,
        "description": "Free Google AI model, good performance"
    }
}

# Optional: Windows users may need to specify the Tesseract path
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command"""
    welcome_text = """
🤖 **AI Question Answering Bot**

Send me a photo of a question and I'll help you solve it!

📋 **How it works:**
1. Send a photo of your question
2. Choose which AI model to use
3. Get your answer!

💡 **Commands:**
/help - Show this help

📷 **Just send a photo to get started!**
    """
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /help command"""
    help_text = """
🤖 **How to use this bot:**

1. Send a photo of your question
2. Choose which AI model to use from the menu
3. Get your answer instantly!

� **Tips for better results:**
- Use clear, well-lit photos
- Make sure text is readable and not blurry
- Avoid shadows or glare on the text
- Hold camera steady when taking photo

🔄 **You can try different AI models for different types of questions!**
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def show_model_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, question_text: str, edit_message: bool = False):
    """Show available AI models for selection"""
    user_id = update.effective_user.id
    
    # Store the question for this user
    pending_questions[user_id] = question_text
    
    keyboard = []
    for model_id, model_info in AI_MODELS.items():
        # Check if the required API key is available
        available = True
        if model_info["client"] == "openai" and not OPENAI_API_KEY:
            available = False
        elif model_info["client"] == "groq" and not GROQ_API_KEY:
            available = False
        elif model_info["client"] == "gemini" and not GEMINI_API_KEY:
            available = False
        
        if available:
            free_badge = " 🆓" if model_info["free"] else " 💰"
            button_text = f"{model_info['name']}{free_badge}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"solve_{model_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🤖 **Choose an AI model to solve your question:**\n\n📝 Question preview:\n" + question_text[:150] + ("..." if len(question_text) > 150 else "")
    
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle model selection callback"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if query.data.startswith("solve_"):
        model_id = query.data.replace("solve_", "")
        
        # Get the pending question for this user
        question_text = pending_questions.get(user_id)
        if not question_text:
            await query.edit_message_text("❌ Error: No question found. Please send a new photo.")
            return
        
        if model_id in AI_MODELS:
            model_info = AI_MODELS[model_id]
            
            # Show processing message
            await query.edit_message_text(f"🤖 Processing with {model_info['name']}...\n\nPlease wait...")
            
            try:
                answer = await get_ai_response(question_text, model_id)
                
                # Send the answer in a new message
                answer_text = f"✅ **Answer from {model_info['name']}:**\n\n{answer}"
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=answer_text,
                    parse_mode='Markdown'
                )
                
                # Clear the pending question
                if user_id in pending_questions:
                    del pending_questions[user_id]
                
                # Update the selection message
                await query.edit_message_text(f"✅ Answer provided using {model_info['name']}!")
                
            except Exception as e:
                logger.error(f"Error getting AI response: {e}")
                error_msg = f"❌ Error with {model_info['name']}: {str(e)}\n\nTry a different model:"
                
                # Show model selection again with error message
                await show_model_selection_with_error(query, question_text, error_msg)
    
    else:
        # Handle old model selection format (if any)
        await query.edit_message_text("Please send a new photo to get started!")

async def get_ai_response(question_text: str, model_id: str) -> str:
    """Get response from the selected AI model"""
    model_info = AI_MODELS[model_id]
    
    try:
        if model_info["client"] == "openai":
            response = openai_client.chat.completions.create(
                model=model_info["model"],
                messages=[{"role": "user", "content": question_text}],
                max_tokens=1000
            )
            return response.choices[0].message.content.strip()
            
        elif model_info["client"] == "groq":
            response = groq_client.chat.completions.create(
                model=model_info["model"],
                messages=[{"role": "user", "content": question_text}],
                max_tokens=1000
            )
            return response.choices[0].message.content.strip()
            
        elif model_info["client"] == "gemini":
            model = genai.GenerativeModel(model_info["model"])
            response = model.generate_content(question_text)
            return response.text
            
    except Exception as e:
        raise Exception(f"AI Error: {str(e)}")
    
    return "Error: Unsupported model"

# Update handler function to use async/await pattern
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user_id = update.effective_user.id

    # Download the highest quality image
    photo = message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_path = f"question_{user_id}.jpg"
    await file.download_to_drive(image_path)

    await message.reply_text("🔍 Extracting text from image...")

    try:
        # Try enhanced OCR first
        question_text = preprocess_image_for_ocr(image_path)
        
        # If enhanced OCR fails or returns empty, try basic OCR
        if not question_text.strip():
            image = Image.open(image_path)
            question_text = pytesseract.image_to_string(image, config='--psm 6')

        # Clean up the image file
        try:
            os.remove(image_path)
        except:
            pass

        if not question_text.strip():
            await message.reply_text("❌ Couldn't extract any text from the image.\n\n💡 Tips:\n- Use a clear, well-lit photo\n- Make sure text is readable\n- Avoid shadows or glare")
            return

        await message.reply_text("✅ Text extracted successfully!\n\nNow choose an AI model to solve your question:")
        
        # Show model selection
        await show_model_selection(update, context, question_text)

    except Exception as e:
        logger.error(f"Error processing image: {e}")
        await message.reply_text(f"❌ Error processing image: {str(e)}")
        # Clean up the image file in case of error
        try:
            os.remove(image_path)
        except:
            pass

async def show_model_selection_with_error(query, question_text: str, error_msg: str):
    """Show model selection with error message"""
    user_id = query.from_user.id
    
    keyboard = []
    for model_id, model_info in AI_MODELS.items():
        # Check if the required API key is available
        available = True
        if model_info["client"] == "openai" and not OPENAI_API_KEY:
            available = False
        elif model_info["client"] == "groq" and not GROQ_API_KEY:
            available = False
        elif model_info["client"] == "gemini" and not GEMINI_API_KEY:
            available = False
        
        if available:
            free_badge = " 🆓" if model_info["free"] else " 💰"
            button_text = f"{model_info['name']}{free_badge}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"solve_{model_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"{error_msg}\n\n🤖 **Choose a different AI model:**"
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

def preprocess_image_for_ocr(image_path: str) -> str:
    """Enhanced OCR preprocessing for better text extraction"""
    import cv2
    import numpy as np
    
    # Read image
    img = cv2.imread(image_path)
    
    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Apply multiple preprocessing techniques
    
    # 1. Resize image (make it larger for better OCR)
    height, width = gray.shape
    scale_factor = 2.0
    new_width = int(width * scale_factor)
    new_height = int(height * scale_factor)
    resized = cv2.resize(gray, (new_width, new_height), interpolation=cv2.INTER_CUBIC)
    
    # 2. Denoise
    denoised = cv2.fastNlMeansDenoising(resized)
    
    # 3. Apply threshold to get better contrast
    # Try multiple threshold methods and combine results
    thresh1 = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    thresh2 = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
    
    # Save preprocessed images temporarily
    cv2.imwrite("temp_thresh1.jpg", thresh1)
    cv2.imwrite("temp_thresh2.jpg", thresh2)
    cv2.imwrite("temp_resized.jpg", resized)
    
    # Try OCR on different processed versions
    results = []
    
    # OCR on original resized
    try:
        text1 = pytesseract.image_to_string(resized, config='--psm 6')
        results.append(text1)
    except:
        pass
    
    # OCR on threshold 1
    try:
        text2 = pytesseract.image_to_string(thresh1, config='--psm 6')
        results.append(text2)
    except:
        pass
    
    # OCR on threshold 2
    try:
        text3 = pytesseract.image_to_string(thresh2, config='--psm 6')
        results.append(text3)
    except:
        pass
    
    # Clean up temp files
    for temp_file in ["temp_thresh1.jpg", "temp_thresh2.jpg", "temp_resized.jpg"]:
        try:
            os.remove(temp_file)
        except:
            pass
    
    # Return the longest result (usually more complete)
    if results:
        return max(results, key=len).strip()
    else:
        return ""

def main() -> None:
    # Create the Application and pass it your bot's token
    application = Application.builder().token(TELEGRAM_TOKEN).job_queue(None).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(model_callback, pattern="^solve_"))

    # Add handler for photos
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))

    # Run the bot until the user presses Ctrl-C
    print("🤖 Bot is running... Send a photo of a question or use /start")
    application.run_polling()

if __name__ == "__main__":
    main()