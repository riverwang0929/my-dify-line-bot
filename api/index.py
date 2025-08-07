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
    # 【最终极的修正】
    # Dify API 对 multipart/form-data 的处理有非常特殊的要求。
    # 我们需要先上传文件，获得一个 file_id，然后再将这个 file_id 作为 inputs 发送。
    
    # 步骤 1: 上传文件
    upload_url = "https://api.dify.ai/v1/files/upload"
    upload_headers = {'Authorization': f'Bearer {dify_api_key}'}
    upload_files = {'file': ('image.jpeg', image_data, 'image/jpeg')}
    upload_data = {'user': user_id}

    try:
        upload_response = requests.post(upload_url, headers=upload_headers, files=upload_files, data=upload_data, timeout=60)
        upload_response.raise_for_status()
        upload_result = upload_response.json()
        file_id = upload_result.get('id')
        if not file_id:
            return "Dify 文件上传成功，但未返回 file_id。"
    except Exception as e:
        app.logger.error(f"Dify 文件上传失败: {e} - {upload_response.text if 'upload_response' in locals() else 'No response'}")
        return f"Dify 文件上传失败: {e}"

    # 步骤 2: 发送聊天请求，带上 file_id
    chat_headers = {'Authorization': f'Bearer {dify_api_key}', 'Content-Type': 'application/json'}
    chat_data = {
        "inputs": {},
        "query": "请根据我上传的图片进行分析", # 必须有一个 query
        "response_mode": "blocking",
        "conversation_id": "",
        "user": user_id,
        "files": [
            {
                "type": "image",
                "transfer_method": "remote_url", # 或者 local_file，但这里用 remote_url 指代已上传的文件
                "upload_file_id": file_id
            }
        ]
    }

    try:
        response = requests.post(dify_api_url, headers=chat_headers, json=chat_data, timeout=300)
        response.raise_for_status()
        response_data = response.json()
        full_answer = response_data.get('answer', '')
        return full_answer if full_answer else "分析完成，但未收到有效回覆。"
    except Exception as e:
        app.logger.error(f"Dify API 呼叫失败: {e} - {response.text if 'response' in locals() else 'No response'}")
        return f"Dify API 呼叫失败: {e}"
