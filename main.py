import os
import json
import time
from datetime import datetime
import requests
import feedparser
from newspaper import Article
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ==========================================
# SETUP & AUTHENTICATION
# ==========================================
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
gemini_model = genai.GenerativeModel('gemini-3.5-flash')

creds_dict = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
scopes = ['https://www.googleapis.com/auth/blogger', 'https://www.googleapis.com/auth/spreadsheets']
google_creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_published_urls():
    service = build('sheets', 'v4', credentials=google_creds)
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=os.environ["SPREADSHEET_ID"], range="Database!B:B"
        ).execute()
        return [row[0] for row in result.get('values', []) if row]
    except Exception:
        return []

def log_to_sheets(sheet_name, row_data):
    service = build('sheets', 'v4', credentials=google_creds)
    service.spreadsheets().values().append(
        spreadsheetId=os.environ["SPREADSHEET_ID"],
        range=f"{sheet_name}!A:C",
        valueInputOption="USER_ENTERED",
        body={'values': [row_data]}
    ).execute()

def get_unsplash_image(query):
    try:
        url = f"https://api.unsplash.com/search/photos?query={query}&client_id={os.environ['UNSPLASH_API_KEY']}&per_page=1"
        res = requests.get(url).json()
        if res.get('results'):
            return res['results'][0]['urls']['regular'], res['results'][0]['user']['name']
    except Exception:
        pass
    return None, None

def publish_to_blogger(title, html_content, labels):
    service = build('blogger', 'v3', credentials=google_creds)
    body = {"title": title, "content": html_content, "labels": labels}
    result = service.posts().insert(blogId=os.environ["BLOG_ID"], body=body, isDraft=False).execute()
    return result.get("url")

# ==========================================
# CORE LOGIC 
# ==========================================
def process_news():
    print("Starting AI Bot Run...")
    # Add as many RSS feeds here as you want!
    rss_feeds = [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.ign.com/ign/news"
    ]
    
    published_urls = get_published_urls()
    
    for feed_url in rss_feeds:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:5]: 
            article_url = entry.link
            
            if article_url in published_urls:
                continue 
                
            print(f"Found new story: {entry.title}")
            
            try:
                article = Article(article_url)
                article.download()
                article.parse()
                full_text = article.text
                
                if len(full_text) < 500:
                    continue 
                
                eval_prompt = f"Analyze this text: {full_text[:2000]}. Is it highly newsworthy and factual? Reply EXACTLY with APPROVE or SKIP."
                if "SKIP" in gemini_model.generate_content(eval_prompt).text.strip().upper():
                    log_to_sheets("Logs", [str(datetime.now()), "Skipped", entry.title])
                    published_urls.append(article_url)
                    continue
                    
                print("Story approved! Generating article...")
                write_prompt = f"""
                Write a 700+ word SEO-optimized news article based on this text: {full_text[:3000]}
                Format strictly as valid HTML (no markdown blocks).
                Include: An engaging <h1> title tag, Introduction, <h2> sections, a <ul> Key Takeaways list, and a Conclusion.
                The very last line MUST be a single specific image search keyword wrapped in brackets like this: [cybersecurity]
                """
                
                article_html = gemini_model.generate_content(write_prompt).text.strip()
                if article_html.startswith("```html"):
                    article_html = article_html[7:-3]
                    
                title = entry.title 
                image_keyword = "technology"
                
                if "<h1>" in article_html:
                    title = article_html.split("<h1>")[1].split("</h1>")[0]
                    article_html = article_html.replace(f"<h1>{title}</h1>", "") 
                
                if "[" in article_html and "]" in article_html:
                    image_keyword = article_html.split("[")[-1].split("]")[0]
                    article_html = article_html[:article_html.rfind("[")] 
                
                img_url, img_author = get_unsplash_image(image_keyword)
                if img_url:
                    img_tag = f'<img src="{img_url}" alt="{image_keyword}" style="max-width:100%; border-radius:8px;"><p><i>Image by {img_author} via Unsplash</i></p>'
                    article_html = img_tag + article_html
                
                live_url = publish_to_blogger(title, article_html, ["News", "Trending"])
                
                log_to_sheets("Database", [title, article_url, str(datetime.now())])
                log_to_sheets("Logs", [str(datetime.now()), "Published", live_url])
                print("Successfully published one article. Run complete.")
                return # Exits after publishing 1 article per 30-min run
                
            except Exception as e:
                print(f"Error processing {article_url}: {e}")
                log_to_sheets("Logs", [str(datetime.now()), "Error", str(e)])
                continue

if __name__ == "__main__":
    process_news()
