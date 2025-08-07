# -*- coding: utf-8 -*-
import os
import sys
import requests
import logging
import json
import hmac
import hashlib
import base64
from flask import Flask, request, abort

# 配置日誌
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# 從環境變數獲取金鑰
channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
dify_api_key = os.getenv('DIFY_API_KEY', None)
dify_api_url = os.getenv('DIFY_API_URL', None)

if not all([channel_secret, channel_access_token, dify_api_key, dify_api_url]):
    logging.error('錯誤：一個或多個必要的環境變數缺失！')
    sys.exit(1)

@app.route("/callback", methods=['POST'])
def callback():
    # 手動驗證簽名
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        hash = hmac.new(channel_secret.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).digest()
        if signature != base64.b64encode(hash).decode('utf-8'):
            abort(400)
    except Exception as e:
        app.logger.error(f"簽名驗證失敗: {e}")
        abort(400)

    # 解析 webhook 事件
    events = request.json.get('events', [])
    for event in events:
        if event['type'] == 'message':
            handle_message(event)

    return 'OK'

def handle_message(event):
    message_type = event['message']['type']
    reply_token = event['replyToken']
    user_id = event['source']['userId']

    if message_type == 'image':
        message_id = event['message']['id']
        # 1. 立刻回覆，避免 LINE 超時
        reply_message(reply_token, '圖面已收到，專家系統分析中，請稍候約30秒...')

        # 2. 手動下載圖片
        image_data = download_line_image(message_id)
        if not image_data:
            push_message(user_id, '無法下載您上傳的圖片，請稍後再試。')
            return

        # 3. 呼叫 Dify
        dify_response_text = call_dify_api(user_id, image_data)
        
        # 4. 推送 Dify 結果
        if dify_response_text:
            for i in range(0, len(dify_response_text), 4800):
                chunk = dify_response_text[i:i+4800]
                push_message(user_id, chunk)
        else:
            push_message(user_id, "分析完成，但 Dify 未提供有效回覆。")

    elif message_type == 'text':
        reply_message(reply_token, '您好，請直接上傳需要分析的管件圖面。')

def reply_message(reply_token, text):
    url = 'https://api.line.me/v2/bot/message/reply'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {channel_access_token}'}
    data = {'replyToken': reply_token, 'messages': [{'type': 'text', 'text': text}]}
    requests.post(url, headers=headers, json=data)

def push_message(user_id, text):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {channel_access_token}'}
    data = {'to': user_id, 'messages': [{'type': 'text', 'text': text}]}
    requests.post(url, headers=headers, json=data)

def download_line_image(message_id):
    url = f'https://api-data.line.me/v2/bot/message/{message_id}/content'
    headers = {'Authorization': f'Bearer {channel_access_token}'}
    try:
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        return response.content
    except requests.exceptions.RequestException as e:
        app.logger.error(f"下載 LINE 圖片失敗: {e}")
        return None

def call_dify_api(user_id, image_data):
    # 【重要修正】移除 Content-Type，讓 requests 自動處理 multipart/form-data 的邊界
    headers = {'Authorization': f'Bearer {dify_api_key}'}
    
    files = {
        'inputs': (None, json.dumps({})), # 將 JSON inputs 作為一個部分
        'response_mode': (None, 'blocking'),
        'user': (None, user_id),
        'conversation_id': (None, ''),
        'pipe_drawing_image': ('image.jpeg', image_data, 'image/jpeg')
    }
    
    try:
        response = requests.post(dify_api_url, headers=headers, files=files, timeout=300)
        response.raise_for_status()
        response_data = response.json()
        full_answer = response_data.get('answer', '')
        return full_answer if full_answer else "分析完成，但未收到有效回覆。"
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Dify API 呼叫失敗: {e} - {response.text if 'response' in locals() else 'No response'}")
        return f"Dify API 呼叫失敗: {e}"
    except json.JSONDecodeError:
        app.logger.error(f"無法解析 Dify 的回覆: {response.text}")
        return f"無法解析 Dify 的回覆。原始回覆: {response.text[:200]}"
