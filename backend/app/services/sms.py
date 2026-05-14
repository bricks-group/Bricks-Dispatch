from twilio.rest import Client as TwilioClient
import os

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

def send_sms(to: str, body: str) -> bool:
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
        print("Twilio credentials not fully configured. Skipping SMS send.")
        return False
    
    client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    try:
        message = client.messages.create(body=body, from_=TWILIO_PHONE_NUMBER, to=to)
        return message.status in ('queued', 'sent', 'delivered')
    except Exception as e:
        print(f"Error sending SMS: {e}")
        return False
