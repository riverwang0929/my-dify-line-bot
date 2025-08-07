# -*- coding: utf-8 -*-
import os
import sys
import requests
import logging
import base64
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, PushMessageRequest
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent

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

handler = WebhookHandler(channel_secret)
configuration = Configuration(access_token=channel_access_token)

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
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        user_id = event.source.user_id

        try:
            # 1. 立刻回覆，避免 LINE 超時
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text='圖面已收到，專家系統分析中，請稍候約30秒...')]
                )
            )
        except Exception as e:
            app.logger.error(f"無法回覆 LINE 訊息: {e}")

        try:
            # 2. 【100% 正確的 V3 版本下載圖片方法】
            message_id = event.message.id
            # 使用 messaging_api_blob 下載
            from linebot.v3.messaging import MessagingApiBlob
            line_bot_blob_api = MessagingApiBlob(api_client)
            response_content = line_bot_blob_api.download_message_content(message_id=message_id)
            # response_content 直接就是圖片的二進制數據
            image_data = response_content

            # 3. 呼叫 Dify
            dify_response_text = call_dify_api(user_id, image_data)
            
            # 4. 推送 Dify 結果
            if dify_response_text:
                for i in range(0, len(dify_response_text), 4800):
                    chunk = dify_response_text[i:i+4800]
                    line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=chunk)]))
            else:
                line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text="分析完成，但 Dify 未提供有效回覆。")]))

        except Exception as e:
            app.logger.error(f"處理圖片或呼叫 Dify 時發生錯誤: {e}")
            line_bot_api.push_message(PushMessageRequest(to=user_id, messages=[TextMessage(text=f"處理您的請求時發生內部錯誤，請稍後再試。")]))

def call_dify_api(user_id, image_data):
    headers = {'Authorization': f'Bearer {dify_api_key}'}
    files = {'pipe_drawing_image': ('image.jpeg', image_data, 'image/jpeg')}
    data = {'inputs': '{}', 'response_mode': 'blocking', 'user': user_id, 'conversation_id': ''}
    
    try:
        response = requests.post(dify_api_url, headers=headers, files=files, data=data, timeout=300)
        response.raise_for_status()
        response_data = response.json()
        full_answer = response_data.get('answer', '')
        return full_answer if full_answer else "分析完成，但未收到有效回覆。"
    except Exception as e:
        app.logger.error(f"Dify API 呼叫失敗: {e}")
        return f"Dify API 呼叫失敗: {e}"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text='您好，請直接上傳需要分析的管件圖面。')]
            )
        )
