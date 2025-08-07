# -*- coding: utf-8 -*-
import os
import sys
import requests
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, PushMessageRequest
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent

app = Flask(__name__)

# 從環境變數獲取金鑰
channel_secret = os.getenv('LINE_CHANNEL_SECRET', None)
channel_access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', None)
dify_api_key = os.getenv('DIFY_API_KEY', None)
dify_api_url = os.getenv('DIFY_API_URL', None)

# 確保所有金鑰都存在
if not all([channel_secret, channel_access_token, dify_api_key, dify_api_url]):
    # 在伺服器日誌中打印錯誤，但不要讓應用崩潰
    print('錯誤：一個或多個必要的環境變數缺失！')
    # 可以選擇性地退出，但在 serverless 環境中，讓它保持運行可能更好
    # sys.exit(1)

handler = WebhookHandler(channel_secret)
configuration = Configuration(access_token=channel_access_token)
api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    user_id = event.source.user_id
    # 1. 立刻回覆，避免 LINE 超時
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text='圖面已收到，專家系統分析中，請稍候約30秒...')]
        )
    )

    # 2. 下載圖片
    message_content = line_bot_api.get_message_content(message_id=event.message.id)

    # 3. 呼叫 Dify
    dify_response_text = call_dify_api(user_id, message_content)
    
    # 4. 推送 Dify 結果
    if dify_response_text:
        # LINE 訊息長度限制為 5000 字，我們需要分割訊息
        for i in range(0, len(dify_response_text), 4800):
            chunk = dify_response_text[i:i+4800]
            line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=chunk)]))

def call_dify_api(user_id, image_bytes):
    headers = {'Authorization': f'Bearer {dify_api_key}'}
    files = {'pipe_drawing_image': ('image.jpeg', image_bytes, 'image/jpeg')}
    data = {'inputs': '{}', 'response_mode': 'blocking', 'user': user_id}
    
    try:
        # 使用 streaming=True, timeout 增加
        response = requests.post(dify_api_url, headers=headers, files=files, data=data, timeout=300)
        response.raise_for_status()
        # 直接獲取 Dify blocking mode 的回覆
        full_answer = response.json().get('answer', '')
        return full_answer if full_answer else "分析完成，但未收到有效回覆。"
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Dify API Error: {e}")
        return f"Dify API 呼叫失敗: {e}"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    line_bot_api.reply_message(
        ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text='您好，請直接上傳需要分析的管件圖面。')]
        )
    )
