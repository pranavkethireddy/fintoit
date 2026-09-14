import requests
import os

def send_sms_verification(to_phone: str, code: str) -> bool:
    try:
        response = requests.post(
            "https://api.quo.com/v1/messages",
            headers={
                "Authorization": os.getenv("QUO_API_KEY"),
                "Content-Type": "application/json"
            },
            json={
                "content": f"Your Fintoit verification code is: {code}. Expires in 10 minutes.",
                "from": os.getenv("QUO_FROM_NUMBER"),
                "to": [to_phone],
                "userId": os.getenv("QUO_USER_ID")
            }
        )
        return response.status_code == 202
    except Exception as e:
        print(f"[SMS ERROR] {e}")
        return False