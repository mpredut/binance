
import time

from pushbullet import Pushbullet
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

####MYLIB
from apikeys import PUSHBULLET_API_KEY, SMTP_USERNAME, SMTP_PASSWORD, TO_EMAIL
pb = Pushbullet(PUSHBULLET_API_KEY)

# Configurare SMTP
SMTP_SERVER = "smtp.googl.com"  # Exemplu: smtp.gmail.com
SMTP_PORT = 587  # Portul pentru TLS

last_alert_time = None

def send_push_notification(title, message):
    pb.push_note(title, message)

def send_email(subject, body, to_email):
    """Trimite un email."""
    msg = MIMEMultipart()
    msg['From'] = SMTP_USERNAME
    msg['To'] = to_email
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    server.starttls()
    server.login(SMTP_USERNAME, SMTP_PASSWORD)
    text = msg.as_string()
    server.sendmail(SMTP_USERNAME, to_email, text)
    server.quit()

def check_alert(condition, message, alert_interval=60):

    global last_alert_time
    current_time = time.time()

    if condition:
        if last_alert_time is None or (current_time - last_alert_time) >= alert_interval:
            #timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))
            timestamp = time.strftime('%H:%M:%S', time.localtime(current_time))
            message_with_time = f"{message} at {timestamp}"
            
            # Trimite notificare push pe Android
            send_push_notification("Alertă Trading", message_with_time)
            
            #send_email(
            #    subject="Alertă Trading",
            #    body=message_with_time,
            #    to_email=TO_EMAIL
            #)
            
            # Actualizează timpul ultimei alerte
            last_alert_time = current_time

